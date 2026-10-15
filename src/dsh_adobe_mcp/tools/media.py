"""``ppro_media_*`` 工具：素材探测、重新链接与代理生成（说明书 §7.2）。"""

from __future__ import annotations

from typing import Any

from .context import context

__all__ = ["ppro_media_probe", "ppro_media_relink", "ppro_media_generate_proxies"]


async def ppro_media_probe(item: str, host: str | None = None) -> dict[str, Any]:
    """探测工程条目对应的媒体信息（T0）：路径、时长、帧率、尺寸、音视频轨、是否离线。

    宿主公开 API 覆盖不到的字段会出现在 ``unavailable`` 列表里 —— 明确说"拿不到"，
    而不是用猜测值填充。
    """
    return await context().invoke("ppro_media_probe", {"item": item}, host=host)


async def ppro_media_relink(
    item: str,
    new_path: str,
    override_checks: bool = False,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把离线素材重新链接到新路径（T1）。返回里带原路径，便于回退。

    ``override_checks=True`` 会跳过宿主的媒体一致性校验（慎用）。
    """
    return await context().invoke(
        "ppro_media_relink",
        {"item": item, "new_path": new_path, "override_checks": override_checks},
        host=host,
        dry_run=dry_run,
    )


async def ppro_media_generate_proxies(
    items: list[str],
    preset_path: str | None = None,
    ingest: bool = True,
    host: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """为指定素材生成/附加代理（T1，**长任务**）。

    调用立即返回 ``job_id``，进度用 ``job_status`` 查询：
    宿主编码器能报告进度时用它，否则按输出文件增长/存在性判定，无法判定时返回 ``unknown``。
    """
    return await context().invoke_job(
        "ppro_media_generate_proxies",
        {"items": items, "preset_path": preset_path, "ingest": ingest},
        host=host,
        dry_run=dry_run,
    )
