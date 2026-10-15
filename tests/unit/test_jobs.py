"""长任务模型：状态机、并发入队、进度来源、持久化（说明书 §9）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dsh_adobe_mcp.jobs.manager import JobManager, JobNotFound, JobQueueFull
from dsh_adobe_mcp.jobs.models import JobState, ProgressSource


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "jobs"


def make_manager(state_dir: Path, **kwargs) -> JobManager:
    params = {
        "state_dir": state_dir,
        "poll_interval_s": 100,
        "max_concurrent_per_host": 1,
        "max_queued_per_host": 1,
        "persist": True,
        "settle_polls": 2,
    }
    params.update(kwargs)
    return JobManager(**params)  # type: ignore[arg-type]


async def test_submit_and_status_lifecycle(state_dir: Path):
    manager = make_manager(state_dir)
    gate = asyncio.Event()

    async def submit():
        await gate.wait()
        return {"job_id": "enc-9", "output_path": "D:\\out\\x.mp4"}

    job = manager.submit(
        tool="ppro_export_encode_sequence",
        host="premiere:25.0",
        command="export.encode_sequence",
        parameters={},
        submit_callable=submit,
        output_path="D:\\out\\x.mp4",
    )
    assert job.state is JobState.pending
    await asyncio.sleep(0)
    status = manager.status(job.id)
    assert status["state"] == "running"
    assert status["progress"] is None
    assert status["progress_display"] == "unknown"
    assert any("unknown" in note for note in status["notes"])

    gate.set()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if manager.get(job.id).encoder_job_id:
            break
    assert manager.get(job.id).encoder_job_id == "enc-9"


async def test_submit_failure_marks_job_failed(state_dir: Path):
    manager = make_manager(state_dir)

    async def submit():
        raise RuntimeError("宿主拒绝")

    job = manager.submit(
        tool="t", host="h", command="c", parameters={}, submit_callable=submit
    )
    for _ in range(50):
        await asyncio.sleep(0.01)
        if manager.get(job.id).state.terminal:
            break
    assert manager.get(job.id).state is JobState.failed
    assert manager.get(job.id).error["code"] == "HOST_ERROR"


async def test_queue_limit_enforced(state_dir: Path):
    manager = make_manager(state_dir, max_queued_per_host=1)
    release = asyncio.Event()

    async def slow_submit():
        await release.wait()
        return {}

    first = manager.submit(tool="t", host="h", command="c", parameters={}, submit_callable=slow_submit)
    await asyncio.sleep(0)
    second = manager.submit(tool="t", host="h", command="c", parameters={}, submit_callable=slow_submit)
    assert second.state is JobState.pending
    assert manager.status(second.id)["queued"] is True
    with pytest.raises(JobQueueFull):
        manager.submit(tool="t", host="h", command="c", parameters={}, submit_callable=slow_submit)
    release.set()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if manager.get(first.id).encoder_job_id is not None or manager.get(second.id).state.terminal:
            break


async def test_cancel_queued_job(state_dir: Path):
    manager = make_manager(state_dir, max_queued_per_host=2)
    release = asyncio.Event()

    async def slow_submit():
        await release.wait()
        return {}

    first = manager.submit(tool="t", host="h", command="c", parameters={}, submit_callable=slow_submit)
    await asyncio.sleep(0)
    second = manager.submit(tool="t", host="h", command="c", parameters={}, submit_callable=slow_submit)
    result = await manager.cancel(second.id)
    assert result["cancelled"] is True
    assert result["stage"] == "queued"
    assert manager.get(second.id).state is JobState.cancelled
    release.set()
    await asyncio.sleep(0.02)


async def test_cancel_running_job_without_host_support_is_honest(state_dir: Path):
    manager = make_manager(state_dir)

    async def submit():
        return {"job_id": "enc-1"}

    job = manager.submit(tool="t", host="h", command="c", parameters={}, submit_callable=submit)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if manager.get(job.id).encoder_job_id:
            break
    result = await manager.cancel(job.id)
    assert result["cancelled"] is False
    assert "不支持取消" in result["message"]
    assert manager.get(job.id).state is JobState.running


async def test_progress_from_encoder_probe(state_dir: Path):
    async def probe(job):
        return {"state": "running", "progress": 0.42, "message": "编码中"}

    manager = make_manager(state_dir, probe=probe)

    async def submit():
        return {"job_id": "enc-1"}

    job = manager.submit(tool="t", host="h", command="c", parameters={}, submit_callable=submit)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if manager.get(job.id).encoder_job_id:
            break
    await manager.poll_once()
    assert manager.get(job.id).progress == pytest.approx(0.42)
    assert manager.get(job.id).progress_source is ProgressSource.encoder


async def test_encoder_success_is_authoritative(state_dir: Path):
    async def probe(job):
        return {"state": "succeeded"}

    manager = make_manager(state_dir, probe=probe)

    async def submit():
        return {"job_id": "enc-1"}

    job = manager.submit(tool="t", host="h", command="c", parameters={}, submit_callable=submit)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if manager.get(job.id).encoder_job_id:
            break
    await manager.poll_once()
    assert manager.get(job.id).state is JobState.succeeded
    assert manager.get(job.id).progress == 1.0
    assert not (manager.get(job.id).result or {}).get("inferred")


async def test_output_file_growth_then_inferred_success(state_dir: Path, tmp_path: Path):
    output = tmp_path / "out.mp4"
    manager = make_manager(state_dir)

    async def submit():
        return {"job_id": "enc-1", "output_path": str(output)}

    job = manager.submit(
        tool="t", host="h", command="c", parameters={}, submit_callable=submit, output_path=str(output)
    )
    for _ in range(50):
        await asyncio.sleep(0.01)
        if manager.get(job.id).encoder_job_id:
            break

    await manager.poll_once()  # 文件还不存在
    assert manager.get(job.id).state is JobState.running
    assert manager.get(job.id).progress_source is ProgressSource.output_exists

    output.write_bytes(b"x" * 2048)
    await manager.poll_once()
    assert manager.get(job.id).progress_source is ProgressSource.output_growth

    await manager.poll_once()
    await manager.poll_once()
    assert manager.get(job.id).state is JobState.succeeded
    assert manager.get(job.id).result["inferred"] is True
    assert any("推断" in note for note in manager.status(job.id)["notes"])


async def test_persistence_snapshot_and_reload_marks_unknown(state_dir: Path):
    manager = make_manager(state_dir)

    async def submit():
        return {"job_id": "enc-1"}

    job = manager.submit(tool="t", host="h", command="c", parameters={}, submit_callable=submit)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if manager.get(job.id).encoder_job_id:
            break
    snapshot = json.loads((state_dir / "jobs.json").read_text(encoding="utf-8"))
    assert snapshot["jobs"][0]["id"] == job.id

    revived = make_manager(state_dir)
    await revived.start()
    try:
        restored = revived.get(job.id)
        # 服务器重启后无法确认宿主侧状态 → 明确 unknown，绝不猜测
        assert restored.state is JobState.unknown
        assert any("重启" in event.message for event in restored.events)
    finally:
        await revived.stop()


async def test_status_of_unknown_job_raises(state_dir: Path):
    manager = make_manager(state_dir)
    with pytest.raises(JobNotFound):
        manager.get("nope")


async def test_event_log_is_append_only_jsonl(state_dir: Path):
    manager = make_manager(state_dir)

    async def submit():
        return {"job_id": "enc-1"}

    job = manager.submit(tool="t", host="h", command="c", parameters={}, submit_callable=submit)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if manager.get(job.id).encoder_job_id:
            break
    logs = list(state_dir.glob("jobs-*.jsonl"))
    assert logs, "事件日志未生成"
    kinds = [
        json.loads(line)["kind"]
        for line in logs[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert kinds[0] == "submitted"
    assert "accepted" in kinds


async def test_summary_counts_by_state(state_dir: Path):
    manager = make_manager(state_dir)

    async def submit():
        return {"job_id": "enc-1"}

    manager.submit(tool="t", host="h", command="c", parameters={}, submit_callable=submit)
    await asyncio.sleep(0.05)
    summary = manager.summary()
    assert summary["total"] == 1
    assert summary["by_state"].get("running") == 1
