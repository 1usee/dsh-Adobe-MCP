"""``ppro_sequence_*`` 工具：序列的创建、查询与设置（说明书 §7.2）。

说明：说明书 §7.2 把整个 ``ppro_sequence_`` 命名空间标为 T1（即使 ``list``/``info``
实际是只读查询）。这里严格遵守该表格，未擅自降级；只读项在返回里会说明"未改变状态"。
"""

from __future__ import annotations

from typing import Any

from .context import context

__all__ = [
    "ppro_sequence_create",
    "ppro_sequence_create_from_clips",
    "ppro_sequence_list",
    "ppro_sequence_info",
    "ppro_sequence_set_active",
    "ppro_sequence_set_in_out",
    "ppro_sequence_set_work_area",
]


async def ppro_sequence_create(
    name: str,
    preset_path: str | None = None,
    width: int | None = None,
    height: int | None = None,
    frame_rate: float | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """新建序列（T1）。

    推荐给出 ``preset_path``（.sqpreset）。宿主公开 API 无法在无预设时设定
    width/height/frame_rate —— 此时本工具会返回 ``UNSUPPORTED_API`` 并说明补救方式，
    而不会悄悄创建一个设置不对的序列。
    """
    return await context().invoke(
        "ppro_sequence_create",
        {
            "name": name,
            "preset_path": preset_path,
            "width": width,
            "height": height,
            "frame_rate": frame_rate,
        },
        host=host,
        dry_run=dry_run,
    )


async def ppro_sequence_create_from_clips(
    name: str,
    items: list[str],
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """由一组素材创建序列（T1）。序列设置按首个素材的规格匹配，顺序即时间线顺序。"""
    return await context().invoke(
        "ppro_sequence_create_from_clips",
        {"name": name, "items": items},
        host=host,
        dry_run=dry_run,
    )


async def ppro_sequence_list(host: str | None = None) -> dict[str, Any]:
    """列出工程内全部序列及其轨道数、时长（只读查询，未改变宿主状态）。"""
    return await context().invoke("ppro_sequence_list", {}, host=host)


async def ppro_sequence_info(
    sequence: str | None = None,
    host: str | None = None,
) -> dict[str, Any]:
    """读取指定（或活动）序列的详细信息（只读查询）。"""
    return await context().invoke("ppro_sequence_info", {"sequence": sequence}, host=host)


async def ppro_sequence_set_active(
    sequence: str,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把指定序列设为活动序列（T1）。"""
    return await context().invoke(
        "ppro_sequence_set_active", {"sequence": sequence}, host=host, dry_run=dry_run
    )


async def ppro_sequence_set_in_out(
    sequence: str | None = None,
    in_s: float | None = None,
    out_s: float | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """设置序列入点/出点（T1）。至少给出 ``in_s`` 或 ``out_s`` 之一。"""
    return await context().invoke(
        "ppro_sequence_set_in_out",
        {"sequence": sequence, "in_s": in_s, "out_s": out_s},
        host=host,
        dry_run=dry_run,
    )


async def ppro_sequence_set_work_area(
    start_s: float,
    end_s: float,
    sequence: str | None = None,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """设置序列工作区（T1）。

    公开 DOM 没有这个能力：只有在 ``extendscript.enable_qe = true`` 时才可能完成，
    否则返回 ``UNSUPPORTED_API`` 并说明如何启用（QE 是未公开接口，可能崩溃宿主）。
    """
    return await context().invoke(
        "ppro_sequence_set_work_area",
        {"start_s": start_s, "end_s": end_s, "sequence": sequence},
        host=host,
        dry_run=dry_run,
    )
