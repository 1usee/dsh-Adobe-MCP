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
]

_HIVES = {"HKLM": "HKEY_LOCAL_MACHINE", "HKCU": "HKEY_CURRENT_USER"}


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

    def value(self, location: RegistryLocation, value_name: str) -> str | None:  # pragma: no cover
        winreg = self._winreg
        try:
            with self._open(location) as handle:
                data, _kind = winreg.QueryValueEx(handle, value_name)
                return str(data) if data is not None else None
        except FileNotFoundError:
            return None
        except OSError:
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


def read_host_installs(
    locations: Sequence[RegistryLocation], reader: RegistryReader
) -> list[HostInstall]:
    """按注册表位置列表读取全部安装记录（位置之间相互独立，允许重复）。"""
    installs: list[HostInstall] = []
    for location in locations:
        before = len(installs)
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
            installs.append(
                HostInstall(
                    version=version,
                    install_path=Path(str(raw)),
                    source=child.describe(),
                )
            )
        if len(installs) > before:
            continue
        # 少数安装会把 InstallPath 直接写在主键上而不分子键；仅在没有任何
        # 版本子键命中时才采用它，避免把同一个安装重复登记成"无版本"记录。
        raw = reader.value(location, location.value_name)
        if raw:
            installs.append(
                HostInstall(version="", install_path=Path(str(raw)), source=location.describe())
            )
    return installs


def read_raw(location: RegistryLocation, reader: RegistryReader) -> dict[str, Any]:  # pragma: no cover
    """诊断用：把一个注册表位置下的内容原样读出。"""
    return {
        "location": location.describe(),
        "subkeys": reader.subkeys(location),
        "value": reader.value(location, location.value_name),
    }
