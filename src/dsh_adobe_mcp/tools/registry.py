"""工具注册：把 ``tools/*.py`` 里的函数按工具组挂到 FastMCP 上（说明书 §7.3、§14.2）。

两条硬性规则在这里落地：

1. **完整性**：56 个工具与 ``safety/policy.py`` 的风险表必须一一对应。
   少实现一个、或多出一个没登记的，导入时就报错——而不是等客户端调用时才 404。
2. **渐进式暴露**：只有 ``tools.enabled_groups`` 里的工具组会注册到 MCP，
   避免 56 个工具一次性挤占客户端上下文（说明书 §7.3）。
"""

from __future__ import annotations

import functools
from typing import Any, Callable

from ..errors import ToolError, describe_exception
from ..result import failure
from ..safety.policy import POLICY, enabled_tools as policy_enabled_tools, iter_policies
from . import app, effects, export, host, jobs, media, project, safety, sequence, timeline

__all__ = [
    "TOOL_MODULES",
    "collect_tools",
    "validate_completeness",
    "register_tools",
    "schema_catalog",
    "schema_catalog_sync",
    "guarded",
]

TOOL_MODULES = (host, app, project, sequence, timeline, effects, media, export, jobs, safety)


def guarded(function: Callable[..., Any]) -> Callable[..., Any]:
    """给工具函数套一层兜底：**任何**异常都变成统一返回信封。

    为什么需要：MCP 服务器一旦把裸异常抛给框架，客户端只会看到一个协议级错误，
    既没有 ``code`` 也没有 ``remedy``——恰恰是本设计最想避免的"AI 拿到错误却不知道下一步"。
    走 :meth:`ToolContext.invoke` 的工具已经有完整的异常处理，这一层是为了覆盖
    L1 专属工具与任何遗漏路径（纵深防御，代价是一次函数调用）。
    """

    @functools.wraps(function)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await function(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 这里就是要兜住一切
            payload = describe_exception(exc)
            try:
                from .context import context

                ctx = context()
                state = ctx.current_state()
                if ctx.logger is not None:
                    ctx.logger.error(
                        "tool_unhandled_exception",
                        tool=function.__name__,
                        error=f"{type(exc).__name__}: {exc}",
                    )
            except Exception:  # noqa: BLE001 - 上下文不可用时也不能再抛
                state = {}
            if isinstance(exc, ToolError) or payload.get("code") != "INTERNAL_ERROR":
                return failure(
                    payload["code"],
                    message=payload["message"],
                    remedy=payload["remedy"],
                    detail=payload.get("detail"),
                    state=state,
                )
            return failure(
                "INTERNAL_ERROR",
                message=f"{type(exc).__name__}: {exc}",
                detail={"tool": function.__name__},
                state=state,
            )

    return wrapper


def collect_tools() -> dict[str, Callable[..., Any]]:
    """收集全部工具函数（键为工具名）。"""
    found: dict[str, Callable[..., Any]] = {}
    for module in TOOL_MODULES:
        for name in getattr(module, "__all__", ()):
            function = getattr(module, name, None)
            if function is None:
                raise RuntimeError(f"{module.__name__} 的 __all__ 声明了不存在的名字：{name}")
            if not callable(function):
                continue
            if name in found:
                raise RuntimeError(f"工具名重复：{name}（{module.__name__}）")
            found[name] = function
    validate_completeness(found)
    return found


def validate_completeness(found: dict[str, Callable[..., Any]] | None = None) -> None:
    """工具函数集合必须与风险表一一对应。"""
    found = found if found is not None else collect_tools()
    declared = set(POLICY)
    implemented = set(found)
    missing = sorted(declared - implemented)
    extra = sorted(implemented - declared)
    if missing or extra:
        details = []
        if missing:
            details.append(f"风险表声明但未实现：{missing}")
        if extra:
            details.append(f"实现了但未登记风险等级：{extra}")
        raise RuntimeError("工具集合与风险表不一致 —— " + "；".join(details))


def register_tools(mcp: Any, enabled_groups: list[str], *, include_disabled: bool = False) -> list[str]:
    """把启用的工具注册到 FastMCP；返回注册的工具名（按契约声明顺序）。"""
    functions = collect_tools()
    if include_disabled:
        names = [policy.name for policy in iter_policies()]
    else:
        names = policy_enabled_tools(enabled_groups)
    for name in names:
        mcp.tool(guarded(functions[name]), name=name)
    return names


async def schema_catalog(*, include_disabled: bool = True) -> dict[str, dict[str, Any]]:
    """返回 ``工具名 → JSON schema``，供文档生成与快照测试使用。"""
    from fastmcp import FastMCP

    functions = collect_tools()
    mcp = FastMCP(name="schema-catalog")
    names = (
        [policy.name for policy in iter_policies()]
        if include_disabled
        else policy_enabled_tools(list(POLICY))
    )
    for name in names:
        mcp.tool(guarded(functions[name]), name=name)
    tools = await mcp.get_tools()
    catalog: dict[str, dict[str, Any]] = {}
    for name, tool in tools.items():
        schema = getattr(tool, "parameters", None)
        catalog[name] = dict(schema) if isinstance(schema, dict) else {}
    return catalog


def schema_catalog_sync(*, include_disabled: bool = True) -> dict[str, dict[str, Any]]:
    """``schema_catalog`` 的同步包装（脚本与快照测试用）。"""
    import asyncio

    return asyncio.run(schema_catalog(include_disabled=include_disabled))
