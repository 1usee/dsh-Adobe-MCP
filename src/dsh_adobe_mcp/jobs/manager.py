"""长任务管理器（说明书 §9）。

* **提交**：长操作（导出、代理生成）返回 ``job_id`` 而非阻塞等待；
* **并发上限**：每宿主同时最多一个长任务，超出时**入队**而非并发（说明书 §9.2）；
* **进度**：按可靠性排序取源——宿主编码器接口 > 输出文件增长 > 输出文件存在性；
  无法确定时返回 ``unknown`` 而**不猜测**（只有编码器接口缺位时，才用"文件大小稳定"
  这一显式标注的推断，并在结果里写清 ``inferred: true``）；
* **持久化**：状态既存内存，也落盘（快照 + 追加事件日志），使 MCP 服务器重启后仍能报告历史任务；
* **与求值锁的关系**（§9.3）："在途求值"只持续到提交那一刻，提交后锁即释放，
  因此长任务不违反约束 C-2。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from .models import Job, JobState, ProgressSource

__all__ = ["JobManager", "JobNotFound", "JobQueueFull"]


class JobNotFound(Exception):
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"找不到任务：{job_id}")


class JobQueueFull(Exception):
    def __init__(self, host: str, limit: int) -> None:
        self.host = host
        self.limit = limit
        super().__init__(f"宿主 {host} 的排队任务已达上限 {limit}")


SubmitCallable = Callable[[], Awaitable[Mapping[str, Any]]]
ProbeCallable = Callable[[Job], Awaitable[Mapping[str, Any] | None]]
CancelCallable = Callable[[Job], Awaitable[bool]]


class JobManager:
    """任务注册表 + 每宿主串行执行 + 状态轮询。"""

    def __init__(
        self,
        *,
        state_dir: Path,
        poll_interval_s: float = 5,
        max_concurrent_per_host: int = 1,
        max_queued_per_host: int = 8,
        logger: Any = None,
        clock: Callable[[], float] | None = None,
        probe: ProbeCallable | None = None,
        settle_polls: int = 3,
        persist: bool = True,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.poll_interval_s = float(poll_interval_s)
        self.max_concurrent_per_host = max(1, int(max_concurrent_per_host))
        self.max_queued_per_host = max(1, int(max_queued_per_host))
        self.logger = logger
        self._clock = clock or time.time
        self._probe = probe
        self._settle_polls = max(1, settle_polls)
        self._persist_enabled = persist

        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._queue: dict[str, list[str]] = {}
        self._active: dict[str, set[str]] = {}
        self._callables: dict[str, SubmitCallable] = {}
        self._stable_polls: dict[str, int] = {}
        self._last_size: dict[str, int] = {}
        self._poll_task: asyncio.Task[None] | None = None
        self._stopping = False

    # ---- 生命周期 ------------------------------------------------------
    @property
    def snapshot_path(self) -> Path:
        return self.state_dir / "jobs.json"

    def _event_log_path(self) -> Path:
        day = time.strftime("%Y-%m-%d", time.localtime(self._clock()))
        return self.state_dir / f"jobs-{day}.jsonl"

    async def start(self) -> None:
        self._load()
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop(), name="job-poll")

    async def stop(self) -> None:
        self._stopping = True
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
            self._poll_task = None
        for task in list(self._tasks.values()):
            task.cancel()
        self._persist()

    # ---- 提交 ----------------------------------------------------------
    def submit(
        self,
        *,
        tool: str,
        host: str,
        command: str | None,
        parameters: Mapping[str, Any],
        submit_callable: SubmitCallable,
        output_path: str | None = None,
        trace_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Job:
        """登记一个长任务并开始执行（或入队）。"""
        running = self._running_count(host)
        queued = len(self._queue.get(host, []))
        if running >= self.max_concurrent_per_host and queued >= self.max_queued_per_host:
            raise JobQueueFull(host, self.max_queued_per_host)

        job = Job(
            tool=tool,
            host=host,
            command=command,
            parameters=dict(parameters),
            output_path=output_path,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
        )
        job.add_event(JobState.pending, "已登记，等待执行")
        self._jobs[job.id] = job
        self._order.append(job.id)
        self._append_event(job, "submitted", {"tool": tool, "host": host})
        self._persist()

        if self.logger is not None:
            self.logger.info("job_submitted", job_id=job.id, tool=tool, host=host)

        if running >= self.max_concurrent_per_host:
            self._queue.setdefault(host, []).append(job.id)
            self._callables[job.id] = submit_callable
            if self.logger is not None:
                self.logger.info("job_queued", job_id=job.id, host=host, queue_len=queued + 1)
            return job

        self._start_job(job, submit_callable)
        return job

    def _start_job(self, job: Job, submit_callable: SubmitCallable) -> None:
        self._active.setdefault(job.host, set()).add(job.id)
        self._callables[job.id] = submit_callable
        task = asyncio.create_task(self._run_job(job, submit_callable), name=f"job-{job.id}")
        self._tasks[job.id] = task

    async def _run_job(self, job: Job, submit_callable: SubmitCallable) -> None:
        try:
            await self._submit_job(job, submit_callable)
        finally:
            self._active.get(job.host, set()).discard(job.id)
            self._callables.pop(job.id, None)
            try:
                self._promote_queued()
            except Exception as exc:  # noqa: BLE001 - 队列推进失败不得让服务器崩
                if self.logger is not None:
                    self.logger.error("job_queue_promote_failed", error=str(exc))

    async def _submit_job(self, job: Job, submit_callable: SubmitCallable) -> None:
        job.add_event(JobState.running, "已提交给宿主")
        self._persist()
        try:
            result = await submit_callable()
        except asyncio.CancelledError:
            job.add_event(JobState.cancelled, "任务在提交阶段被取消")
            self._append_event(job, "cancelled", {})
            raise
        except Exception as exc:  # noqa: BLE001 - 任何异常都要落成任务失败，而不是让服务器崩
            job.error = {
                "code": getattr(exc, "code", "HOST_ERROR"),
                "message": str(exc),
                "remedy": getattr(exc, "remedy", "查看服务器日志中同 trace_id 的记录"),
            }
            job.add_event(JobState.failed, f"提交失败：{exc}")
            self._append_event(job, "failed", job.error)
            self._persist()
            if self.logger is not None:
                self.logger.error("job_submit_failed", job_id=job.id, error=str(exc))
            return

        payload = dict(result or {})
        job.result = payload
        encoder_job_id = payload.get("job_id") or payload.get("encoder_job_id")
        if encoder_job_id is not None:
            job.encoder_job_id = str(encoder_job_id)
        # 输出路径：调用者给出的路径优先（它才是我们要监视增长的那个文件），
        # 宿主回填的路径只在调用者没给时用于补全。
        if payload.get("output_path") and not job.output_path:
            job.output_path = str(payload["output_path"])
        job.add_event(
            JobState.running,
            "宿主已受理，等待编码完成",
            encoder_job_id=job.encoder_job_id,
        )
        self._append_event(job, "accepted", {"encoder_job_id": job.encoder_job_id})
        self._persist()
        if self.logger is not None:
            self.logger.info(
                "job_accepted", job_id=job.id, encoder_job_id=job.encoder_job_id
            )

    def _running_count(self, host: str) -> int:
        return len(self._active.get(host, ()))

    def _promote_queued(self) -> None:
        for host, waiting in list(self._queue.items()):
            while waiting and self._running_count(host) < self.max_concurrent_per_host:
                job_id = waiting.pop(0)
                job = self._jobs.get(job_id)
                callable_ = self._callables.get(job_id)
                if job is None or job.state.terminal or callable_ is None:
                    continue
                self._start_job(job, callable_)
        self._queue = {host: waiting for host, waiting in self._queue.items() if waiting}

    # ---- 查询 ----------------------------------------------------------
    def get(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFound(job_id)
        return job

    def list(self, *, limit: int = 50, host: str | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for job_id in reversed(self._order):
            job = self._jobs[job_id]
            if host is not None and job.host != host:
                continue
            items.append(job.to_public())
            if len(items) >= limit:
                break
        return items

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for job in self._jobs.values():
            counts[job.state.value] = counts.get(job.state.value, 0) + 1
        return {
            "total": len(self._jobs),
            "by_state": counts,
            "queued": {host: len(items) for host, items in self._queue.items() if items},
        }

    def status(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        data = job.to_public()
        data["queued"] = job_id in self._queue.get(job.host, [])
        data["notes"] = self._notes_for(job)
        return data

    def _notes_for(self, job: Job) -> list[str]:
        notes: list[str] = []
        if job.state is JobState.unknown:
            notes.append(
                "宿主侧没有提供可判定的状态；请确认宿主/AME 的编码队列与输出文件"
            )
        if job.result and job.result.get("inferred"):
            notes.append(
                "本任务的成功状态是根据输出文件大小稳定推断的，不是编码器报告的（inferred=true）"
            )
        if job.state is JobState.running and job.progress is None:
            notes.append("宿主未提供进度，进度显示为 unknown（不猜测）")
        return notes

    # ---- 取消 ----------------------------------------------------------
    async def cancel(self, job_id: str, cancel_callable: CancelCallable | None = None) -> dict[str, Any]:
        job = self.get(job_id)
        if job.state.terminal:
            return {
                "cancelled": False,
                "state": job.state.value,
                "message": "任务已结束，无需取消",
            }

        waiting = self._queue.get(job.host, [])
        if job_id in waiting:
            waiting.remove(job_id)
            job.add_event(JobState.cancelled, "任务在排队阶段被取消")
            self._append_event(job, "cancelled", {"stage": "queued"})
            self._persist()
            return {"cancelled": True, "state": job.state.value, "stage": "queued"}

        if cancel_callable is not None:
            try:
                accepted = await cancel_callable(job)
            except Exception as exc:  # noqa: BLE001
                accepted = False
                job.message = f"取消请求失败：{exc}"
        else:
            accepted = False

        if accepted:
            job.add_event(JobState.cancelled, "宿主已接受取消请求")
            self._append_event(job, "cancelled", {"stage": "running"})
        else:
            job.add_event(
                JobState.running,
                "取消请求不被支持；任务仍在继续（job_cancel 是尽力而为）",
            )
            # add_event 会覆盖 message，因此面向调用者的说明放在它之后。
            job.message = "宿主/编码器不支持取消该任务；任务仍在继续（job_cancel 是尽力而为）"
        self._persist()
        return {
            "cancelled": accepted,
            "state": job.state.value,
            "message": job.message,
        }

    # ---- 轮询 ----------------------------------------------------------
    async def _poll_loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self.poll_interval_s)
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 轮询不得让服务器崩
                if self.logger is not None:
                    self.logger.error("job_poll_failed", error=str(exc))

    async def poll_once(self) -> None:
        """执行一轮状态刷新（测试直接调用它，避免依赖真实时间）。"""
        for job_id in list(self._order):
            job = self._jobs[job_id]
            if job.state.terminal:
                continue
            if job.state is JobState.pending:
                # 排队中的任务还没有被宿主受理，没有可查询的进度。
                continue
            info: Mapping[str, Any] | None = None
            if self._probe is not None and job.encoder_job_id:
                try:
                    info = await self._probe(job)
                except Exception as exc:  # noqa: BLE001
                    if self.logger is not None:
                        self.logger.warning(
                            "job_probe_failed", job_id=job.id, error=str(exc)
                        )
                    info = None
            self._apply_progress(job, info)
        self._promote_queued()
        self._persist()

    def _apply_progress(self, job: Job, info: Mapping[str, Any] | None) -> None:
        state_text = None
        if info:
            state_text = info.get("state")
            if info.get("progress") is not None:
                try:
                    job.progress = max(0.0, min(1.0, float(info["progress"])))
                    job.progress_source = ProgressSource.encoder
                except (TypeError, ValueError):
                    pass
            if info.get("message"):
                job.message = str(info["message"])

        if state_text:
            normalized = str(state_text).lower()
            if normalized in ("succeeded", "success", "done", "complete", "completed"):
                # 编码器报告完成 → 进度就是 100%，不必等 probe 给出数值
                job.progress = 1.0
                job.progress_source = ProgressSource.encoder
                job.add_event(JobState.succeeded, job.message or "编码器报告完成")
                self._append_event(job, "succeeded", {"source": "encoder"})
                return
            if normalized in ("failed", "error"):
                job.error = job.error or {
                    "code": "HOST_ERROR",
                    "message": job.message or "编码器报告失败",
                    "remedy": "在宿主/AME 的编码队列中查看错误详情，确认预设与输出路径可用",
                }
                job.add_event(JobState.failed, job.message or "编码器报告失败")
                self._append_event(job, "failed", job.error)
                return
            if normalized in ("cancelled", "canceled"):
                job.add_event(JobState.cancelled, "宿主报告任务已取消")
                self._append_event(job, "cancelled", {"source": "encoder"})
                return
            if normalized in ("running", "encoding", "in_progress"):
                job.state = JobState.running

        # 后备来源：输出文件增长 / 存在性（显式标注，不冒充编码器结论）
        self._update_from_output_file(job)

    def _update_from_output_file(self, job: Job) -> None:
        if not job.output_path:
            return
        path = Path(job.output_path)
        try:
            size = path.stat().st_size if path.is_file() else None
        except OSError:  # pragma: no cover - 依赖环境
            size = None

        if size is None:
            job.progress_source = ProgressSource.output_exists
            self._stable_polls[job.id] = 0
            return

        previous = self._last_size.get(job.id)
        job.output_size = size
        if previous is None or size > previous:
            self._stable_polls[job.id] = 0
            job.progress_source = ProgressSource.output_growth
            if job.progress is None:
                job.message = "输出文件正在增长（编码进行中）"
            job.state = JobState.running
        else:
            self._stable_polls[job.id] = self._stable_polls.get(job.id, 0) + 1
            if self._stable_polls[job.id] >= self._settle_polls and size > 0:
                if job.progress is None:
                    job.progress = 1.0
                job.progress_source = ProgressSource.output_exists
                job.result = dict(job.result or {})
                job.result["inferred"] = True
                job.add_event(
                    JobState.succeeded,
                    f"输出文件已稳定（{size} 字节）：推断为完成（宿主未提供状态）",
                    inferred=True,
                )
                self._append_event(job, "succeeded", {"source": "output_exists", "inferred": True})
        self._last_size[job.id] = size

    # ---- 持久化 --------------------------------------------------------
    def _persist(self) -> None:
        if not self._persist_enabled:
            return
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            snapshot = {
                "version": 1,
                "saved_at": self._clock(),
                "jobs": [self._jobs[job_id].model_dump(mode="json") for job_id in self._order],
            }
            tmp = self.snapshot_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.snapshot_path)
        except OSError as exc:  # pragma: no cover - 依赖环境
            if self.logger is not None:
                self.logger.warning("job_persist_failed", error=str(exc))

    def _append_event(self, job: Job, kind: str, detail: Mapping[str, Any]) -> None:
        if not self._persist_enabled:
            return
        record = {
            "ts": self._clock(),
            "job_id": job.id,
            "tool": job.tool,
            "host": job.host,
            "kind": kind,
            "detail": dict(detail),
        }
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with self._event_log_path().open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:  # pragma: no cover - 依赖环境
            pass

    def _load(self) -> None:
        if not self._persist_enabled or not self.snapshot_path.is_file():
            return
        try:
            data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for raw in data.get("jobs", []):
            try:
                job = Job.model_validate(raw)
            except Exception:  # noqa: BLE001 - 旧版本快照格式变化时跳过而不是崩
                continue
            if not job.state.terminal:
                # 服务器重启后无法确认宿主侧状态：明确标为 unknown，绝不猜测。
                job.add_event(
                    JobState.unknown,
                    "MCP 服务器重启，宿主侧状态无法确认（不猜测）；"
                    "请检查宿主/AME 的编码队列与输出文件",
                )
            self._jobs[job.id] = job
            self._order.append(job.id)
