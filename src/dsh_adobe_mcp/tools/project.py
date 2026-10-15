"""``ppro_project_*`` 工具：工程与素材箱操作（说明书 §7.2）。

风险等级来自说明书 §7.2 的表格：``new`` / ``open`` / ``save_as`` / ``close`` 为 T2
（必须经确认令牌），``save`` / ``import`` / ``bin_*`` 为 T1，``info`` / ``bin_list`` /
``item_find`` 为 T0。
"""

from __future__ import annotations

from typing import Any

from .context import context

__all__ = [
    "ppro_project_new",
    "ppro_project_open",
    "ppro_project_save",
    "ppro_project_save_as",
    "ppro_project_close",
    "ppro_project_info",
    "ppro_project_import",
    "ppro_project_bin_create",
    "ppro_project_bin_list",
    "ppro_project_bin_move",
    "ppro_project_item_find",
]


async def ppro_project_new(
    path: str,
    host: str | None = None,
    dry_run: bool = False,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【T2 需确认】新建工程并保存到指定 .prproj 路径。

    未带 ``confirm_token`` 时**不会执行**，而是返回影响说明与一次性确认令牌。
    """
    return await context().invoke(
        "ppro_project_new", {"path": path}, host=host, dry_run=dry_run, confirm_token=confirm_token
    )


async def ppro_project_open(
    path: str,
    host: str | None = None,
    dry_run: bool = False,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【T2 需确认】打开磁盘上的 .prproj 工程。

    当前工程有未保存改动时 L1 会拒绝（避免触发宿主模态对话框），要求先 ``ppro_project_save``。
    """
    return await context().invoke(
        "ppro_project_open", {"path": path}, host=host, dry_run=dry_run, confirm_token=confirm_token
    )


async def ppro_project_save(
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """保存当前工程到它的现有路径（T1）。"""
    return await context().invoke("ppro_project_save", {}, host=host, dry_run=dry_run)


async def ppro_project_save_as(
    path: str,
    host: str | None = None,
    dry_run: bool = False,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【T2 需确认】另存工程到新路径。这会改变当前工程的路径。"""
    return await context().invoke(
        "ppro_project_save_as",
        {"path": path},
        host=host,
        dry_run=dry_run,
        confirm_token=confirm_token,
    )


async def ppro_project_close(
    confirm_discard: bool = False,
    host: str | None = None,
    dry_run: bool = False,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【T2 需确认】关闭当前工程。

    ``confirm_discard=False``（默认）时，若工程有未保存改动会被拒绝，
    避免宿主弹出"是否保存"的模态对话框把求值卡死。
    """
    return await context().invoke(
        "ppro_project_close",
        {"confirm_discard": confirm_discard},
        host=host,
        dry_run=dry_run,
        confirm_token=confirm_token,
    )


async def ppro_project_info(host: str | None = None) -> dict[str, Any]:
    """读取当前工程结构概览：名称、路径、脏标志、序列名列表、素材箱与条目数量。"""
    return await context().invoke("ppro_project_info", {}, host=host)


async def ppro_project_import(
    paths: list[str],
    bin: str | None = None,
    suppress_ui: bool = True,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """导入磁盘素材到工程（T1），可用 ``bin`` 指定目标素材箱。

    路径必须位于允许的根目录内（默认用户目录与当前工程目录），否则返回 ``PATH_NOT_ALLOWED``。
    """
    return await context().invoke(
        "ppro_project_import",
        {"paths": paths, "bin": bin, "suppress_ui": suppress_ui},
        host=host,
        dry_run=dry_run,
    )


async def ppro_project_bin_create(
    name: str,
    parent: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """新建素材箱（T1）。``parent`` 为父素材箱路径，缺省为工程根。"""
    return await context().invoke(
        "ppro_project_bin_create", {"name": name, "parent": parent}, host=host, dry_run=dry_run
    )


async def ppro_project_bin_list(
    parent: str | None = None,
    host: str | None = None,
) -> dict[str, Any]:
    """列出素材箱及其直接子项数量（T0）。``parent`` 限定只列某个素材箱下的子箱。"""
    return await context().invoke("ppro_project_bin_list", {"parent": parent}, host=host)


async def ppro_project_bin_move(
    items: list[str],
    target_bin: str,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把工程条目移动到目标素材箱（T1）。``items`` 可以是树路径或 nodeId。"""
    return await context().invoke(
        "ppro_project_bin_move",
        {"items": items, "target_bin": target_bin},
        host=host,
        dry_run=dry_run,
    )


async def ppro_project_item_find(
    query: str,
    kind: str = "any",
    limit: int = 50,
    host: str | None = None,
) -> dict[str, Any]:
    """按名称/路径子串查找工程条目（T0）。

    ``kind`` 取值 ``any`` / ``clip`` / ``bin`` / ``sequence`` / ``file``；
    返回项里的 ``path`` 可直接用于其它工具的 ``item`` 参数。
    """
    return await context().invoke(
        "ppro_project_item_find", {"query": query, "kind": kind, "limit": limit}, host=host
    )
