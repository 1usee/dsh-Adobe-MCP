"""任务模型（说明书 §9）。

导出、代理生成这类操作耗时从数十秒到数小时，MCP 工具调用不应阻塞。
提交后立即返回 ``job_id``，由 ``job_*`` 工具查询状态。

状态机：``pending`` → ``running`` → ``succeeded`` / ``failed`` / ``cancelled``；
无法判定时为 ``unknown``——**明确返回 unknown 而不猜测**（说明书 §9.2）。

进度来源按可靠性排序：宿主编码器接口查询 > 监视输出文件增长 > 监视输出文件存在性。
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

__all__ = ["JobState", "ProgressSource", "Job", "JobEvent"]


class JobState(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    unknown = "unknown"

    @property
    def terminal(self) -> bool:
        return self in (JobState.succeeded, JobState.failed, JobState.cancelled)


class ProgressSource(str, Enum):
    encoder = "encoder"
    output_growth = "output_growth"
    output_exists = "output_exists"
    unknown = "unknown"


class JobEvent(BaseModel):
    ts: float
    state: JobState
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class Job(BaseModel):
    """一个长任务。"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    tool: str
    host: str
    command: str | None = None
    state: JobState = JobState.pending
    created_at: float = Field(default_factory=time.time)
    started_at: float | None = None
    updated_at: float = Field(default_factory=time.time)
    finished_at: float | None = None

    output_path: str | None = None
    output_size: int | None = None
    progress: float | None = Field(
        default=None, description="0..1；无法判定时为 None"
    )
    progress_source: ProgressSource = ProgressSource.unknown

    encoder_job_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    message: str = ""
    trace_id: str | None = None
    idempotency_key: str | None = None
    events: list[JobEvent] = Field(default_factory=list)

    # ---- 便捷方法 -------------------------------------------------------
    def add_event(self, state: JobState, message: str = "", **detail: Any) -> None:
        self.state = state
        self.updated_at = time.time()
        self.message = message
        if state == JobState.running and self.started_at is None:
            self.started_at = self.updated_at
        if state.terminal:
            self.finished_at = self.updated_at
        self.events.append(JobEvent(ts=self.updated_at, state=state, message=message, detail=detail))

    def to_public(self, *, include_events: bool = False) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        if not include_events:
            data.pop("events", None)
        if self.progress is None:
            data["progress_display"] = "unknown"
        else:
            data["progress_display"] = f"{self.progress * 100:.1f}%"
        return data
