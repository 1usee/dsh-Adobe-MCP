"""检查点：创建、列举、保留策略、恢复前置条件（说明书 §8.3）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dsh_adobe_mcp.safety.checkpoint import CheckpointError, CheckpointManager, project_dir_of


class FakeClock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def manager(tmp_path: Path, clock: FakeClock) -> CheckpointManager:
    return CheckpointManager(tmp_path / "checkpoints", max_checkpoints=3, clock=clock)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "demo.prproj"
    path.write_bytes(b"<Project/>" * 100)
    return path


def test_create_copies_file_and_writes_sidecar(manager: CheckpointManager, project: Path):
    checkpoint = manager.create(project, label="before T2")
    assert Path(checkpoint.path).is_file()
    assert Path(checkpoint.path).read_bytes() == project.read_bytes()
    assert checkpoint.size == project.stat().st_size
    assert checkpoint.kind == "file_copy"
    sidecar = Path(checkpoint.path).with_suffix(".prproj.json")
    assert sidecar.is_file()
    assert manager.get(checkpoint.id).label == "before T2"


def test_create_rejects_missing_project_file(manager: CheckpointManager, tmp_path: Path):
    with pytest.raises(CheckpointError) as excinfo:
        manager.create(tmp_path / "nope.prproj")
    assert excinfo.value.code == "CHECKPOINT_FAILED"
    assert "工程文件不存在" in excinfo.value.message


def test_copy_failure_is_reported_and_leaves_nothing_behind(tmp_path: Path, project: Path):
    def broken_copy(source: str, target: str) -> None:
        raise OSError("磁盘错误")

    manager = CheckpointManager(tmp_path / "cp", copy_function=broken_copy)
    with pytest.raises(CheckpointError) as excinfo:
        manager.create(project)
    assert excinfo.value.code == "CHECKPOINT_FAILED"
    assert not list((tmp_path / "cp").glob("*.prproj"))


def test_size_mismatch_is_rejected(tmp_path: Path, project: Path):
    """复制期间源文件被改写 → 宁可拒绝，也不留下一份坏检查点（说明书 A-4）。"""

    def truncated_copy(source: str, target: str) -> None:
        Path(target).write_bytes(b"partial")

    manager = CheckpointManager(tmp_path / "cp", copy_function=truncated_copy)
    with pytest.raises(CheckpointError) as excinfo:
        manager.create(project)
    assert "大小与源工程不一致" in excinfo.value.message
    assert not list((tmp_path / "cp").glob("*.prproj"))


def test_retention_prunes_oldest(manager: CheckpointManager, project: Path, clock: FakeClock):
    ids = []
    for index in range(5):
        clock.advance(60)
        ids.append(manager.create(project, label=f"label-{index}").id)
    remaining = [item.id for item in manager.list()]
    assert len(remaining) == 3
    assert ids[0] not in remaining and ids[1] not in remaining
    assert ids[-1] in remaining
    # 被清理的检查点连同 sidecar 一起删除：磁盘上不应留下孤儿元数据
    orphan_sidecars = [
        path
        for path in manager.directory.glob("*.json")
        if not path.with_suffix("").is_file()
    ]
    assert orphan_sidecars == []


def test_list_is_sorted_newest_first(manager: CheckpointManager, project: Path, clock: FakeClock):
    first = manager.create(project)
    clock.advance(120)
    second = manager.create(project)
    listed = manager.list()
    assert [item.id for item in listed] == [second.id, first.id]


def test_get_unknown_checkpoint(manager: CheckpointManager):
    with pytest.raises(CheckpointError) as excinfo:
        manager.get("does-not-exist")
    assert excinfo.value.code == "CHECKPOINT_NOT_FOUND"


def test_delete_removes_files(manager: CheckpointManager, project: Path):
    checkpoint = manager.create(project)
    manager.delete(checkpoint.id)
    assert not Path(checkpoint.path).exists()
    assert manager.list() == []


def test_adopt_registers_existing_file_inside_checkpoint_dir(
    manager: CheckpointManager, project: Path
):
    target = manager.directory / "save-as__20261015-120000.prproj"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x" * 32)
    checkpoint = manager.adopt(target, project_path=str(target), kind="save_as")
    assert checkpoint.kind == "save_as"
    assert manager.get(checkpoint.id).project_path == str(target)
    assert checkpoint.id in [item.id for item in manager.list()]


def test_adopt_refuses_file_outside_checkpoint_dir(manager: CheckpointManager, tmp_path: Path):
    """登记一个目录外的文件会产出"看不见的检查点"，必须在入口拒绝。"""
    outside = tmp_path / "outside.prproj"
    outside.write_bytes(b"x")
    with pytest.raises(CheckpointError) as excinfo:
        manager.adopt(outside)
    assert "只能登记检查点目录内的文件" in excinfo.value.message


def test_stats_reports_writability(manager: CheckpointManager, project: Path):
    manager.create(project)
    stats = manager.stats()
    assert stats["count"] == 1
    assert stats["max_checkpoints"] == 3
    assert stats["writable"] is True
    assert stats["total_mb"] > 0


def test_unwritable_directory_is_reported(tmp_path: Path, project: Path, monkeypatch):
    """检查点目录不可写 → 抛 CHECKPOINT_FAILED（调用方据此拒绝 T2）。"""
    manager = CheckpointManager(tmp_path / "cp")

    def boom(*args, **kwargs):
        raise OSError("拒绝访问")

    monkeypatch.setattr(Path, "mkdir", boom)
    with pytest.raises(CheckpointError) as excinfo:
        manager.create(project)
    assert excinfo.value.code == "CHECKPOINT_FAILED"
    assert "不可用" in excinfo.value.message


def test_insufficient_disk_space_is_rejected(tmp_path: Path, project: Path, monkeypatch):
    manager = CheckpointManager(tmp_path / "cp")

    class Usage:
        free = 1024

    monkeypatch.setattr(
        "dsh_adobe_mcp.safety.checkpoint.shutil.disk_usage", lambda _path: Usage()
    )
    with pytest.raises(CheckpointError) as excinfo:
        manager.create(project)
    assert "磁盘可用空间不足" in excinfo.value.message


def test_project_dir_of():
    assert project_dir_of(r"D:\work\demo.prproj") == Path(r"D:\work")
    assert project_dir_of(None) is None
    assert project_dir_of("") is None


def test_list_ignores_foreign_files(manager: CheckpointManager, project: Path):
    manager.create(project)
    (manager.directory / "note.txt").write_text("hello", encoding="utf-8")
    assert len(manager.list()) == 1
