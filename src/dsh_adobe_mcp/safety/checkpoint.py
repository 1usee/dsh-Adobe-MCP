"""检查点：T2 破坏性操作前的工程副本（说明书 §8.3）。

设计要点：

* **主机制**是对磁盘上的 ``.prproj`` 做**文件级复制**到检查点目录。相比 ``saveAs``，
  文件复制不改变当前工程路径、不触发宿主对话框、不污染宿主内部状态。
* **前置条件**：若工程有未保存改动，先触发一次 ``save``（这本身是 T1 并记录）再复制；
  若工程从未保存过（没有磁盘文件），退化为 ``save_as`` 到检查点路径，并在返回里**明确告知**
  用户工程路径已被改变（这一步的编排在工具层，不在本模块）。
* **保留策略**：最多 ``safety.max_checkpoints`` 份，超出按时间清理最旧的，清理动作写日志。
* **恢复**由 ``safety_restore_checkpoint`` 完成，且恢复前会为当前状态再建一个检查点，
  避免"恢复"本身成为不可逆操作。

检查点目录不可写或磁盘空间不足时 **必须抛错**，由调用方拒绝执行 T2 操作——
"没有检查点也照样执行"是明确禁止的（说明书 §14.4）。
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

__all__ = ["Checkpoint", "CheckpointError", "CheckpointManager"]


class CheckpointError(Exception):
    """检查点相关失败；``code`` 对应 errors.py 中的错误码。"""

    def __init__(self, code: str, message: str, remedy: str, detail: Any = None) -> None:
        self.code = code
        self.message = message
        self.remedy = remedy
        self.detail = detail
        super().__init__(message)


_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._\u4e00-\u9fff-]+")


def _safe_stem(name: str, limit: int = 48) -> str:
    cleaned = _UNSAFE_CHARS.sub("_", name).strip("_.")
    return (cleaned or "project")[:limit]


@dataclass
class Checkpoint:
    """一份检查点的元数据。"""

    id: str
    path: str
    project_name: str
    project_path: str | None
    label: str | None
    size: int
    created_at: float
    created_iso: str
    kind: str = "file_copy"  # file_copy（复制）| save_as（工程从未保存过时的退化路径）

    def to_public(self) -> dict[str, Any]:
        data = asdict(self)
        data["size_mb"] = round(self.size / (1024 * 1024), 3)
        return data

    @classmethod
    def from_public(cls, data: dict[str, Any]) -> Checkpoint:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)  # type: ignore[arg-type]


class CheckpointManager:
    """检查点的创建、列举、恢复准备与清理。"""

    MIN_FREE_BYTES = 64 * 1024 * 1024  # 低于 64MB 可用空间直接拒绝创建检查点

    def __init__(
        self,
        directory: Path,
        *,
        max_checkpoints: int = 20,
        logger: Any = None,
        clock: Callable[[], float] | None = None,
        copy_function: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.max_checkpoints = max(1, int(max_checkpoints))
        self.logger = logger
        self._clock = clock or time.time
        self._copy = copy_function or shutil.copy2
        self._lock = threading.Lock()

    # ---- 基础 ----------------------------------------------------------
    def ensure_directory(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CheckpointError(
                "CHECKPOINT_FAILED",
                f"检查点目录不可用：{self.directory}",
                "检查配置项 safety.checkpoint_dir 是否可写、磁盘空间是否充足；"
                "修复后再重试（T2 操作在无检查点时不执行）",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

    def assert_space(self, needed_bytes: int) -> None:
        target = self.directory if self.directory.exists() else self.directory.parent
        try:
            usage = shutil.disk_usage(str(target))
        except OSError as exc:  # pragma: no cover - 依赖环境
            raise CheckpointError(
                "CHECKPOINT_FAILED",
                "无法读取磁盘可用空间，因此拒绝执行破坏性操作",
                "确认 safety.checkpoint_dir 所在磁盘可访问",
                detail=str(exc),
            ) from exc
        if usage.free - needed_bytes < self.MIN_FREE_BYTES:
            raise CheckpointError(
                "CHECKPOINT_FAILED",
                f"磁盘可用空间不足：剩余 {usage.free / 1048576:.0f}MB，"
                f"本次需要约 {needed_bytes / 1048576:.0f}MB 加保留余量",
                "清理磁盘空间或调整 safety.checkpoint_dir 后重试",
            )

    def _sidecar(self, copy_path: Path) -> Path:
        return copy_path.with_suffix(copy_path.suffix + ".json")

    def _write_sidecar(self, checkpoint: Checkpoint) -> None:
        try:
            self._sidecar(Path(checkpoint.path)).write_text(
                json.dumps(checkpoint.to_public(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover - 依赖环境
            if self.logger is not None:
                self.logger.warning("checkpoint_sidecar_failed", error=str(exc))

    # ---- 创建 ----------------------------------------------------------
    def _new_path(self, project_name: str, moment: float) -> Path:
        stamp = datetime.fromtimestamp(moment, tz=timezone.utc).astimezone().strftime(
            "%Y%m%d-%H%M%S"
        )
        stem = _safe_stem(Path(project_name).stem if project_name else "project")
        candidate = self.directory / f"{stem}__{stamp}.prproj"
        counter = 1
        while candidate.exists() or self._sidecar(candidate).exists():
            candidate = self.directory / f"{stem}__{stamp}_{counter}.prproj"
            counter += 1
        return candidate

    def create(
        self,
        project_path: str | Path,
        *,
        label: str | None = None,
        project_name: str | None = None,
    ) -> Checkpoint:
        """对磁盘上的工程文件做文件级复制，产出一份检查点。"""
        source = Path(project_path)
        if not source.is_file():
            raise CheckpointError(
                "CHECKPOINT_FAILED",
                f"工程文件不存在，无法建立检查点：{source}",
                "确认工程已保存到磁盘（ppro_project_save），或先另存为一个路径",
            )
        self.ensure_directory()
        size = source.stat().st_size
        self.assert_space(size)

        moment = self._clock()
        name = project_name or source.stem
        copy_path = self._new_path(source.stem, moment)

        try:
            self._copy(str(source), str(copy_path))
        except OSError as exc:
            raise CheckpointError(
                "CHECKPOINT_FAILED",
                f"复制工程文件失败：{source} → {copy_path}",
                "检查磁盘空间与目录权限后重试",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        try:
            copied_size = copy_path.stat().st_size
        except OSError:  # pragma: no cover - 依赖环境
            copied_size = size
        if copied_size != size:
            # 复制过程中源文件被改写（宿主正在写盘）——这是危险信号，必须失败。
            try:
                copy_path.unlink()
            except OSError:  # pragma: no cover
                pass
            raise CheckpointError(
                "CHECKPOINT_FAILED",
                "复制出的检查点大小与源工程不一致（源文件可能在复制期间被改写）",
                "让宿主编译/保存结束后重试；若反复出现，说明宿主正在后台写工程文件",
                detail={"source_size": size, "copy_size": copied_size},
            )

        checkpoint = Checkpoint(
            id=copy_path.stem,
            path=str(copy_path),
            project_name=name,
            project_path=str(source),
            label=label,
            size=copied_size,
            created_at=moment,
            created_iso=datetime.fromtimestamp(moment).astimezone().isoformat(timespec="seconds"),
            kind="file_copy",
        )
        self._write_sidecar(checkpoint)
        if self.logger is not None:
            self.logger.info(
                "checkpoint_created",
                checkpoint_id=checkpoint.id,
                project=checkpoint.project_name,
                size=checkpoint.size,
                label=label,
            )
        self.prune()
        return checkpoint

    def adopt(
        self,
        copy_path: str | Path,
        *,
        project_path: str | None = None,
        label: str | None = None,
        project_name: str | None = None,
        kind: str = "save_as",
    ) -> Checkpoint:
        """把一份已在**检查点目录内**的工程文件登记为检查点。

        用途是"工程从未保存过"的退化路径：先 ``save_as`` 到检查点目录，再把产物登记进来。
        如果文件不在检查点目录内，登记出来的检查点既不会被 :meth:`list` 列出、
        也无法被恢复，因此在入口直接拒绝（而不是留下一个"看不见的检查点"）。
        """
        path = Path(copy_path)
        if not path.is_file():
            raise CheckpointError(
                "CHECKPOINT_FAILED",
                f"登记检查点失败，文件不存在：{path}",
                "确认宿主已完成另存操作后重试",
            )
        self.ensure_directory()
        try:
            inside = path.resolve().parent == self.directory.resolve()
        except OSError:  # pragma: no cover - 依赖环境
            inside = False
        if not inside:
            raise CheckpointError(
                "CHECKPOINT_FAILED",
                f"只能登记检查点目录内的文件（{self.directory}），实际为：{path}",
                "把工程另存到配置项 safety.checkpoint_dir 指定的目录内，或改用 "
                "CheckpointManager.create() 做文件级复制",
            )
        moment = path.stat().st_mtime or self._clock()
        checkpoint = Checkpoint(
            id=path.stem,
            path=str(path),
            project_name=project_name or path.stem,
            project_path=project_path,
            label=label,
            size=path.stat().st_size,
            created_at=moment,
            created_iso=datetime.fromtimestamp(moment).astimezone().isoformat(timespec="seconds"),
            kind=kind,
        )
        self._write_sidecar(checkpoint)
        self.prune()
        return checkpoint

    # ---- 列举与读取 ----------------------------------------------------
    def list(self) -> list[Checkpoint]:
        """按时间倒序列出检查点（最新的在最前）。"""
        if not self.directory.is_dir():
            return []
        found: list[Checkpoint] = []
        for copy_path in self.directory.glob("*.prproj"):
            sidecar = self._sidecar(copy_path)
            meta: dict[str, Any] = {}
            if sidecar.is_file():
                try:
                    meta = json.loads(sidecar.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    meta = {}
            try:
                stat = copy_path.stat()
            except OSError:  # pragma: no cover - 依赖环境
                continue
            if meta:
                checkpoint = Checkpoint.from_public(meta)
                checkpoint.path = str(copy_path)
                checkpoint.id = copy_path.stem
            else:
                checkpoint = Checkpoint(
                    id=copy_path.stem,
                    path=str(copy_path),
                    project_name=copy_path.stem,
                    project_path=None,
                    label=None,
                    size=stat.st_size,
                    created_at=stat.st_mtime,
                    created_iso=datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(
                        timespec="seconds"
                    ),
                    kind="file_copy",
                )
            found.append(checkpoint)
        found.sort(key=lambda item: item.created_at, reverse=True)
        return found

    def get(self, checkpoint_id: str) -> Checkpoint:
        for checkpoint in self.list():
            if checkpoint.id == checkpoint_id or Path(checkpoint.path).name == checkpoint_id:
                return checkpoint
        raise CheckpointError(
            "CHECKPOINT_NOT_FOUND",
            f"找不到检查点：{checkpoint_id}",
            "先调用 safety_list_checkpoints 查看可用检查点及其 id",
        )

    def delete(self, checkpoint_id: str) -> Checkpoint:
        checkpoint = self.get(checkpoint_id)
        path = Path(checkpoint.path)
        for target in (path, self._sidecar(path)):
            try:
                if target.exists():
                    target.unlink()
            except OSError as exc:  # pragma: no cover - 依赖环境
                if self.logger is not None:
                    self.logger.warning(
                        "checkpoint_delete_failed", path=str(target), error=str(exc)
                    )
        if self.logger is not None:
            self.logger.info("checkpoint_deleted", checkpoint_id=checkpoint.id)
        return checkpoint

    # ---- 清理 ----------------------------------------------------------
    def prune(self) -> list[str]:
        """保留最新 ``max_checkpoints`` 份，删除更旧的；返回被删除的 id 列表。"""
        with self._lock:
            checkpoints = self.list()
            excess = checkpoints[self.max_checkpoints :]
            removed: list[str] = []
            for checkpoint in excess:
                path = Path(checkpoint.path)
                for target in (path, self._sidecar(path)):
                    try:
                        if target.exists():
                            target.unlink()
                    except OSError as exc:  # pragma: no cover - 依赖环境
                        if self.logger is not None:
                            self.logger.warning(
                                "checkpoint_prune_failed", path=str(target), error=str(exc)
                            )
                removed.append(checkpoint.id)
            if removed and self.logger is not None:
                self.logger.info(
                    "checkpoints_pruned", removed=removed, kept=len(checkpoints) - len(removed)
                )
            return removed

    # ---- 诊断 ----------------------------------------------------------
    def directory_writable(self) -> tuple[bool, str | None]:
        try:
            self.ensure_directory()
            probe = self.directory / ".write-probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            return True, None
        except (CheckpointError, OSError) as exc:
            return False, str(exc)

    def stats(self) -> dict[str, Any]:
        checkpoints = self.list()
        total = sum(item.size for item in checkpoints)
        return {
            "directory": str(self.directory),
            "count": len(checkpoints),
            "max_checkpoints": self.max_checkpoints,
            "total_mb": round(total / 1048576, 3),
            "writable": self.directory_writable()[0],
            "free_mb": _free_mb(self.directory),
        }


def _free_mb(directory: Path) -> float | None:
    target = directory if directory.exists() else directory.parent
    try:
        return round(shutil.disk_usage(str(target)).free / 1048576, 1)
    except OSError:  # pragma: no cover - 依赖环境
        return None


def project_dir_of(project_path: str | None) -> Path | None:
    """取工程文件所在目录（用于把它加入路径白名单）。"""
    if not project_path:
        return None
    path = Path(project_path)
    return path.parent if path.name else None
