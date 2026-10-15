"""混沌测试（说明书 §14.4）。

覆盖四类真实的失败场景，断言的是**行为契约**而不是实现细节：

1. 求值在途时宿主/面板消失 → 服务器返回确定错误、进入可恢复状态、检查点仍可用；
2. 连接被拔掉 → 心跳/断连被检测到，DESYNC 被标记，重连后 host_resync 能清除；
3. 求值超时 → **不重试**、标记 DESYNC、后续命令被拒绝；
4. 检查点不可用 → T2 操作被拒绝，而不是"没有检查点也照做"。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dsh_adobe_mcp.bridge.cep_client import HandshakeWatcher
from dsh_adobe_mcp.config import Config
from dsh_adobe_mcp.errors import ToolError
from dsh_adobe_mcp.jobs.models import JobState
from dsh_adobe_mcp.safety.checkpoint import CheckpointError
from dsh_adobe_mcp.tools import app as app_tools
from dsh_adobe_mcp.tools import export as export_tools
from dsh_adobe_mcp.tools import host as host_tools
from dsh_adobe_mcp.tools import jobs as job_tools
from dsh_adobe_mcp.tools import project as project_tools
from dsh_adobe_mcp.tools import safety as safety_tools
from fakes.fake_panel import FakePanel

pytestmark = pytest.mark.chaos


# --------------------------------------------------------------------------
# 1. 求值在途时断开
# --------------------------------------------------------------------------
async def test_evaluate_timeout_marks_desync_and_never_retries(ctx, panel, tmp_config: Config):
    panel.no_response.add("app.info")

    result = await app_tools.ppro_app_info(host="premiere:25.0")
    assert result["ok"] is False
    assert result["error"]["code"] == "BRIDGE_TIMEOUT"
    # 超时不是失败而是"不确定"：remedy 必须要求先 resync，而不是让调用者重试
    assert "host_resync" in result["error"]["remedy"]
    assert result["error"]["detail"]["command"] == "app.info"

    # 绝不自动重试：宿主侧只应看到一次请求
    assert panel.count("app.info") == 1

    # 后续命令立刻被拒绝（不带着未知状态继续写工程）
    blocked = await project_tools.ppro_project_save(host="premiere:25.0")
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "BRIDGE_DESYNC"
    assert panel.count("project.save") == 0

    # resync 是唯一出路，且它能清除 DESYNC
    panel.no_response.discard("app.info")
    resynced = await host_tools.host_resync(host="premiere:25.0")
    assert resynced["ok"] is True, resynced["error"]
    assert resynced["data"]["desync_cleared"] is True

    after = await app_tools.ppro_app_info(host="premiere:25.0")
    assert after["ok"] is True


async def test_desync_state_is_visible_in_runner_snapshot(ctx, bridge, panel):
    panel.no_response.add("app.info")
    await app_tools.ppro_app_info(host="premiere:25.0")
    session = bridge.session_for("premiere:25.0")
    snapshot = session.runner.snapshot()
    assert snapshot["desync"]["desynced"] is True
    assert "超时" in snapshot["desync"]["reason"]


# --------------------------------------------------------------------------
# 2. 连接被拔掉
# --------------------------------------------------------------------------
async def test_connection_drop_marks_desync_and_recovers(ctx, bridge, panel, tmp_config: Config):
    await host_tools.host_wait_ready(host="premiere:25.0", timeout_s=3)
    panel.close_on.add("app.info")

    result = await app_tools.ppro_app_info(host="premiere:25.0")
    assert result["ok"] is False
    assert result["error"]["code"] in ("BRIDGE_DISCONNECTED", "BRIDGE_TIMEOUT")

    session = bridge.session_for("premiere:25.0")
    if session is not None:
        assert session.runner.desynced is True

    # 面板还在（只是断了这条连接）→ 重连后必须能恢复
    panel.close_on.discard("app.info")
    await asyncio.sleep(0.05)
    recovered = await host_tools.host_wait_ready(host="premiere:25.0", timeout_s=5)
    assert recovered["ok"] is True, recovered["error"]
    fresh = await app_tools.ppro_app_info(host="premiere:25.0")
    assert fresh["ok"] is True


async def test_panel_crash_removes_handshake_and_reports_panel_not_loaded(
    ctx, bridge, panel: FakePanel, tmp_config: Config
):
    await host_tools.host_wait_ready(host="premiere:25.0", timeout_s=3)
    panel.remove_handshake()
    await panel.stop()

    # 握手目录应立即能看到文件消失（陈旧文件被清理）
    watcher = HandshakeWatcher(tmp_config.bridge.handshake_dir)
    assert watcher.find("PPRO") == []

    result = await app_tools.ppro_app_info(host="premiere:25.0")
    assert result["ok"] is False
    assert result["error"]["code"] == "PANEL_NOT_LOADED"
    assert "重启宿主" in result["error"]["remedy"]
    assert result["error"]["detail"]["deployment"]["manifest"]["ok"] is True


async def test_stale_handshake_from_dead_process_is_cleaned(tmp_config: Config):
    """异常退出会留下握手文件；进程号已消失时必须清理，而不是拿去连接。"""
    directory = tmp_config.bridge.handshake_dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "handshake-PPRO-999999.json").write_text(
        '{"protocol":1,"port":1,"token":"x","host":"PPRO","pid":999999}', encoding="utf-8"
    )
    watcher = HandshakeWatcher(directory)
    assert watcher.find("PPRO") == []
    assert not (directory / "handshake-PPRO-999999.json").exists()


# --------------------------------------------------------------------------
# 3. 宿主崩溃（强杀进程）
# --------------------------------------------------------------------------
async def test_host_killed_mid_flight_leaves_server_recoverable(
    ctx, bridge, project_panel: FakePanel, tmp_config: Config
):
    await host_tools.host_wait_ready(host="premiere:25.0", timeout_s=3)
    # 先建一个检查点，验证崩溃后它仍然可用
    checkpoint = ctx.checkpoints.create(
        project_panel.payloads["project.info"]["path"], label="crash-proof"
    )

    project_panel.no_response.add("app.info")
    first = await app_tools.ppro_app_info(host="premiere:25.0")
    assert first["ok"] is False

    # 宿主进程消失：握手被清理，工具给出确定错误而不是挂住
    project_panel.remove_handshake()
    await project_panel.stop()
    second = await project_tools.ppro_project_save(host="premiere:25.0")
    assert second["ok"] is False
    assert second["error"]["code"] in ("BRIDGE_DESYNC", "BRIDGE_DISCONNECTED", "PANEL_NOT_LOADED")

    # 服务器仍然可用：状态查询照常返回，检查点仍在
    status = await host_tools.host_status(host="premiere:25.0")
    assert status["ok"] is True
    listed = await safety_tools.safety_list_checkpoints()
    assert any(item["id"] == checkpoint.id for item in listed["data"]["checkpoints"])

    # 任务模型也能报告历史（没有任务 → 空列表而不是异常）
    jobs = await job_tools.job_list()
    assert jobs["ok"] is True
    assert jobs["data"]["jobs"] == []


# --------------------------------------------------------------------------
# 4. 检查点不可用
# --------------------------------------------------------------------------
async def test_t2_is_refused_when_checkpoint_dir_is_unusable(
    tmp_config: Config, logger, bridge, project_panel: FakePanel, tmp_path: Path
):
    """把 checkpoint_dir 指向一个**已存在的文件**：目录建不出来 → 拒绝执行 T2。"""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("occupied", encoding="utf-8")
    config = tmp_config.model_copy(
        update={
            "safety": tmp_config.safety.model_copy(update={"checkpoint_dir": blocker})
        }
    )
    from dsh_adobe_mcp.tools.context import ToolContext, set_context

    context = ToolContext(config, logger=logger, bridge=bridge)
    set_context(context)
    try:
        first = await project_tools.ppro_project_close(
            confirm_discard=True, host="premiere:25.0"
        )
        token = first["data"]["confirm_token"]
        result = await safety_tools.safety_confirm_operation(
            confirm_token=token, host="premiere:25.0"
        )
    finally:
        set_context(None)

    assert result["ok"] is False
    assert result["error"]["code"] == "CHECKPOINT_FAILED"
    # 关键断言：宁可拒绝，也不能在没有检查点的情况下执行破坏性操作
    assert project_panel.count_executed("project.close") == 0


async def test_checkpoint_failure_during_restore_is_reported(
    ctx, tmp_config: Config, logger, bridge, project_panel: FakePanel
):
    checkpoint = ctx.checkpoints.create(
        project_panel.payloads["project.info"]["path"], label="restore-source"
    )

    def boom(*args, **kwargs):
        raise CheckpointError("CHECKPOINT_FAILED", "模拟磁盘满", "清理磁盘后重试")

    monkeyed = ctx.checkpoints
    original = monkeyed.create
    monkeyed.create = boom  # type: ignore[assignment]
    try:
        first = await safety_tools.safety_restore_checkpoint(
            checkpoint_id=checkpoint.id, host="premiere:25.0"
        )
        result = await safety_tools.safety_confirm_operation(
            confirm_token=first["data"]["confirm_token"], host="premiere:25.0"
        )
    finally:
        monkeyed.create = original  # type: ignore[assignment]

    assert result["ok"] is False
    assert result["error"]["code"] == "CHECKPOINT_FAILED"
    assert project_panel.count_executed("project.open") == 0


# --------------------------------------------------------------------------
# 5. 任务通道的失败模式
# --------------------------------------------------------------------------
async def test_job_submission_failure_is_recorded_not_raised(
    ctx, project_panel: FakePanel, tmp_path: Path
):
    project_panel.error_response["export.encode_sequence"] = {
        "code": "HOST_ERROR",
        "message": "编码器不可用",
        "remedy": "在宿主里手工导出一次确认编码器状态",
    }
    preset = tmp_path / "preset.epr"
    preset.write_bytes(b"preset")
    first = await export_tools.ppro_export_encode_sequence(
        output_path=str(tmp_path / "o.mp4"), preset_path=str(preset), host="premiere:25.0"
    )
    result = await safety_tools.safety_confirm_operation(
        confirm_token=first["data"]["confirm_token"], host="premiere:25.0"
    )
    # 确认阶段成功返回 job_id（提交是异步的）
    assert result["ok"] is True, result["error"]
    job_id = result["data"]["job_id"]

    for _ in range(50):
        await asyncio.sleep(0.01)
        if ctx.jobs.get(job_id).state.terminal:
            break
    job = ctx.jobs.get(job_id)
    assert job.state is JobState.failed
    assert job.error["code"] == "HOST_ERROR"

    status = await job_tools.job_status(job_id=job_id)
    assert status["data"]["state"] == "failed"
    assert "编码器不可用" in status["data"]["error"]["message"]


async def test_bridge_timeout_while_submitting_job_marks_job_failed(
    ctx, project_panel: FakePanel, tmp_path: Path
):
    project_panel.no_response.add("export.encode_sequence")
    preset = tmp_path / "preset.epr"
    preset.write_bytes(b"preset")
    first = await export_tools.ppro_export_encode_sequence(
        output_path=str(tmp_path / "o.mp4"), preset_path=str(preset), host="premiere:25.0"
    )
    result = await safety_tools.safety_confirm_operation(
        confirm_token=first["data"]["confirm_token"], host="premiere:25.0"
    )
    job_id = result["data"]["job_id"]
    for _ in range(200):
        await asyncio.sleep(0.01)
        if ctx.jobs.get(job_id).state.terminal:
            break
    job = ctx.jobs.get(job_id)
    assert job.state is JobState.failed
    assert job.error["code"] == "BRIDGE_TIMEOUT"
    # 超时后连接进入 DESYNC，后续命令被拒绝
    with pytest.raises(ToolError) as excinfo:
        await ctx.bridge.call(
            ctx.contract.by_name["app.info"], {}, host_selector="premiere:25.0"
        )
    assert excinfo.value.code == "BRIDGE_DESYNC"
