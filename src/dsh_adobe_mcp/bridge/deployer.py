"""CEP 面板部署（说明书 §4.2）。

部署目标为**用户级**扩展目录 ``%APPDATA%\\Adobe\\CEP\\extensions\\<extension-id>\\``：
系统级目录需要管理员权限，用户级无需提权，也不会与其他用户或系统范围配置互相干扰。

部署策略：

1. 比较源目录与目标目录的**清单哈希**（文件名 + 大小 + 修改时间），一致则跳过——
   避免每次启动都做无谓写入；
2. 不一致时整体替换：先复制到临时目录，校验完整性，再原子替换目标目录；
3. 部署前校验 ``manifest.xml`` 必需元素齐全，不合规则拒绝部署并报错；
4. 保留上一版本用于回滚；部署失败时自动还原；
5. ``scripts/dev_install_cep.ps1`` 提供同样的逻辑供手动执行。

**关于 DSH 文件沙箱（说明书 A-1）**：MCP 服务器是独立进程，其文件访问是否受
DSH 工作区沙箱约束取决于 DSH 的启动机制。本模块不假定任何一种情况，
而是在真实写入失败时返回可执行的退路（由用户手工运行部署脚本）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .manifest_check import ManifestCheck, check_manifest

__all__ = [
    "DeployError",
    "DeployPlan",
    "DeployResult",
    "CepDeployer",
    "directory_digest",
    "player_debug_registry_path",
]

#: 被排除在部署之外的文件（版本库元数据与构建产物）。
#:
#: 注意 ``.debug`` **不在**这里：CEP 会读取扩展目录下的 ``.debug`` 来决定调试端口，
#: 它是扩展的一部分；把它排除会让面板的远程调试永远连不上。
DEFAULT_IGNORES = (".git", "__pycache__", "*.pyc", ".DS_Store", "Thumbs.db")


class DeployError(Exception):
    """部署失败。``code`` 对应 errors.py 中的错误码。"""

    def __init__(self, code: str, message: str, remedy: str, detail: Any = None) -> None:
        self.code = code
        self.message = message
        self.remedy = remedy
        self.detail = detail
        super().__init__(message)


@dataclass
class DeployPlan:
    action: str  # install | replace | skip
    reason: str
    source_digest: str | None = None
    target_digest: str | None = None
    manifest: ManifestCheck | None = None
    file_count: int = 0

    def to_public(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "source_digest": self.source_digest,
            "target_digest": self.target_digest,
            "file_count": self.file_count,
            "manifest": self.manifest.to_public() if self.manifest else None,
        }


@dataclass
class DeployResult:
    action: str
    target_dir: str
    digest: str | None = None
    backup_dir: str | None = None
    file_count: int = 0
    warnings: list[str] = field(default_factory=list)
    duration_ms: int = 0

    def to_public(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target_dir": self.target_dir,
            "digest": self.digest,
            "backup_dir": self.backup_dir,
            "file_count": self.file_count,
            "warnings": list(self.warnings),
            "duration_ms": self.duration_ms,
        }


def _is_ignored(path: Path, patterns: Iterable[str]) -> bool:
    name = path.name
    for pattern in patterns:
        if pattern.startswith("*"):
            if name.endswith(pattern[1:]):
                return True
        elif name == pattern:
            return True
    return False


def directory_manifest(
    root: Path,
    *,
    ignores: Iterable[str] = DEFAULT_IGNORES,
    file_extension: str | None = None,
) -> list[tuple[str, int, int]]:
    """清单：``(相对路径, 大小, 修改时间纳秒)``，按路径排序。

    这是"是否需要重新部署"的判据（说明书 §4.2 第 1 条）。
    """
    entries: list[tuple[str, int, int]] = []
    if not root.is_dir():
        return entries
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if any(_is_ignored(part, ignores) for part in (path, *path.parents)):
            continue
        if file_extension and path.suffix.lower() != file_extension.lower():
            continue
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover - 依赖环境
            continue
        entries.append((str(path.relative_to(root)).replace("\\", "/"), stat.st_size, stat.st_mtime_ns))
    return entries


def directory_digest(
    root: Path, *, ignores: Iterable[str] = DEFAULT_IGNORES, file_extension: str | None = None
) -> str:
    """对清单求 sha256，作为"目录版本"的指纹。"""
    digest = hashlib.sha256()
    for relative, size, mtime in directory_manifest(root, ignores=ignores, file_extension=file_extension):
        digest.update(f"{relative}\0{size}\0{mtime}\n".encode("utf-8"))
    return digest.hexdigest()


def player_debug_registry_path(csxs_version: int) -> str:
    """开发期调试模式注册表路径（说明书 §1.3 的唯一例外说明）。"""
    return rf"Software\Adobe\CSXS.{csxs_version}"


def set_player_debug_mode(
    versions: Iterable[int] = (9, 10, 11, 12),
    *,
    writer: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """设置 ``HKCU\\Software\\Adobe\\CSXS.<n>\\PlayerDebugMode = "1"``。

    这是 Adobe 官方为扩展开发者提供的标准设置，**只影响未签名扩展的加载**，
    与宿主软件的授权无关，也不影响任何已签名扩展（说明书 §1.3）。

    :param writer: 可注入的注册表写入器（签名 ``(path, value)``），便于测试。
    """
    results: dict[str, Any] = {}
    if writer is None:
        if os.name != "nt":  # pragma: no cover - 非 Windows
            return {"ok": False, "reason": "非 Windows 平台，无需该设置", "applied": []}
        import winreg

        def writer(path: str, value: str) -> None:  # type: ignore[misc]
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as handle:
                winreg.SetValueEx(handle, "PlayerDebugMode", 0, winreg.REG_SZ, value)

    applied: list[str] = []
    for version in versions:
        path = player_debug_registry_path(int(version))
        try:
            writer(path, "1")
            applied.append(path)
        except OSError as exc:  # pragma: no cover - 依赖环境
            results.setdefault("errors", []).append(f"{path}: {exc}")
    results["ok"] = True
    results["applied"] = applied
    results["hint"] = (
        "PlayerDebugMode 只为加载未签名的本地开发版扩展；本项目不读取、不判断、"
        "不干预任何授权状态（说明书 §1.3）"
    )
    return results


class CepDeployer:
    """把仓库里的 ``cep-extension/`` 部署到用户级 CEP 扩展目录。"""

    def __init__(
        self,
        *,
        source_dir: Path,
        extensions_dir: Path,
        extension_id: str,
        backup_dir: Path | None = None,
        logger: Any = None,
        ignores: Iterable[str] = DEFAULT_IGNORES,
        keep_backups: int = 2,
    ) -> None:
        self.source_dir = Path(source_dir)
        self.extensions_dir = Path(extensions_dir)
        self.extension_id = extension_id
        self.backup_dir = Path(backup_dir) if backup_dir else self.extensions_dir.parent / "CEP-backup"
        self.logger = logger
        self.ignores = tuple(ignores)
        self.keep_backups = max(1, keep_backups)

    # ---- 路径 ----------------------------------------------------------
    @property
    def target_dir(self) -> Path:
        return self.extensions_dir / self.extension_id

    @property
    def manifest_path(self) -> Path:
        return self.source_dir / "CSXS" / "manifest.xml"

    def deployed_manifest_path(self) -> Path:
        return self.target_dir / "CSXS" / "manifest.xml"

    # ---- 检查 ----------------------------------------------------------
    def check(self) -> ManifestCheck:
        return check_manifest(self.manifest_path)

    def plan(self, *, force: bool = False) -> DeployPlan:
        """比较源与目标的清单哈希，得出部署动作。"""
        manifest = self.check()
        if not manifest.ok:
            return DeployPlan(
                action="abort",
                reason="manifest 校验失败：" + manifest.summary(),
                manifest=manifest,
            )
        if not self.source_dir.is_dir():
            raise DeployError(
                "PANEL_DEPLOY_FAILED",
                f"面板源目录不存在：{self.source_dir}",
                "确认仓库完整（cep-extension/ 目录存在），或用配置项 cep.source_dir 指向正确位置",
            )

        source_digest = directory_digest(self.source_dir, ignores=self.ignores)
        file_count = len(directory_manifest(self.source_dir, ignores=self.ignores))
        if not self.target_dir.is_dir():
            return DeployPlan(
                action="install",
                reason="目标目录不存在，执行首次安装",
                source_digest=source_digest,
                manifest=manifest,
                file_count=file_count,
            )
        target_digest = directory_digest(self.target_dir, ignores=self.ignores)
        if not force and source_digest == target_digest:
            return DeployPlan(
                action="skip",
                reason="源与目标的清单哈希一致，无需重新部署",
                source_digest=source_digest,
                target_digest=target_digest,
                manifest=manifest,
                file_count=file_count,
            )
        return DeployPlan(
            action="replace",
            reason="源与目标不一致，整体替换",
            source_digest=source_digest,
            target_digest=target_digest,
            manifest=manifest,
            file_count=file_count,
        )

    def status(self) -> dict[str, Any]:
        """部署状态诊断（供 ``host_status`` 使用）。"""
        manifest = self.check()
        source_digest = (
            directory_digest(self.source_dir, ignores=self.ignores)
            if self.source_dir.is_dir()
            else None
        )
        target_exists = self.target_dir.is_dir()
        target_digest = (
            directory_digest(self.target_dir, ignores=self.ignores) if target_exists else None
        )
        writable, reason = self._writable()
        return {
            "source_dir": str(self.source_dir),
            "extensions_dir": str(self.extensions_dir),
            "extension_id": self.extension_id,
            "target_dir": str(self.target_dir),
            "installed": target_exists,
            "in_sync": bool(source_digest and target_digest and source_digest == target_digest),
            "source_digest": source_digest,
            "target_digest": target_digest,
            "manifest": manifest.to_public(),
            "extensions_dir_writable": writable,
            "writability_note": reason,
            "player_debug_mode": self._player_debug_state(),
        }

    # ---- 部署 ----------------------------------------------------------
    def deploy(self, *, force: bool = False) -> DeployResult:
        started = time.perf_counter()
        plan = self.plan(force=force)
        warnings: list[str] = [issue.message for issue in (plan.manifest.warnings if plan.manifest else [])]

        if plan.action == "abort":
            assert plan.manifest is not None
            raise DeployError(
                "PANEL_MANIFEST_INVALID",
                "面板 manifest 不合规，已拒绝部署：" + plan.manifest.summary(),
                "按 detail 列出的缺失项修正 cep-extension/CSXS/manifest.xml 后重试；"
                "其中 --mixed-context 缺失会导致面板里 require 未定义",
                detail=plan.manifest.to_public(),
            )

        if plan.action == "skip":
            if self.logger is not None:
                self.logger.info("cep_deploy_skipped", target=str(self.target_dir))
            return DeployResult(
                action="skip",
                target_dir=str(self.target_dir),
                digest=plan.source_digest,
                file_count=plan.file_count,
                warnings=warnings,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        self._assert_writable()
        staging = self._staging_dir()
        backup_path: Path | None = None
        try:
            self._copy_tree(self.source_dir, staging)
            staged_digest = directory_digest(staging, ignores=self.ignores)
            if staged_digest != plan.source_digest:
                raise DeployError(
                    "PANEL_DEPLOY_FAILED",
                    "复制到临时目录后的清单哈希与源不一致（复制不完整）",
                    "检查磁盘空间与杀毒软件拦截后重试",
                    detail={"source": plan.source_digest, "staged": staged_digest},
                )

            if self.target_dir.exists():
                backup_path = self._backup_dir()
                self._replace_dir(self.target_dir, backup_path)
            try:
                self._move_dir(staging, self.target_dir)
            except OSError:
                # 替换失败 → 自动还原上一版本（说明书 §4.2 第 4 条）
                if backup_path is not None and backup_path.exists():
                    if self.target_dir.exists():
                        shutil.rmtree(self.target_dir, ignore_errors=True)
                    self._replace_dir(backup_path, self.target_dir)
                raise

            deployed_digest = directory_digest(self.target_dir, ignores=self.ignores)
            self._prune_backups()
        except DeployError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except PermissionError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise DeployError(
                "PANEL_DEPLOY_BLOCKED",
                f"写入 CEP 扩展目录被拒绝：{self.extensions_dir}",
                "由用户在普通终端运行 scripts\\dev_install_cep.ps1 完成一次部署，"
                "本服务器随后只做存在性检测（说明书 §4.2 的退路）",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise DeployError(
                "PANEL_DEPLOY_FAILED",
                f"部署面板失败：{exc}",
                "查看 detail 中的系统错误；必要时手工运行 scripts\\dev_install_cep.ps1",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        duration_ms = int((time.perf_counter() - started) * 1000)
        if self.logger is not None:
            self.logger.info(
                "cep_deployed",
                action=plan.action,
                target=str(self.target_dir),
                digest=deployed_digest,
                backup=str(backup_path) if backup_path else None,
                duration_ms=duration_ms,
            )
        return DeployResult(
            action=plan.action,
            target_dir=str(self.target_dir),
            digest=deployed_digest,
            backup_dir=str(backup_path) if backup_path else None,
            file_count=plan.file_count,
            warnings=warnings,
            duration_ms=duration_ms,
        )

    # ---- 内部工具 ------------------------------------------------------
    def _staging_dir(self) -> Path:
        return self.extensions_dir / f".{self.extension_id}.staging-{os.getpid()}"

    def _backup_dir(self) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return self.backup_dir / f"{self.extension_id}-{stamp}"

    def _writable(self) -> tuple[bool, str | None]:
        probe_root = self.extensions_dir
        try:
            probe_root.mkdir(parents=True, exist_ok=True)
            probe = probe_root / f".write-probe-{os.getpid()}"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            return True, None
        except OSError as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def _assert_writable(self) -> None:
        writable, reason = self._writable()
        if not writable:
            raise DeployError(
                "PANEL_DEPLOY_BLOCKED",
                f"CEP 扩展目录不可写：{self.extensions_dir}",
                "由用户在普通终端运行 scripts\\dev_install_cep.ps1 完成一次部署"
                "（说明书 §4.2 列出的沙箱退路）；随后服务器只做存在性检测",
                detail=reason,
            )

    def _copy_tree(self, source: Path, target: Path) -> None:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if any(_is_ignored(part, self.ignores) for part in (path, *path.parents)):
                continue
            destination = target / relative
            if path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)

    def _replace_dir(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(source), str(target))

    def _move_dir(self, source: Path, target: Path) -> None:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        try:
            os.replace(str(source), str(target))
        except OSError:
            shutil.move(str(source), str(target))

    def _prune_backups(self) -> None:
        if not self.backup_dir.is_dir():
            return
        backups = sorted(
            (path for path in self.backup_dir.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for stale in backups[self.keep_backups :]:
            shutil.rmtree(stale, ignore_errors=True)
            if self.logger is not None:
                self.logger.info("cep_backup_pruned", path=str(stale))

    def _player_debug_state(self) -> dict[str, Any]:
        if os.name != "nt":
            return {"applicable": False, "reason": "非 Windows 平台"}
        try:
            import winreg

            found: dict[str, str | None] = {}
            for version in (9, 10, 11, 12):
                path = player_debug_registry_path(version)
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as handle:
                        value, _kind = winreg.QueryValueEx(handle, "PlayerDebugMode")
                        found[path] = str(value)
                except FileNotFoundError:
                    found[path] = None
                except OSError:
                    found[path] = None
            return {
                "applicable": True,
                "values": found,
                "enabled": any(value == "1" for value in found.values()),
                "hint": "未签名扩展需要 PlayerDebugMode=1 才能加载（Adobe 官方开发设置）",
            }
        except Exception as exc:  # pragma: no cover - 依赖环境
            return {"applicable": True, "error": str(exc)}

    def rollback(self, backup_dir: str | Path | None = None) -> DeployResult:
        """回滚到最近一次备份（异常场景的手工工具）。"""
        if backup_dir is None:
            candidates = sorted(
                (path for path in self.backup_dir.iterdir() if path.is_dir()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            ) if self.backup_dir.is_dir() else []
            if not candidates:
                raise DeployError(
                    "PANEL_DEPLOY_FAILED",
                    "没有可用的备份",
                    f"检查备份目录 {self.backup_dir}；若无备份，重新运行部署即可重建面板目录",
                )
            backup_dir = candidates[0]
        source = Path(backup_dir)
        if not source.is_dir():
            raise DeployError(
                "PANEL_DEPLOY_FAILED", f"备份不存在：{source}", "检查 cep.backup_dir 配置"
            )
        self._assert_writable()
        self._replace_dir(source, self.target_dir)
        digest = directory_digest(self.target_dir, ignores=self.ignores)
        if self.logger is not None:
            self.logger.warning("cep_rollback", backup=str(source), digest=digest)
        return DeployResult(
            action="rollback", target_dir=str(self.target_dir), digest=digest
        )
