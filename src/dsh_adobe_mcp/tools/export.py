"""``ppro_export_*`` 工具：导出预设枚举与编码提交（说明书 §7.2、§9）。

``encode_*`` 是 T2（批量导出）且是**长任务**：确认之后立即返回 ``job_id``，
由 ``job_status`` 跟踪，不阻塞其它工具调用。
"""

from __future__ import annotations

from typing import Any

from .context import context

__all__ = [
    "ppro_export_list_presets",
    "ppro_export_encode_sequence",
    "ppro_export_encode_file",
]


async def ppro_export_list_presets(
    kind: str = "sequence",
    query: str | None = None,
) -> dict[str, Any]:
    """列出可用的编码预设（T0）。

    预设来自 AME 的 Presets 目录扫描（``export.preset_dirs``，可用配置覆盖）——
    宿主公开 API 不提供预设枚举，因此这里如实报告扫描过的目录与其中缺失的部分。
    """
    ctx = context()
    payload = ctx.list_presets(kind, query)
    warnings = []
    if not payload["presets"]:
        warnings.append(
            "没有发现预设文件：请确认 AME 已安装，或在配置 export.preset_dirs 里"
            "直接给出预设目录（.epr 文件所在目录）"
        )
    return {
        "ok": True,
        "data": payload,
        "error": None,
        "warnings": warnings,
        "host_state": ctx.current_state(),
        "undo_hint": None,
    }


async def ppro_export_encode_sequence(
    output_path: str,
    preset_path: str,
    sequence: str | None = None,
    work_area: str = "entire",
    remove_from_queue: bool = True,
    host: str | None = None,
    dry_run: bool = False,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【T2 需确认 · 长任务】把序列送入宿主/AME 编码器导出。

    确认时的影响说明会给出输出路径是否已存在（会覆盖）、预设文件是否存在。
    执行后返回 ``job_id``，用 ``job_status`` 跟踪。
    """
    return await context().invoke_job(
        "ppro_export_encode_sequence",
        {
            "output_path": output_path,
            "preset_path": preset_path,
            "sequence": sequence,
            "work_area": work_area,
            "remove_from_queue": remove_from_queue,
        },
        host=host,
        dry_run=dry_run,
        confirm_token=confirm_token,
        output_path=output_path,
    )


async def ppro_export_encode_file(
    item: str,
    output_path: str,
    preset_path: str,
    remove_from_queue: bool = True,
    host: str | None = None,
    dry_run: bool = False,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【T2 需确认 · 长任务】把单个工程条目送入编码器导出，返回 ``job_id``。"""
    return await context().invoke_job(
        "ppro_export_encode_file",
        {
            "item": item,
            "output_path": output_path,
            "preset_path": preset_path,
            "remove_from_queue": remove_from_queue,
        },
        host=host,
        dry_run=dry_run,
        confirm_token=confirm_token,
        output_path=output_path,
    )
