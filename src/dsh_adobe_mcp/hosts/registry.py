"""Windows 注册表宿主发现（说明书 §4.1）。

**不使用文件系统猜测**（例如扫描 ``C:\\Program Files\\Adobe``），因为那会误匹配
非标准安装、绿色版残目录与卸载残留。只认注册表，注册表里没有就报"
``HOST_NOT_INSTALLED`` + 已发现的宿主列表"。

为了可测试，真实读取逻辑被抽象成 :class:`RegistryReader` 协议：
生产用 :class:`WinregReader`，测试用 :class:`DictReader` 注入一份假注册表。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

from .base import RegistryLocation, version_sort_key

__all__ = [
    "RegistryReader",
    "WinregReader",
    "DictReader",
    "HostInstall",
    "read_host_installs",
    "default_reader",
    "normalize_install_path",
    "candidate_install_dirs",
    "describe_location",
    "app_paths_location",
    "uninstall_locations",
    "UNINSTALL_PATH",
    "APP_PATHS_PATH",
]


def normalize_install_path(raw: str | Path) -> Path | None:  # pragma: no cover - 公共别名
    """:func:`_normalize_install_path` 的公共别名（供诊断脚本调用）。"""
    return _normalize_install_path(raw)


def candidate_install_dirs(raw: Path, product_dir_names: Sequence[str]) -> list[Path]:
    """:func:`_candidate_install_dirs` 的公共别名（供诊断脚本调用）。"""
    return _candidate_install_dirs(raw, product_dir_names)

_HIVES = {"HKLM": "HKEY_LOCAL_MACHINE", "HKCU": "HKEY_CURRENT_USER"}

#: Windows 的卸载注册表段（两套视图都要看）
UNINSTALL_PATH = r"Microsoft\Windows\CurrentVersion\Uninstall"
#: App Paths 段：指向实际安装的可执行文件
APP_PATHS_PATH = r"Microsoft\Windows\CurrentVersion\App Paths"


class RegistryReader(Protocol):
    """读注册表的最小接口（便于注入假实现）。"""

    def subkeys(self, location: RegistryLocation) -> list[str]:
        """返回该键下的子键名。键不存在时返回空列表。"""

    def value(self, location: RegistryLocation, value_name: str) -> str | None:
        """读取键下的字符串值。"""


@dataclass(frozen=True)
class HostInstall:
    """注册表里的一条宿主安装记录。"""

    version: str
    install_path: Path
    source: str


class WinregReader:
    """真实实现：读 HKLM，按需切换 32/64 位视图。"""

    def __init__(self) -> None:
        import winreg  # 仅 Windows 可用；导入失败由 default_reader 兜住

        self._winreg = winreg

    def _open(self, location: RegistryLocation):  # pragma: no cover - 依赖 Windows 注册表
        winreg = self._winreg
        hive = getattr(winreg, _HIVES.get(location.hive.upper(), location.hive))
        view = 0
        if location.view == "64" and hasattr(winreg, "KEY_WOW64_64KEY"):
            view = winreg.KEY_WOW64_64KEY
        elif location.view == "32" and hasattr(winreg, "KEY_WOW64_32KEY"):
            view = winreg.KEY_WOW64_32KEY
        else:
            view = getattr(winreg, "KEY_WOW64_64KEY", 0)
        return winreg.OpenKey(hive, location.path, 0, winreg.KEY_READ | view)

    def subkeys(self, location: RegistryLocation) -> list[str]:  # pragma: no cover
        winreg = self._winreg
        try:
            with self._open(location) as handle:
                names: list[str] = []
                index = 0
                while True:
                    try:
                        names.append(winreg.EnumKey(handle, index))
                    except OSError:
                        break
                    index += 1
                return names
        except FileNotFoundError:
            return []
        except PermissionError:
            return []
        except OSError:
            return []

    def value(self, location: RegistryLocation, value_name: str) -> str | None:
        winreg = self._winreg
        names = ["(default)"] if value_name == "(default)" else [value_name]
        for name in names:
            try:
                with self._open(location) as handle:
                    data, _kind = winreg.QueryValueEx(handle, name)
                    if data is not None:
                        return str(data)
            except FileNotFoundError:
                continue
            except OSError:
                continue
        return None


class DictReader:
    """测试用假注册表。

    ``values`` 形如 ``{"HKLM\\SOFTWARE\\Adobe\\Premiere Pro\\25.0": {"InstallPath": "C:\\...\\"}}``。
    """

    def __init__(self, values: dict[str, dict[str, str]] | None = None) -> None:
        self.values = {key.lower(): dict(value) for key, value in (values or {}).items()}

    @staticmethod
    def _key(location: RegistryLocation) -> str:
        return f"{location.hive.upper()}\\{location.path}".lower()

    def subkeys(self, location: RegistryLocation) -> list[str]:
        prefix = self._key(location) + "\\"
        found: list[str] = []
        for key in self.values:
            if not key.startswith(prefix):
                continue
            remainder = key[len(prefix) :]
            if "\\" in remainder:
                continue
            found.append(remainder)
        return found

    def value(self, location: RegistryLocation, value_name: str) -> str | None:
        entry = self.values.get(self._key(location))
        if not entry:
            return None
        for name, value in entry.items():
            if name.lower() == value_name.lower():
                return value
        return None


def default_reader() -> RegistryReader:
    """在生产环境返回真实读取器；非 Windows 或缺少权限时退化为空读取器。"""
    import os

    if os.name != "nt":  # pragma: no cover - 非 Windows 平台
        return DictReader({})
    try:
        return WinregReader()
    except Exception:  # pragma: no cover - winreg 不可用
        return DictReader({})


def _version_dirs(location: RegistryLocation, reader: RegistryReader) -> list[str]:
    names = [name for name in reader.subkeys(location) if any(ch.isdigit() for ch in name)]
    names.sort(key=version_sort_key, reverse=True)
    return names


def _normalize_install_path(raw: str | Path) -> Path | None:
    """把注册表里拿到的路径统一成"安装目录"。

    Adobe 在不同位置登记的路径含义不同：``InstallPath`` / ``App Paths`` 指向目录，
    而 ``Uninstall\\InstallLocation`` 可能指向可执行文件，也可能指向产品父目录
    （例如 ``D:\\premiere\\Adobe``）。这里按"若指向 exe 则取父目录；若是父目录则
    拼上产品目录名"的顺序归一化，交由发现阶段再判断哪种解释真实存在。
    """
    text = str(raw).strip().strip('"')
    if not text:
        return None
    return Path(text)


def _candidate_install_dirs(raw: Path, product_dir_names: Sequence[str]) -> list[Path]:
    """由一条注册表记录推导出所有可能的安装目录（按可信度排序）。"""
    candidates: list[Path] = []
    if raw.suffix.lower() == ".exe":
        candidates.append(raw.parent)
    else:
        candidates.append(raw)
        for name in product_dir_names:
            candidates.append(raw / name)
    seen: list[Path] = []
    for item in candidates:
        if item not in seen:
            seen.append(item)
    return seen


def _executable_for(directory: Path, executable_name: str) -> Path | None:
    """在候选目录里找可执行文件；找不到返回 None（由调用方决定是否降级登记）。"""
    direct = directory / executable_name
    if direct.is_file():
        return direct
    # 兜底：目录里可能有品牌化的其它命名（例如 "Adobe Premiere Pro 2023.exe"）
    try:
        matches = [item for item in directory.glob("*.exe") if "Premiere" in item.name]
    except OSError:  # pragma: no cover - 依赖环境
        return None
    return matches[0] if matches else None


def _version_from_path(path: Path | None) -> str:
    """从未知格式的路径里尽量还原版本号（如 "Adobe Premiere Pro 2023" → "2023"）。"""
    if path is None:
        return ""
    for part in reversed(path.parts):
        digits = "".join(ch for ch in part if ch.isdigit())
        if len(digits) >= 2:
            return digits if len(digits) == 4 else f"{digits[:2]}.{digits[2:]}"
    return ""


def _fallback_locations(location: RegistryLocation, executable_name: str) -> list[RegistryLocation]:
    """（保留的兼容入口）返回补充探测位置。

    实际探测在 :func:`_probe_fallbacks` 中完成；这个函数保留是为了让补充证据源
    在诊断输出/文档里可见。
    """
    del executable_name
    return [*uninstall_locations(), app_paths_location(executable_name or "", location.view)]


def app_paths_location(executable_name: str, view: str = "auto") -> RegistryLocation:
    """``App Paths\\<exe>`` 位置：指向实际安装的可执行文件。"""
    return RegistryLocation(
        hive="HKLM",
        path=rf"SOFTWARE\{APP_PATHS_PATH}\{executable_name}",
        value_name="(default)",
        view=view,
    )


def uninstall_locations() -> list[RegistryLocation]:
    """Windows 卸载注册表段的两套视图。

    **两套都要扫**：本机实测 Premiere Pro 的安装记录（``PPRO_23_1``）只存在于
    ``WOW6432Node``（32 位视图）下，只扫 64 位视图会一条都看不到。
    """
    return [
        RegistryLocation(hive="HKLM", path=rf"SOFTWARE\{UNINSTALL_PATH}", value_name="DisplayName", view="64"),
        RegistryLocation(
            hive="HKLM",
            path=rf"SOFTWARE\WOW6432Node\{UNINSTALL_PATH}",
            value_name="DisplayName",
            view="32",
        ),
    ]


def describe_location(location: RegistryLocation) -> str:
    """人类可读的位置描述；``(default)`` 是"该键的默认值"，不是子键名。"""
    if location.value_name == "(default)":
        return f"{location.hive}\\{location.path}（默认值）"
    return location.describe()


def read_host_installs(
    locations: Sequence[RegistryLocation],
    reader: RegistryReader,
    *,
    executable_name: str = "",
    version_value_name: str = "DisplayVersion",
    product_dir_names: Sequence[str] = (),
    uninstall_match: str = "",
) -> list[HostInstall]:
    """按注册表位置列表读取全部安装记录（位置之间相互独立，允许重复）。

    对每个位置按以下顺序取证，任一来源命中即停止：

    1. ``<位置>\\<版本>\\InstallPath``（标准安装：Adobe 自己写的安装目录）；
    2. 位置主键上的 ``InstallPath``（少数安装不分子键）；
    3. ``App Paths`` 中指向的可执行文件（非默认盘符安装时最可靠的一条）；
    4. ``Uninstall`` 段里 ``DisplayName`` 匹配的记录，取其安装位置并推导目录。

    探测 3/4 的存在，是因为**非默认安装路径下 Adobe 往往不写 ``InstallPath``**：
    此时只认第 1 条会把一份完全正常的安装判成"未安装"。
    """
    installs: list[HostInstall] = []
    for location in locations:
        found_here: list[HostInstall] = []
        for version in _version_dirs(location, reader):
            child = RegistryLocation(
                hive=location.hive,
                path=f"{location.path}\\{version}",
                value_name=location.value_name,
                view=location.view,
            )
            raw = reader.value(child, location.value_name)
            if not raw:
                continue
            found_here.append(
                HostInstall(
                    version=version,
                    install_path=Path(str(raw)),
                    source=f"{child.describe()} (InstallPath)",
                )
            )
        if found_here:
            installs.extend(found_here)
            continue

        raw = reader.value(location, location.value_name)
        if raw:
            installs.append(
                HostInstall(
                    version="",
                    install_path=Path(str(raw)),
                    source=f"{location.describe()} (InstallPath)",
                )
            )
            continue

        installs.extend(
            _probe_fallbacks(
                location,
                reader,
                executable_name=executable_name,
                version_value_name=version_value_name,
                product_dir_names=product_dir_names,
                uninstall_match=uninstall_match,
            )
        )
    return installs


def _probe_fallbacks(
    location: RegistryLocation,
    reader: RegistryReader,
    *,
    executable_name: str,
    version_value_name: str,
    product_dir_names: Sequence[str],
    uninstall_match: str = "",
) -> list[HostInstall]:
    """在主位置无 InstallPath 时，用 App Paths / Uninstall 两条证据补齐。"""
    results: list[HostInstall] = []
    match_text = uninstall_match or (Path(executable_name).stem.replace("Adobe", "").strip() if executable_name else "")
    names = [executable_name] if executable_name else []
    names += [name for name in product_dir_names if name]

    def consider(raw: str | None, source: str, version: str = "") -> bool:
        path = _normalize_install_path(raw) if raw else None
        if path is None:
            return False
        # 先试"注册表原文"，再试推导出的候选目录。
        # 这个顺序很关键：App Paths 指向 exe，取其父目录就是真正的安装目录，
        # 先推导会把 D:\premiere\Adobe\Adobe Premiere Pro 2023\Adobe Premiere Pro 2023.exe
        # 这类拼错的结果排到前面。
        ordered = [path] + _candidate_install_dirs(path, product_dir_names)
        for candidate in ordered:
            executable = _executable_for(candidate, executable_name) if executable_name else None
            if executable is not None:
                results.append(
                    HostInstall(
                        version=version
                        or _version_from_path(candidate)
                        or _version_from_path(path)
                        or _version_from_path(executable),
                        install_path=candidate,
                        source=source,
                    )
                )
                return True
        # 找不到 exe 时仍登记（让 host_status 能报告"装了但路径不可用"），
        # 此时用最贴近产品目录名的那个候选，避免把产品父目录当成安装目录。
        for candidate in reversed(ordered):
            if candidate.is_dir() or candidate == path:
                results.append(
                    HostInstall(
                        version=version or _version_from_path(candidate) or _version_from_path(path),
                        install_path=candidate,
                        source=source + "（未找到可执行文件，仅供诊断）",
                    )
                )
                return True
        return False

    # 证据 1：App Paths —— 指向实际安装的 exe
    if executable_name:
        app_paths = app_paths_location(executable_name, location.view)
        consider(reader.value(app_paths, "(default)"), f"{describe_location(app_paths)}（App Paths）")

    # 证据 2：Uninstall 段 —— DisplayName 含产品名的记录（**两套视图都要看**）
    for uninstall in uninstall_locations():
        for subkey in reader.subkeys(uninstall):
            entry = RegistryLocation(
                hive=uninstall.hive,
                path=f"{uninstall.path}\\{subkey}",
                value_name="DisplayName",
                view=uninstall.view,
            )
            display = reader.value(entry, "DisplayName") or ""
            if not match_text or match_text.lower() not in display.lower():
                continue
            version = reader.value(entry, version_value_name) or ""
            for value_name in ("InstallLocation", "InstallSource", "InstallDir"):
                raw = reader.value(entry, value_name)
                if raw and consider(raw, f"{describe_location(entry)}（{value_name}）", version):
                    break
    return results



def read_raw(location: RegistryLocation, reader: RegistryReader) -> dict[str, Any]:  # pragma: no cover
    """诊断用：把一个注册表位置下的内容原样读出。"""
    return {
        "location": location.describe(),
        "subkeys": reader.subkeys(location),
        "value": reader.value(location, location.value_name),
    }
