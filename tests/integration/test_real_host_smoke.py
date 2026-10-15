"""真机端到端冒烟（说明书 §14.3）。**默认跳过。**

开启方式（两种任选）：

    pytest -m integration --integration
    $env:DSH_ADOBE_MCP_INTEGRATION=1; pytest -m integration

前置条件（说明书 §14.3 明确要求）：
* 本机已安装并授权可用的 Premiere Pro；
* CEP 面板已部署（``pwsh -File scripts/dev_install_cep.ps1``），必要时设置 PlayerDebugMode；
* **宿主当前没有打开用户的真实工程** —— 本测试会新建工程、导入素材并导出，
  在用户的工作上跑会污染用户数据。测试自身会先检查这一点，不满足就直接失败。
* ``tests/fixtures/media/*.mp4`` 存在（用 ``tests/fixtures/make_fixtures.ps1`` 生成）。

链路（与说明书 §14.3 的 7 步一致）：

    启动宿主并等待面板就绪 → 新建工程 → 导入素材 → 创建序列 → 插入时间线
    → 读取片段列表断言 → 导出短片断言产物非空 → 关闭工程（不保存）
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from dsh_adobe_mcp.config import load_config
from dsh_adobe_mcp.logging_setup import Redactor, StructuredLogger
from dsh_adobe_mcp.tools import app as app_tools
from dsh_adobe_mcp.tools import export as export_tools
from dsh_adobe_mcp.tools import host as host_tools
from dsh_adobe_mcp.tools import jobs as job_tools
from dsh_adobe_mcp.tools import project as project_tools
from dsh_adobe_mcp.tools import safety as safety_tools
from dsh_adobe_mcp.tools import sequence as sequence_tools
from dsh_adobe_mcp.tools import timeline as timeline_tools
from dsh_adobe_mcp.tools.context import ToolContext, set_context

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "media"


@pytest.fixture(scope="module")
def integration_config(tmp_path_factory) -> object:
    if os.environ.get("DSH_ADOBE_MCP_INTEGRATION", "") not in ("1", "true", "yes"):
        pytest.skip("集成测试默认跳过：设置 DSH_ADOBE_MCP_INTEGRATION=1 或加 --integration")
    work_dir = tmp_path_factory.mktemp("integration")
    config = load_config(
        env=os.environ,
        cli_overrides={
            "server": {"log_dir": str(work_dir / "logs"), "log_level": "debug"},
            "safety": {"checkpoint_dir": str(work_dir / "checkpoints")},
            "jobs": {"state_dir": str(work_dir / "jobs"), "poll_interval_s": 1},
            "tools": {
                "enabled_groups": [
                    "core",
                    "project_edit",
                    "timeline_edit",
                    "effects",
                    "media",
                    "export",
                ]
            },
        },
        extra_config_files=[REPO_ROOT / "config.toml"]
        if (REPO_ROOT / "config.toml").is_file()
        else None,
    )
    return config


@pytest.fixture(scope="module")
def live_context(integration_config):
    logger = StructuredLogger(
        integration_config.server.log_dir,
        level="debug",
        console_level="none",
        redactor=Redactor(),
    )
    context = ToolContext(integration_config, logger=logger)
    set_context(context)
    try:
        yield context
    finally:
        set_context(None)
        logger.close()


async def _assert_no_user_project(ctx) -> None:
    """前置检查：宿主里不能有用户的真实工程（说明书 §14.3）。"""
    result = await app_tools.ppro_app_info()
    if not result["ok"]:
        pytest.fail(f"无法读取宿主状态：{result['error']}")
    data = result["data"] or {}
    if data.get("has_project") and data.get("project_path"):
        pytest.fail(
            "宿主当前打开了工程，为避免污染用户工作，集成测试拒绝继续："
            f"{data['project_path']}（请先在宿主中关闭它）"
        )


async def test_full_smoke_chain(live_context, tmp_path: Path):
    ctx = live_context

    # ---- 1. 宿主与面板 ----
    status = await host_tools.host_status()
    assert status["ok"] is True, status["error"]
    if not (status["data"]["host"]["selected"] or {}).get("running"):
        launched = await host_tools.host_launch(wait=True)
        assert launched["ok"] is True, launched["error"]
    ready = await host_tools.host_wait_ready(timeout_s=180)
    assert ready["ok"] is True, ready["error"]

    await _assert_no_user_project(ctx)

    # ---- 2. 新建工程（T2：先确认再执行）----
    project_path = tmp_path / "smoke.prproj"
    first = await project_tools.ppro_project_new(path=str(project_path))
    assert first["error"]["code"] == "CONFIRM_REQUIRED"
    created = await safety_tools.safety_confirm_operation(
        confirm_token=first["data"]["confirm_token"]
    )
    assert created["ok"] is True, created["error"]
    assert project_path.is_file(), "新建工程后磁盘上应有工程文件"

    # ---- 3. 导入素材 ----
    media = sorted(FIXTURE_DIR.glob("*.mp4"))
    if not media:
        await project_tools.ppro_project_close(confirm_discard=True)
        pytest.skip("缺少测试素材：先运行 tests/fixtures/make_fixtures.ps1 生成")
    imported = await project_tools.ppro_project_import(paths=[str(item) for item in media])
    assert imported["ok"] is True, imported["error"]
    assert imported["data"]["imported"], "导入后应返回导入的条目"

    item_path = imported["data"]["imported"][0]["path"]

    # ---- 4. 创建序列并插入时间线 ----
    sequence = await sequence_tools.ppro_sequence_create_from_clips(
        name="DSH-Smoke", items=[item_path]
    )
    assert sequence["ok"] is True, sequence["error"]

    inserted = await timeline_tools.ppro_timeline_insert_clip(item=item_path, at_s=0)
    assert inserted["ok"] is True, inserted["error"]

    # ---- 5. 读取片段列表并断言 ----
    clips = await timeline_tools.ppro_timeline_list_clips()
    assert clips["ok"] is True, clips["error"]
    listed = clips["data"]["clips"]
    assert listed, "时间线上应当至少有一个片段"
    assert any(clip["ref"] == inserted["data"]["ref"] for clip in listed)

    # ---- 6. 导出并断言产物非空 ----
    presets = await export_tools.ppro_export_list_presets()
    if not presets["data"]["presets"]:
        await project_tools.ppro_project_close(confirm_discard=True)
        pytest.skip("没有可用的编码预设：请在宿主中导出一个预设，或配置 export.preset_dirs")
    preset = presets["data"]["presets"][0]["path"]
    output = tmp_path / "smoke.mp4"

    pending = await export_tools.ppro_export_encode_sequence(
        output_path=str(output), preset_path=preset
    )
    assert pending["error"]["code"] == "CONFIRM_REQUIRED"
    started = await safety_tools.safety_confirm_operation(
        confirm_token=pending["data"]["confirm_token"]
    )
    assert started["ok"] is True, started["error"]
    job_id = started["data"]["job_id"]
    assert ctx.jobs.get(job_id) is not None

    for _ in range(120):
        await asyncio.sleep(1)
        status = await job_tools.job_status(job_id=job_id)
        if status["data"]["state"] in ("succeeded", "failed", "cancelled"):
            break
    assert status["data"]["state"] == "succeeded", status["data"]
    assert output.is_file() and output.stat().st_size > 0, "导出产物必须存在且非空"

    # ---- 7. 关闭工程，不保存 ----
    closing = await project_tools.ppro_project_close(confirm_discard=True)
    assert closing["error"]["code"] == "CONFIRM_REQUIRED"
    closed = await safety_tools.safety_confirm_operation(
        confirm_token=closing["data"]["confirm_token"]
    )
    assert closed["ok"] is True, closed["error"]

    # 检查点机制在真机上也应留下可恢复的副本
    checkpoints = await safety_tools.safety_list_checkpoints()
    assert checkpoints["data"]["checkpoints"], "T2 操作应当留下检查点"


async def test_timeout_recovery_on_real_host(live_context):
    """真机上的 DESYNC 恢复：注入一次超时后必须靠 host_resync 恢复。"""
    ctx = live_context
    command = ctx.contract.by_name["app.info"]
    session = await ctx.bridge.ensure_session()
    session.runner.mark_desync("集成测试注入")

    blocked = await app_tools.ppro_app_info()
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "BRIDGE_DESYNC"

    resynced = await host_tools.host_resync()
    assert resynced["ok"] is True, resynced["error"]
    del command
