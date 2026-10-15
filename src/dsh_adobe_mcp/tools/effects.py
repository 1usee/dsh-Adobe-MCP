"""``ppro_effects_*`` 工具：效果与参数（说明书 §7.2、§6.2）。

分层策略：**效果参数读写**属于公开 DOM（有官方文档保证），而**施加效果/转场**需要
未公开的 QE 接口，因此默认禁用、归为 T1 但要先过 QE 门禁。
"""

from __future__ import annotations

from typing import Any

from .context import context

__all__ = [
    "ppro_effects_list_available",
    "ppro_effects_apply",
    "ppro_effects_get_params",
    "ppro_effects_set_param",
    "ppro_effects_apply_transition",
]


async def ppro_effects_list_available(
    target: str = "clip",
    query: str | None = None,
    host: str | None = None,
) -> dict[str, Any]:
    """列出宿主可用的视频/音频效果（T0）。

    ``target`` 取 ``clip`` / ``video_track`` / ``audio_track`` / ``sequence`` / ``master``；
    ``query`` 为名称子串过滤。
    """
    return await context().invoke(
        "ppro_effects_list_available", {"target": target, "query": query}, host=host
    )


async def ppro_effects_apply(
    ref: str,
    effect: str,
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """给片段施加效果（T1，**依赖未公开的 QE 接口**，默认禁用）。

    公开 DOM 只能读写已有参数，无法添加效果；因此未启用 ``extendscript.enable_qe`` 时
    返回 ``QE_DISABLED``，并在启用时随每次调用返回风险警告。
    """
    return await context().invoke(
        "ppro_effects_apply",
        {"ref": ref, "effect": effect, "sequence": sequence},
        host=host,
        dry_run=dry_run,
    )


async def ppro_effects_get_params(
    ref: str,
    sequence: str | None = None,
    host: str | None = None,
) -> dict[str, Any]:
    """读取片段上已有效果组件及其参数（T0，公开 DOM）。"""
    return await context().invoke(
        "ppro_effects_get_params", {"ref": ref, "sequence": sequence}, host=host
    )


async def ppro_effects_set_param(
    ref: str,
    param: str,
    value: Any,
    component: str | None = None,
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """设置片段效果参数值（T1，公开 DOM）。返回里带原值，便于撤销。

    ``component`` 缺省时使用第一个可写组件；``param`` 可以是参数名或 matchName。
    """
    return await context().invoke(
        "ppro_effects_set_param",
        {"ref": ref, "param": param, "value": value, "component": component, "sequence": sequence},
        host=host,
        dry_run=dry_run,
    )


async def ppro_effects_apply_transition(
    ref: str,
    transition: str,
    at: str = "start",
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """给片段添加转场（T1，**依赖未公开的 QE 接口**，默认禁用）。

    ``at`` 取 ``start``（片段头）或 ``end``（片段尾）。
    """
    return await context().invoke(
        "ppro_effects_apply_transition",
        {"ref": ref, "transition": transition, "at": at, "sequence": sequence},
        host=host,
        dry_run=dry_run,
    )
