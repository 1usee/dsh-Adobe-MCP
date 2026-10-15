"""``ppro_timeline_*`` 工具：时间线片段、轨道与标记（说明书 §7.2）。

片段引用（``ref``）形如 ``V:1:0``（``<V|A>:<轨道序号，1 基>:<片段序号，0 基>``），
由 :func:`ppro_timeline_list_clips` 返回，供其它工具引用。
"""

from __future__ import annotations

from typing import Any

from .context import context

__all__ = [
    "ppro_timeline_list_clips",
    "ppro_timeline_clip_info",
    "ppro_timeline_insert_clip",
    "ppro_timeline_overwrite_clip",
    "ppro_timeline_move_clip",
    "ppro_timeline_trim_clip",
    "ppro_timeline_remove_clip",
    "ppro_timeline_razor",
    "ppro_timeline_add_track",
    "ppro_timeline_remove_track",
    "ppro_timeline_add_marker",
    "ppro_timeline_list_markers",
]


async def ppro_timeline_list_clips(
    sequence: str | None = None,
    track_type: str = "all",
    track_index: int | None = None,
    host: str | None = None,
) -> dict[str, Any]:
    """列出序列时间线片段（T0）。``track_type`` 取 ``video`` / ``audio`` / ``all``。

    返回的每个片段都带 ``ref``，可直接用于 move/trim/remove/effects 等工具。
    """
    return await context().invoke(
        "ppro_timeline_list_clips",
        {"sequence": sequence, "track_type": track_type, "track_index": track_index},
        host=host,
    )


async def ppro_timeline_clip_info(
    ref: str,
    sequence: str | None = None,
    host: str | None = None,
) -> dict[str, Any]:
    """读取单个时间线片段的详细信息（T0）：名称、起止、源入出点、速度、效果组件数。"""
    return await context().invoke(
        "ppro_timeline_clip_info", {"ref": ref, "sequence": sequence}, host=host
    )


async def ppro_timeline_insert_clip(
    item: str,
    at_s: float = 0,
    track_type: str = "video",
    track_index: int = 1,
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把工程条目插入到指定轨道时间点（T1，插入模式会让后续片段右移）。

    ``item`` 用素材箱树路径或 nodeId；先用 ``ppro_project_item_find`` 拿到准确值。
    """
    return await context().invoke(
        "ppro_timeline_insert_clip",
        {
            "item": item,
            "at_s": at_s,
            "track_type": track_type,
            "track_index": track_index,
            "sequence": sequence,
        },
        host=host,
        dry_run=dry_run,
    )


async def ppro_timeline_overwrite_clip(
    item: str,
    at_s: float = 0,
    track_type: str = "video",
    track_index: int = 1,
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把工程条目覆盖写入指定轨道时间点（T1，会替换该位置原有内容）。"""
    return await context().invoke(
        "ppro_timeline_overwrite_clip",
        {
            "item": item,
            "at_s": at_s,
            "track_type": track_type,
            "track_index": track_index,
            "sequence": sequence,
        },
        host=host,
        dry_run=dry_run,
    )


async def ppro_timeline_move_clip(
    ref: str,
    to_s: float,
    to_track_type: str | None = None,
    to_track_index: int | None = None,
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """移动片段到新的时间点（T1），可选跨轨道。

    优先使用公开 API；不可用时在启用 QE 的前提下走 QE（未启用则返回 ``UNSUPPORTED_API``）。
    """
    return await context().invoke(
        "ppro_timeline_move_clip",
        {
            "ref": ref,
            "to_s": to_s,
            "to_track_type": to_track_type,
            "to_track_index": to_track_index,
            "sequence": sequence,
        },
        host=host,
        dry_run=dry_run,
    )


async def ppro_timeline_trim_clip(
    ref: str,
    start_s: float | None = None,
    end_s: float | None = None,
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """调整片段在时间线上的起止点（T1）。至少给出 ``start_s`` 或 ``end_s`` 之一。"""
    return await context().invoke(
        "ppro_timeline_trim_clip",
        {"ref": ref, "start_s": start_s, "end_s": end_s, "sequence": sequence},
        host=host,
        dry_run=dry_run,
    )


async def ppro_timeline_remove_clip(
    ref: str,
    ripple: bool = False,
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【T2 需确认】从时间线删除片段（不删除工程素材）。``ripple=True`` 时后续片段左移。

    未带 ``confirm_token`` 时会先返回被删片段的当前信息（名称/起止）供核对。
    """
    return await context().invoke(
        "ppro_timeline_remove_clip",
        {"ref": ref, "ripple": ripple, "sequence": sequence},
        host=host,
        dry_run=dry_run,
        confirm_token=confirm_token,
    )


async def ppro_timeline_razor(
    at_s: float,
    track_type: str = "all",
    track_index: int | None = None,
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【T2 需确认】在指定时间点切分片段。

    **依赖未公开的 QE 接口**（``extendscript.enable_qe``，默认关闭）：这是 Adobe
    未文档化的接口，异常时可能导致宿主崩溃，因此默认禁用且归为 T2。
    """
    return await context().invoke(
        "ppro_timeline_razor",
        {"at_s": at_s, "track_type": track_type, "track_index": track_index, "sequence": sequence},
        host=host,
        dry_run=dry_run,
        confirm_token=confirm_token,
    )


async def ppro_timeline_add_track(
    track_type: str = "video",
    position: int | None = None,
    name: str | None = None,
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """新增视频/音频轨道（T1）。``position`` 缺省表示追加到末尾。"""
    return await context().invoke(
        "ppro_timeline_add_track",
        {"track_type": track_type, "position": position, "name": name, "sequence": sequence},
        host=host,
        dry_run=dry_run,
    )


async def ppro_timeline_remove_track(
    track_index: int,
    track_type: str = "video",
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【T2 需确认】删除轨道及其上的全部片段。

    确认前会读出该轨道上的片段数量与名称，作为影响说明的一部分。
    """
    return await context().invoke(
        "ppro_timeline_remove_track",
        {"track_index": track_index, "track_type": track_type, "sequence": sequence},
        host=host,
        dry_run=dry_run,
        confirm_token=confirm_token,
    )


async def ppro_timeline_add_marker(
    at_s: float,
    name: str | None = None,
    comment: str | None = None,
    duration_s: float | None = None,
    type: str = "comment",
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """在序列上添加标记（T1）。``type`` 取 ``comment`` / ``chapter`` / ``segmentation`` / ``web``。"""
    return await context().invoke(
        "ppro_timeline_add_marker",
        {
            "at_s": at_s,
            "name": name,
            "comment": comment,
            "duration_s": duration_s,
            "type": type,
            "sequence": sequence,
        },
        host=host,
        dry_run=dry_run,
    )


async def ppro_timeline_list_markers(
    sequence: str | None = None,
    host: str | None = None,
) -> dict[str, Any]:
    """列出序列上的全部标记（T0）。"""
    return await context().invoke(
        "ppro_timeline_list_markers", {"sequence": sequence}, host=host
    )
