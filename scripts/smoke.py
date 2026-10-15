"""端到端冒烟脚本（说明书 §14.3 的链路，输出比 pytest 更适合人工观察）。

用法::

    python scripts/smoke.py --yes                 # 用配置里的宿主编译
    python scripts/smoke.py --yes --keep-project  # 保留工程与导出产物供检查

**安全前提**：本脚本会新建工程、导入素材、上时间线并导出，因此
* 必须显式加 ``--yes`` 才会执行（否则只做前置检查并退出）；
* 执行前会确认宿主里没有用户的真实工程，有则直接拒绝运行。

它调用的是与 MCP 工具完全相同的 L1 流水线（ToolContext），
因此既能验证链路，也能验证风险分级/确认令牌/检查点/WAL 是否真的生效。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path

import _bootstrap  # noqa: F401

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

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "media"

PASS = "  [ OK ]"
FAIL = "  [FAIL]"
INFO = "  [ .. ]"


class SmokeFailure(RuntimeError):
    pass


def step(title: str) -> None:
    print(f"\n=== {title} ===")


def check(condition: bool, message: str, *, detail: object = None) -> None:
    if condition:
        print(f"{PASS} {message}")
        return
    print(f"{FAIL} {message}")
    if detail is not None:
        print(f"         {detail}")
    raise SmokeFailure(message)


def show(result: dict, *, expect_ok: bool = True) -> dict:
    if result.get("ok"):
        print(f"{PASS} ok=True data={result.get('data')}")
    else:
        error = result.get("error") or {}
        print(f"{FAIL} ok=False {error.get('code')}: {error.get('message')}")
        print(f"         remedy: {error.get('remedy')}")
    for warning in result.get("warnings") or []:
        print(f"  [warn] {warning}")
    if expect_ok:
        check(bool(result.get("ok")), "调用成功", detail=result.get("error"))
    return result


async def confirm_and_run(ctx: ToolContext, result: dict, *, host: str | None) -> dict:
    """走一遍 T2 确认流程：先断言"未确认绝不执行"，再确认执行。"""
    check(
        (result.get("error") or {}).get("code") == "CONFIRM_REQUIRED",
        "T2 操作在未确认时被拒绝（返回 CONFIRM_REQUIRED）",
        detail=result.get("error"),
    )
    token = result["data"]["confirm_token"]
    impact = result["data"]["impact"]
    print(f"{INFO} 影响说明：{impact.get('summary')}")
    for item in impact.get("irreversible") or []:
        print(f"{INFO}   不可逆：{item}")
    for item in impact.get("reversible") or []:
        print(f"{INFO}   可回退：{item}")
    if impact.get("targets"):
        print(f"{INFO}   目标现状：{impact['targets']}")

    confirmed = await safety_tools.safety_confirm_operation(
        confirm_token=token, host=host
    )
    return show(confirmed)


async def run(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="dsh-smoke-"))
    work_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(
        env={},
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
    logger = StructuredLogger(
        config.server.log_dir, level="debug", console_level="none", redactor=Redactor()
    )
    ctx = ToolContext(config, logger=logger)
    set_context(ctx)
    print(f"{INFO} 工作目录：{work_dir}")
    print(f"{INFO} 日志目录：{config.server.log_dir}")

    try:
        step("1/8 宿主与面板")
        status = show(await host_tools.host_status())
        selected = status["data"]["host"]["selected"]
        check(bool(selected), "发现目标宿主", detail=status["data"]["installs"])
        if not selected.get("running"):
            print(f"{INFO} 宿主未运行，正在启动……")
            show(await host_tools.host_launch(wait=True))
        show(await host_tools.host_wait_ready(timeout_s=180))

        step("2/8 前置检查：宿主里不能有用户的真实工程")
        info = show(await app_tools.ppro_app_info())
        if info["data"].get("has_project") and info["data"].get("project_path"):
            print(f"{FAIL} 宿主当前打开了工程：{info['data']['project_path']}")
            print("         为避免污染用户工作，冒烟脚本拒绝继续。请先在宿主中关闭它。")
            return 2
        check(True, "宿主持有干净状态")

        step("3/8 新建工程（T2 确认 + 检查点）")
        project_path = work_dir / "smoke.prproj"
        pending = await project_tools.ppro_project_new(path=str(project_path))
        show(await confirm_and_run(ctx, pending, host=None))
        check(project_path.is_file(), "工程文件已落盘", detail=str(project_path))

        step("4/8 导入素材")
        media = sorted(FIXTURE_DIR.glob("*.mp4"))
        if not media:
            print(f"{INFO} 没有测试素材；先运行 pwsh -File tests/fixtures/make_fixtures.ps1")
            print(f"{INFO} 冒烟在导入这一步停止（这不算失败）。")
            return 0
        imported = show(await project_tools.ppro_project_import(paths=[str(p) for p in media]))
        check(bool(imported["data"]["imported"]), "至少导入了一个素材")
        item = imported["data"]["imported"][0]["path"]

        step("5/8 建序列 + 上时间线")
        show(await sequence_tools.ppro_sequence_create_from_clips(name="DSH-Smoke", items=[item]))
        inserted = show(await timeline_tools.ppro_timeline_insert_clip(item=item, at_s=0))
        clips = show(await timeline_tools.ppro_timeline_list_clips())
        check(
            any(clip["ref"] == inserted["data"]["ref"] for clip in clips["data"]["clips"]),
            "新插入的片段出现在片段列表中",
        )

        step("6/8 导出（T2 + 长任务）")
        presets = show(await export_tools.ppro_export_list_presets())
        if not presets["data"]["presets"]:
            print(f"{INFO} 没有可用的编码预设（.epr）。请在宿主中导出一个预设，")
            print(f"{INFO} 或在配置 export.preset_dirs 中给出预设目录。导出步骤跳过。")
            return 0
        preset = presets["data"]["presets"][0]["path"]
        output = work_dir / "smoke_export.mp4"
        pending = await export_tools.ppro_export_encode_sequence(
            output_path=str(output), preset_path=preset
        )
        started = show(await confirm_and_run(ctx, pending, host=None))
        job_id = started["data"]["job_id"]
        print(f"{INFO} 任务已提交：{job_id}（宿主不提供进度时显示 unknown，由输出文件增长推断）")

        deadline = time.time() + args.export_timeout
        while time.time() < deadline:
            await asyncio.sleep(2)
            job = await job_tools.job_status(job_id=job_id)
            print(f"{INFO} 状态={job['data']['state']} 进度={job['data']['progress_display']}")
            if job["data"]["state"] in ("succeeded", "failed", "cancelled"):
                break
        job = await job_tools.job_status(job_id=job_id)
        check(job["data"]["state"] == "succeeded", "导出任务成功", detail=job["data"])
        check(output.is_file() and output.stat().st_size > 0, f"产物非空：{output}")

        step("7/8 检查点与 WAL")
        checkpoints = show(await safety_tools.safety_list_checkpoints())
        check(bool(checkpoints["data"]["checkpoints"]), "T2 操作留下了检查点")
        wal = sorted(config.server.log_dir.glob("wal-*.jsonl"))
        check(bool(wal), "WAL 已写入脚本原文", detail=str(wal))

        step("8/8 关闭工程（不保存）")
        pending = await project_tools.ppro_project_close(confirm_discard=True)
        show(await confirm_and_run(ctx, pending, host=None))

        print("\n冒烟通过。")
        if args.keep_project:
            print(f"产物保留在：{work_dir}")
        return 0
    except SmokeFailure as exc:
        print(f"\n冒烟失败：{exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - 脚本层兜底，打印而不炸栈
        print(f"\n冒烟异常中断：{type(exc).__name__}: {exc}")
        return 1
    finally:
        set_context(None)
        await ctx.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="dsh-adobe-mcp 端到端冒烟")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认在宿主上执行真实操作（新建工程/导入/上时间线/导出）",
    )
    parser.add_argument("--work-dir", help="工程与产物目录（缺省为临时目录）")
    parser.add_argument("--export-timeout", type=float, default=300, help="导出等待上限（秒）")
    parser.add_argument("--keep-project", action="store_true", help="保留工程与产物")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.yes:
        print(__doc__)
        print("本脚本会在真实宿主上新建工程并导出，因此必须显式加 --yes 才会执行。")
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
