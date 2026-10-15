"""stdio 入口（``python -m dsh_adobe_mcp``）。

命令行参数优先于环境变量与配置文件（说明书 §10）。stdout 归 MCP 协议所有，
因此 ``--print-example-config`` 这类输出走 stdout，而日志默认只写文件。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

from . import SERVER_NAME, __version__
from .config import Config, example_toml, load_config
from .logging_setup import Redactor, StructuredLogger, log_dir_is_writable
from .server import build_bundle, run_stdio

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dsh-adobe-mcp",
        description="通过 MCP 驱动 Adobe Premiere Pro（stdio 传输）",
    )
    parser.add_argument("--version", action="version", version=f"{SERVER_NAME} {__version__}")
    parser.add_argument("--config", type=Path, help="额外的配置文件（优先级高于用户级与仓库根配置）")
    parser.add_argument("--log-level", choices=["debug", "info", "warning", "error"])
    parser.add_argument("--console-log-level", choices=["none", "error", "warning", "info", "debug"])
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--host-version", help="目标 Premiere Pro 版本（例如 25.0）")
    parser.add_argument("--premiere-version", dest="host_version", help=argparse.SUPPRESS)
    parser.add_argument("--groups", help="启用的工具组，逗号分隔（例如 core,project_edit,export）")
    parser.add_argument(
        "--enable-qe",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="启用未公开的 QE 接口（默认关闭；启用后可能导致宿主崩溃，说明书 §6.2）",
    )
    parser.add_argument("--handshake-dir", type=Path)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--no-launch", action="store_true", help="禁止按需启动宿主")
    parser.add_argument(
        "--print-example-config",
        action="store_true",
        help="打印示例配置到 stdout 后退出",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="只加载并校验配置，打印生效的配置摘要后退出",
    )
    return parser


def cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """把命令行参数转成嵌套的配置覆盖字典。"""
    overrides: dict[str, Any] = {}
    if args.log_level:
        overrides.setdefault("server", {})["log_level"] = args.log_level
    if args.console_log_level:
        overrides.setdefault("server", {})["console_log_level"] = args.console_log_level
    if args.log_dir:
        overrides.setdefault("server", {})["log_dir"] = str(args.log_dir)
    if args.host_version:
        overrides.setdefault("hosts", {}).setdefault("premiere", {})["version"] = args.host_version
    if args.groups:
        groups = [item.strip() for item in args.groups.split(",") if item.strip()]
        overrides.setdefault("tools", {})["enabled_groups"] = groups
    if args.enable_qe is not None:
        overrides.setdefault("extendscript", {})["enable_qe"] = bool(args.enable_qe)
    if args.handshake_dir:
        overrides.setdefault("bridge", {})["handshake_dir"] = str(args.handshake_dir)
    if args.checkpoint_dir:
        overrides.setdefault("safety", {})["checkpoint_dir"] = str(args.checkpoint_dir)
    if args.no_launch:
        overrides.setdefault("hosts", {}).setdefault("premiere", {})["launch_on_demand"] = False
    return overrides


def build_logger(config: Config) -> StructuredLogger:
    writable, reason = log_dir_is_writable(config.server.log_dir)
    if not writable:
        print(
            f"[{SERVER_NAME}] 警告：日志目录不可写（{reason}），本次将只输出到 stderr",
            file=sys.stderr,
        )
    return StructuredLogger(
        config.server.log_dir,
        level="error" if not writable else config.server.log_level,
        console_level=config.server.console_log_level,
        redactor=Redactor(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.print_example_config:
        print(example_toml())
        return 0

    extra_files = [args.config] if args.config else None
    config = load_config(cli_overrides=cli_overrides(args), extra_config_files=extra_files)

    if args.check_config:
        payload = config.model_dump(mode="json", exclude={"loaded_files"})
        import json

        print(json.dumps({"loaded_files": config.loaded_files, "config": payload}, ensure_ascii=False, indent=2))
        return 0

    logger = build_logger(config)
    logger.info(
        "config_loaded",
        loaded_files=config.loaded_files,
        groups=config.tools.enabled_groups,
        qe_enabled=config.extendscript.enable_qe,
        log_level=config.server.log_level,
    )
    bundle = build_bundle(config, logger=logger)
    try:
        run_stdio(bundle)
    finally:
        logger.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - 进程入口
    raise SystemExit(main())
