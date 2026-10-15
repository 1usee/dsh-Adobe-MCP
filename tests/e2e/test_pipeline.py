"""L1 ↔ L2 全链路测试（用假面板，无需真机宿主）。

被测的是**生产代码**：``CepClient``（真实 WebSocket 往返）、``JsxRunner``（求值锁、
超时、DESYNC、幂等键、WAL）、``BridgeService``、``ToolContext``（风险分级、确认门、
检查点、路径白名单、任务模型）。假面板只替代"宿主内 DOM"这一层。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dsh_adobe_mcp.config import Config
from dsh_adobe_mcp.tools import app as app_tools
from dsh_adobe_mcp.tools import export as export_tools
from dsh_adobe_mcp.tools import host as host_tools
from dsh_adobe_mcp.tools import jobs as job_tools
from dsh_adobe_mcp.tools import project as project_tools
from dsh_adobe_mcp.tools import safety as safety_tools
from dsh_adobe_mcp.tools import timeline as timeline_tools
from fakes.fake_panel import FakePanel


@pytest.fixture
def project_file(tmp_path: Path) -> Path:
    """一个真实存在的 .prproj 文件（检查点是文件级复制，必须真实存在）。"""
    path = tmp_path / "demo.prproj"
    path.write_bytes(b"<Project/>" * 512)
    return path


@pytest.fixture
def project_panel(panel: FakePanel, project_file: Path) -> FakePanel:
    """让假面板报告一个真实存在的工程路径。"""
    info = dict(panel.payloads["project.info"])
    info.update({"path": str(project_file), "name": project_file.stem})
    panel.payloads["project.info"] = info
    state = dict(panel.payloads["host.state"])
    state.update({"project_path": str(project_file)})
    panel.payloads["host.state"] = state
    app_info = dict(panel.payloads["app.info"])
    app_info.update({"project_path": str(project_file), "project_name": project_file.stem})
    panel.payloads["app.info"] = app_info
    return panel


# --------------------------------------------------------------------------
# 宿主发现与连接
# --------------------------------------------------------------------------
async def test_host_list_reports_installed_versions(ctx, fake_registry):
    result = await host_tools.host_list()
    assert result["ok"] is True
    versions = [item["version"] for item in result["data"]["hosts"]]
    assert versions == ["25.0", "24.0"]
    assert result["data"]["hosts"][0]["selected_by_default"] is True


async def test_wait_ready_establishes_session(ctx, bridge, panel, tmp_config: Config):
    result = await host_tools.host_wait_ready(host="premiere:25.0", timeout_s=3)
    assert result["ok"] is True, result["error"]
    assert result["data"]["ready"] is True
    assert bridge.sessions  # 会话已建立
    # 认证帧携带的是握手文件里的令牌
    assert panel.auth_attempts == [panel.token]


async def test_status_reports_connection_and_deployment(ctx, panel, tmp_config: Config):
    await host_tools.host_wait_ready(host="premiere:25.0", timeout_s=3)
    result = await host_tools.host_status(host="premiere:25.0")
    assert result["ok"] is True
    assert result["host_state"]["connected"] is True
    assert result["data"]["host"]["selected"]["version"] == "25.0"
    assert "deployment" in result["data"]


# --------------------------------------------------------------------------
# 只读调用与信封
# --------------------------------------------------------------------------
async def test_app_info_roundtrip(ctx, panel, project_panel):
    result = await app_tools.ppro_app_info(host="premiere:25.0")
    assert result["ok"] is True
    assert result["data"]["app_version"] == "25.0.0"
    assert result["error"] is None
    assert result["undo_hint"] is None  # T0 不需要撤销指引
    assert panel.count("app.info") == 1
    meta = panel.last_meta("app.info")
    assert meta["dryRun"] is False
    assert meta["traceId"]


async def test_read_only_tool_sends_no_dry_run_flag(ctx, panel):
    await timeline_tools.ppro_timeline_list_clips(host="premiere:25.0")
    assert panel.commands_seen() == ["timeline.list_clips"]


async def test_error_response_becomes_structured_failure(ctx, panel):
    panel.error_response["app.info"] = {
        "code": "HOST_ERROR",
        "message": "宿主 API 抛错",
        "remedy": "在宿主里手工确认一次",
        "line": 42,
        "file": "ops/app.jsx",
    }
    result = await app_tools.ppro_app_info(host="premiere:25.0")
    assert result["ok"] is False
    assert result["error"]["code"] == "HOST_ERROR"
    assert "宿主 API 抛错" in result["error"]["message"]
    assert result["error"]["remedy"] == "在宿主里手工确认一次"


# --------------------------------------------------------------------------
# 路径白名单与工具组
# --------------------------------------------------------------------------
async def test_path_outside_whitelist_is_rejected(ctx, panel):
    result = await project_tools.ppro_project_import(
        paths=[r"C:\Windows\System32\evil.mp4"], host="premiere:25.0"
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "PATH_NOT_ALLOWED"
    assert panel.count("project.import") == 0  # 根本没有发给宿主


async def test_import_inside_whitelist_passes_paths(ctx, panel, tmp_path: Path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"stub")
    result = await project_tools.ppro_project_import(
        paths=[str(media)], host="premiere:25.0"
    )
    assert result["ok"] is True, result["error"]
    sent = panel.last_args("project.import")
    assert sent["paths"] == [str(media.resolve())]


async def test_disabled_tool_group_is_refused(tmp_config: Config, logger, bridge):
    from dsh_adobe_mcp.tools.context import ToolContext, set_context

    config = tmp_config.model_copy(
        update={"tools": tmp_config.tools.model_copy(update={"enabled_groups": ["core"]})}
    )
    context = ToolContext(config, logger=logger, bridge=bridge)
    set_context(context)
    try:
        result = await project_tools.ppro_project_import(
            paths=[r"C:\x.mp4"], host="premiere:25.0"
        )
    finally:
        set_context(None)
    assert result["ok"] is False
    assert result["error"]["code"] == "TOOL_NOT_ENABLED"


# --------------------------------------------------------------------------
# T2 确认门 + 检查点 + dry-run
# --------------------------------------------------------------------------
async def test_t2_requires_confirmation_and_creates_checkpoint(
    ctx, panel, project_panel, tmp_config: Config
):
    first = await timeline_tools.ppro_timeline_remove_clip(
        ref="V:1:0", host="premiere:25.0"
    )
    assert first["ok"] is False
    assert first["error"]["code"] == "CONFIRM_REQUIRED"
    token = first["data"]["confirm_token"]
    assert first["data"]["impact"]["targets"]["clip"]["name"] == "a.mp4"
    # 未确认前绝不执行
    assert panel.count_executed("timeline.remove_clip") == 0

    second = await safety_tools.safety_confirm_operation(
        confirm_token=token, host="premiere:25.0"
    )
    assert second["ok"] is True, second["error"]
    assert panel.count_executed("timeline.remove_clip") == 1
    assert second["undo_hint"]

    # 检查点已创建且是执行前建的
    checkpoints = ctx.checkpoints.list()
    assert len(checkpoints) == 1
    assert checkpoints[0].project_path == str(project_file_path(project_panel))


def project_file_path(panel: FakePanel) -> str:
    return panel.payloads["project.info"]["path"]


async def test_t2_token_is_single_use(ctx, panel, project_panel):
    first = await timeline_tools.ppro_timeline_remove_clip(ref="V:1:0", host="premiere:25.0")
    token = first["data"]["confirm_token"]
    ok = await safety_tools.safety_confirm_operation(confirm_token=token, host="premiere:25.0")
    assert ok["ok"] is True
    again = await safety_tools.safety_confirm_operation(confirm_token=token, host="premiere:25.0")
    assert again["ok"] is False
    # 一次性消费：重放给出比"无效"更精确的原因
    assert again["error"]["code"] == "CONFIRM_TOKEN_USED"
    assert "新令牌" in again["error"]["remedy"]
    assert panel.count_executed("timeline.remove_clip") == 1


async def test_t2_token_bound_to_parameters(ctx, panel, project_panel):
    first = await timeline_tools.ppro_timeline_remove_clip(ref="V:1:0", host="premiere:25.0")
    token = first["data"]["confirm_token"]
    mismatched = await timeline_tools.ppro_timeline_remove_clip(
        ref="V:1:9", host="premiere:25.0", confirm_token=token
    )
    assert mismatched["ok"] is False
    assert mismatched["error"]["code"] == "CONFIRM_TOKEN_INVALID"
    assert panel.count_executed("timeline.remove_clip") == 0


async def test_dry_run_does_not_touch_host_state(ctx, panel, project_panel):
    result = await timeline_tools.ppro_timeline_remove_clip(
        ref="V:1:0", host="premiere:25.0", dry_run=True
    )
    assert result["ok"] is True, result["error"]
    assert result["data"]["dry_run"] is True
    assert result["data"]["actions"]
    assert panel.last_meta("timeline.remove_clip")["dryRun"] is True
    # dry-run 不需要确认令牌，也不建检查点
    assert ctx.checkpoints.list() == []
    assert len(ctx.confirms) == 0


async def test_dirty_project_is_saved_before_checkpoint(ctx, panel, project_panel):
    info = dict(panel.payloads["project.info"])
    info["dirty"] = True
    panel.payloads["project.info"] = info
    first = await timeline_tools.ppro_timeline_remove_clip(ref="V:1:0", host="premiere:25.0")
    token = first["data"]["confirm_token"]
    result = await safety_tools.safety_confirm_operation(confirm_token=token, host="premiere:25.0")
    assert result["ok"] is True, result["error"]
    # 先 save（T1），再 remove_clip
    executed = panel.executed_commands()
    assert executed.index("project.save") < executed.index("timeline.remove_clip")


# --------------------------------------------------------------------------
# QE 门禁
# --------------------------------------------------------------------------
async def test_qe_command_is_refused_when_disabled(ctx, panel):
    result = await timeline_tools.ppro_timeline_razor(at_s=1.5, host="premiere:25.0")
    assert result["ok"] is False
    assert result["error"]["code"] == "QE_DISABLED"
    assert panel.count_executed("timeline.razor") == 0


async def test_qe_command_runs_with_warning_when_enabled(
    tmp_config: Config, logger, fake_registry, process_probe, project_panel
):
    from dsh_adobe_mcp.bridge.service import BridgeService
    from dsh_adobe_mcp.tools.context import ToolContext, set_context

    # 注意：QE 门禁同时存在于 L1（ToolContext）与 L2（BridgeService），
    # 因此桥接服务也必须用启用 QE 的配置构建，否则会被纵深防御那一层拦下。
    config = tmp_config.model_copy(
        update={
            "extendscript": tmp_config.extendscript.model_copy(update={"enable_qe": True})
        }
    )
    service = BridgeService(
        config,
        logger=logger,
        registry_reader=fake_registry,
        process_probe=process_probe,
        discovery_ttl_s=0.0,
    )
    context = ToolContext(config, logger=logger, bridge=service)
    set_context(context)
    try:
        first = await timeline_tools.ppro_timeline_razor(at_s=1.5, host="premiere:25.0")
        assert first["error"]["code"] == "CONFIRM_REQUIRED"
        assert any(
            "QE" in warning for warning in first["warnings"]
        ), "启用 QE 后每次调用都必须带风险警告（说明书 §6.2）"
        result = await safety_tools.safety_confirm_operation(
            confirm_token=first["data"]["confirm_token"], host="premiere:25.0"
        )
    finally:
        set_context(None)
    assert result["ok"] is True, result["error"]
    assert any("QE" in warning for warning in result["warnings"])


async def test_t2_is_refused_when_project_file_missing(ctx, panel):
    """工程文件不在磁盘上时无法建立检查点，因此 T2 必须被拒绝（说明书 §14.4）。"""
    info = dict(panel.payloads["project.info"])
    info.update({"path": r"D:\does\not\exist.prproj", "dirty": False})
    panel.payloads["project.info"] = info
    first = await timeline_tools.ppro_timeline_remove_clip(ref="V:1:0", host="premiere:25.0")
    result = await safety_tools.safety_confirm_operation(
        confirm_token=first["data"]["confirm_token"], host="premiere:25.0"
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "CHECKPOINT_FAILED"
    assert panel.count_executed("timeline.remove_clip") == 0


# --------------------------------------------------------------------------
# resync
# --------------------------------------------------------------------------
async def test_resync_reports_differences_and_clears_desync(ctx, bridge, panel):
    await host_tools.host_wait_ready(host="premiere:25.0", timeout_s=3)
    session = bridge.session_for("premiere:25.0")
    session.last_state = {
        "has_project": True,
        "project_path": "D:\\old.prproj",
        "dirty": False,
        "active_sequence": "S0",
        "playhead_s": 0.0,
    }
    session.runner.mark_desync("测试注入")
    result = await host_tools.host_resync(host="premiere:25.0")
    assert result["ok"] is True, result["error"]
    fields = {item["field"] for item in result["data"]["differences"]}
    assert {"project_path", "active_sequence"} <= fields
    assert result["data"]["desync_cleared"] is True
    assert session.runner.desynced is False


# --------------------------------------------------------------------------
# 长任务
# --------------------------------------------------------------------------
async def test_export_job_lifecycle(ctx, project_panel, tmp_path: Path, project_file: Path):
    panel = project_panel
    output = tmp_path / "out.mp4"
    preset = tmp_path / "preset.epr"
    preset.write_bytes(b"preset")

    first = await export_tools.ppro_export_encode_sequence(
        output_path=str(output),
        preset_path=str(preset),
        host="premiere:25.0",
    )
    assert first["error"]["code"] == "CONFIRM_REQUIRED"
    assert first["data"]["impact"]["targets"]["output"]["exists"] is False

    second = await safety_tools.safety_confirm_operation(
        confirm_token=first["data"]["confirm_token"], host="premiere:25.0"
    )
    assert second["ok"] is True, second["error"]
    job_id = second["data"]["job_id"]

    # 任务在后台完成提交
    import asyncio

    for _ in range(50):
        await asyncio.sleep(0.02)
        status = await job_tools.job_status(job_id=job_id)
        if status["data"]["state"] == "running" and panel.count("export.encode_sequence") == 1:
            break
    assert panel.count_executed("export.encode_sequence") == 1

    # 输出文件出现并稳定 → 推断完成（显式标注 inferred）
    output.write_bytes(b"x" * 1024)
    for _ in range(6):
        await ctx.jobs.poll_once()
        await asyncio.sleep(0.01)
    final = await job_tools.job_status(job_id=job_id)
    assert final["data"]["state"] == "succeeded"
    assert final["data"]["result"]["inferred"] is True
    assert any("推断" in note for note in final["data"]["notes"])

    listing = await job_tools.job_list()
    assert listing["data"]["summary"]["total"] == 1


async def test_job_cancel_when_not_cancellable(ctx, project_panel, tmp_path: Path):
    panel = project_panel
    preset = tmp_path / "preset.epr"
    preset.write_bytes(b"preset")
    first = await export_tools.ppro_export_encode_sequence(
        output_path=str(tmp_path / "o.mp4"), preset_path=str(preset), host="premiere:25.0"
    )
    second = await safety_tools.safety_confirm_operation(
        confirm_token=first["data"]["confirm_token"], host="premiere:25.0"
    )
    job_id = second["data"]["job_id"]
    import asyncio

    await asyncio.sleep(0.05)
    result = await job_tools.job_cancel(job_id=job_id, host="premiere:25.0")
    assert result["ok"] is False
    assert result["error"]["code"] == "JOB_NOT_CANCELLABLE"
    assert "取消" in result["error"]["message"]


# --------------------------------------------------------------------------
# WAL（说明书 §8.5）
# --------------------------------------------------------------------------
async def test_wal_records_script_text_before_send(ctx, panel, tmp_config: Config):
    await app_tools.ppro_app_info(host="premiere:25.0")
    wal_files = sorted(tmp_config.server.log_dir.glob("wal-*.jsonl"))
    assert wal_files, "WAL 文件未生成"
    entries = [json.loads(line) for line in wal_files[0].read_text(encoding="utf-8").splitlines()]
    phases = [entry["phase"] for entry in entries]
    assert phases == ["request", "result"]
    request_entry = entries[0]
    assert request_entry["command"] == "app.info"
    assert "DSH.dispatch(" in request_entry["script_text"]
    # 脚本原文里应包含处理器的实现（"脚本原文比命令名有价值得多"）
    assert "function app_info" in request_entry["script_text"]
    assert entries[1]["ok"] is True
    assert "rtt_ms" in entries[1]["timing"]


async def test_idempotency_key_generated_for_non_idempotent_commands(ctx, panel):
    # 用 T1 的 bin_create（非幂等）来验证幂等键被下发（C-4：非幂等命令必须带幂等键）
    result = await project_tools.ppro_project_bin_create(name="Bins", host="premiere:25.0")
    assert result["ok"] is True, result["error"]
    assert result["idempotency_key"]
    assert panel.last_meta("project.bin_create")["idempotencyKey"] == result["idempotency_key"]
