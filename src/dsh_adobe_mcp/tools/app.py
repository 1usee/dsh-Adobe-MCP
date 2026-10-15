"""``ppro_app_*`` 工具：宿主应用层的信息读取与播放头控制（说明书 §7.2）。"""

from __future__ import annotations

from typing import Any

from .context import context

__all__ = ["ppro_app_info", "ppro_app_get_state", "ppro_app_set_playhead"]


async def ppro_app_info(host: str | None = None) -> dict[str, Any]:
    """读取宿主应用信息：版本、语言、当前工程名称与路径、是否存在未保存改动。

    这是**不改变宿主任何状态**的只读调用，适合在每次任务开始前确认环境。
    """
    return await context().invoke("ppro_app_info", {}, host=host)


async def ppro_app_get_state(
    include_selection: bool = True,
    host: str | None = None,
) -> dict[str, Any]:
    """读取当前编辑状态：活动序列、播放头、入出点、选中项。

    选中项依赖未公开的 QE 接口：未启用 ``extendscript.enable_qe`` 时返回空数组
    并给出 warning，而不是假装没有选中的东西。
    """
    return await context().invoke(
        "ppro_app_get_state", {"include_selection": include_selection}, host=host
    )


async def ppro_app_set_playhead(
    seconds: float,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把播放头移动到指定秒数（相对活动序列起点）。

    ``dry_run=True`` 时只校验活动序列与时间是否合法，不移动播放头。
    """
    return await context().invoke(
        "ppro_app_set_playhead", {"seconds": seconds}, host=host, dry_run=dry_run
    )
