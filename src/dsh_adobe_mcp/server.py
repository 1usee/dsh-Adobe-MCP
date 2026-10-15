"""MCP 应用装配与 stdio 入口（说明书 §3.1、§13.2）。

L1 是唯一对外可见的层：它持有全部工具 schema、负责参数校验、风险分级、
确认令牌签发、任务生命周期与审计，但**不知道宿主的具体 API**，只发命令信封。

stdio 传输下**标准输出归协议所有**：所有日志写文件（默认），控制台日志走 stderr。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from fastmcp import FastMCP

from . import SERVER_NAME, __version__
from .bridge.service import BridgeService
from .config import Config
from .logging_setup import StructuredLogger
from .safety.policy import DEFAULT_ENABLED_GROUPS
from .tools.context import ToolContext, set_context
from .tools.registry import collect_tools, register_tools

__all__ = ["SERVER_INSTRUCTIONS", "AppBundle", "build_bundle", "run_stdio", "tool_names"]


SERVER_INSTRUCTIONS = """\
本服务器让 AI 客户端通过 CEP/ExtendScript 桥接真实驱动 Adobe Premiere Pro（Windows）。

工作方式：每个工具都是一次"结构化命令"，在宿主内执行后返回统一信封
{ok, data, error{code,message,remedy}, warnings, host_state, undo_hint}。
**失败时读 error.remedy**，它给出可执行的下一步；**写操作后读 undo_hint**，它给出撤销方式。

安全模型（务必遵守）：
* T0 只读：可直接调用；T1 增量/可逆：直接执行并返回 undo_hint；
* T2 破坏性：**不会直接执行**，而是返回 CONFIRM_REQUIRED 与影响说明 + 一次性确认令牌；
  核对影响后调用 safety_confirm_operation(confirm_token=...) 才会真正执行；
* 每个 T2 操作执行前会自动创建工程检查点（safety_list_checkpoints 可见）；
* 参数注入无法执行任意脚本：命令名必须在注册表中，参数必须通过 schema 校验。

典型链路：host_list → host_status → host_launch → host_wait_ready →
ppro_project_new/open → ppro_project_import → ppro_sequence_create →
ppro_timeline_insert_clip → ppro_export_encode_sequence → job_status。

求值超时（BRIDGE_TIMEOUT）**不代表命令失败**：宿主状态未知，必须先 host_resync，
再决定是否继续，禁止直接重试。
"""


@dataclass
class AppBundle:
    """装配产物：MCP 应用 + 工具上下文 + 配置 + 日志器。"""

    config: Config
    context: ToolContext
    mcp: FastMCP
    logger: StructuredLogger | None = None
    registered_tools: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.registered_tools is None:
            self.registered_tools = []


def build_bundle(
    config: Config,
    *,
    logger: StructuredLogger | None = None,
    bridge: BridgeService | None = None,
    include_disabled: bool = False,
) -> AppBundle:
    """装配 L1：工具上下文 + FastMCP 应用（含生命周期钩子）。"""
    ctx = ToolContext(config, logger=logger, bridge=bridge)

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        set_context(ctx)
        info = await ctx.startup()
        if logger is not None:
            logger.info("server_started", version=__version__, **info)
        try:
            yield {"context": ctx, "info": info}
        finally:
            if logger is not None:
                logger.info("server_stopping")
            await ctx.shutdown()
            set_context(None)

    mcp = FastMCP(
        name=SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        version=__version__,
        lifespan=lifespan,
    )
    registered = register_tools(mcp, ctx.enabled_groups, include_disabled=include_disabled)
    bundle = AppBundle(
        config=config, context=ctx, mcp=mcp, logger=logger, registered_tools=registered
    )
    if logger is not None:
        logger.info(
            "tools_registered",
            count=len(registered),
            groups=ctx.enabled_groups,
            tools=registered,
            default_groups=list(DEFAULT_ENABLED_GROUPS),
        )
    return bundle


def tool_names() -> list[str]:
    """全部工具名（不含启用过滤）。"""
    return list(collect_tools())


def run_stdio(bundle: AppBundle) -> None:
    """以 stdio 传输运行（DSH 通过 stdio 接入，说明书 §1.1 第 1 条）。"""
    asyncio_loop_note = "stdio"
    if bundle.logger is not None:
        bundle.logger.info("transport_selected", transport=asyncio_loop_note)
    bundle.mcp.run(transport="stdio", show_banner=False)
