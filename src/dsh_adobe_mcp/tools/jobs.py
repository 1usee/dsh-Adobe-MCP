"""``job_*`` 工具：长任务查询与取消（说明书 §7.2、§9）。

任务状态既在内存也在磁盘（快照 + 追加事件日志），因此 MCP 服务器重启后
仍能报告历史任务；重启时无法确认宿主侧状态的任务会被明确标为 ``unknown``。
"""

from __future__ import annotations

from typing import Any

from .context import context

__all__ = ["job_list", "job_status", "job_cancel"]


async def job_list(limit: int = 50, host: str | None = None) -> dict[str, Any]:
    """列出长任务（最新的在最前），并给出按状态统计的摘要。"""
    return await context().job_list(limit, host)


async def job_status(job_id: str, host: str | None = None) -> dict[str, Any]:
    """查询单个长任务：状态、进度、产物路径、日志与说明。

    进度来源按可靠性排序（宿主编码器接口 > 输出文件增长 > 存在性）；
    无法判定时进度为 ``unknown``——**不猜测**。
    """
    return await context().job_status(job_id, host)


async def job_cancel(job_id: str, host: str | None = None) -> dict[str, Any]:
    """尽力取消长任务。

    排队中的任务可以真正取消；已经被编码器受理的任务通常无法取消，
    此时返回 ``JOB_NOT_CANCELLABLE`` 并说明该怎么做，而不是假装成功。
    """
    return await context().job_cancel(job_id, host)
