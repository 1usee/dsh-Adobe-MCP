"""宿主适配抽象（说明书 §4.1）。

L2 只接触宿主，因此"宿主"这个概念在这里抽象成三件事：

* :class:`HostDescriptor` —— 发现产物（类型、版本、安装路径、可执行文件、CEP 标识、运行/连接状态）；
* :class:`RegistryLocation` —— 一个宿主在注册表中的位置（**不使用文件系统猜测**）；
* :class:`HostAdapter` —— 每个宿主类型一份的平台适配（进程名、可执行文件、启动方式）。

`After Effects` / `Media Encoder` 等后续宿主要扩展时，只需要新增一个 HostAdapter，
L1 与 L3 都不需要改动——这是说明书 §1.2 为 v2 预留的扩展点。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Sequence

__all__ = [
    "HostType",
    "HostDescriptor",
    "RegistryLocation",
    "HostSelection",
    "HostAdapter",
    "version_sort_key",
    "parse_host_key",
]


class HostType(str, Enum):
    premiere = "premiere"
    media_encoder = "media_encoder"


#: CEP 宿主标识（manifest 的 HostList 与握手文件都用它）
CEP_HOST_IDS: dict[HostType, str] = {
    HostType.premiere: "PPRO",
    HostType.media_encoder: "AME",
}

#: MCP 工具里 ``host`` 参数的合法别名
HOST_ALIASES: dict[str, HostType] = {
    "premiere": HostType.premiere,
    "ppro": HostType.premiere,
    "pr": HostType.premiere,
    "media_encoder": HostType.media_encoder,
    "ame": HostType.media_encoder,
}


@dataclass(frozen=True)
class RegistryLocation:
    """宿主在 Windows 注册表中的一个候选位置。"""

    hive: str
    path: str
    value_name: str = "InstallPath"
    view: str = "auto"  # auto | 64 | 32

    def describe(self) -> str:
        return f"{self.hive}\\{self.path}\\{self.value_name}"


@dataclass
class HostDescriptor:
    """一个已安装宿主的完整描述。"""

    host_type: HostType
    version: str
    install_path: str
    executable: str
    cep_host_id: str
    running: bool = False
    pid: int | None = None
    connected: bool = False
    panel_version: str | None = None
    contract_schema_version: int | None = None
    qe_enabled: bool | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """工具 ``host`` 参数使用的稳定标识，例如 ``premiere:25.0``。"""
        return f"{self.host_type.value}:{self.version}"

    @property
    def executable_exists(self) -> bool:
        return bool(self.executable) and Path(self.executable).is_file()

    def to_public(self) -> dict[str, Any]:
        return {
            "host": self.host_type.value,
            "key": self.key,
            "version": self.version,
            "cep_host_id": self.cep_host_id,
            "install_path": self.install_path,
            "executable": self.executable,
            "executable_exists": self.executable_exists,
            "running": self.running,
            "pid": self.pid,
            "connected": self.connected,
            "panel_version": self.panel_version,
            "contract_schema_version": self.contract_schema_version,
            "qe_enabled": self.qe_enabled,
            "notes": list(self.notes),
        }


@dataclass
class HostSelection:
    """多版本并存时的选择结果（说明书 §4.1：歧义要报告，不要默默接管）。"""

    chosen: HostDescriptor | None
    alternatives: list[HostDescriptor] = field(default_factory=list)
    ambiguous: bool = False
    note: str = ""
    requested_version: str | None = None

    @property
    def ok(self) -> bool:
        return self.chosen is not None


def version_sort_key(version: str) -> tuple[int, ...]:
    """把 "25.0" / "24.6" / "2023" 这类版本号转成可比较的元组。

    Premiere Pro 的版本号有两种风格：年份式（``2023``）与主次式（``24.6``）。
    直接按数字比较会把 2023 排在 25 之前（2023 > 25），从而**选错宿主版本**，
    因此年份式（>= 2000）先减去 2000 归一化：``2023`` → 23，``24.6`` → (24, 6)。
    """
    parts: list[int] = []
    for index, chunk in enumerate(str(version).replace("-", ".").split(".")):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        value = int(digits) if digits else 0
        if index == 0 and value >= 2000:
            value -= 2000
        parts.append(value)
    return tuple(parts)


def parse_host_key(value: str | None) -> tuple[HostType | None, str | None]:
    """解析工具 ``host`` 参数：``"premiere"`` / ``"ppro:25.0"`` / ``"ame"`` / ``None``。"""
    if value is None:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    if ":" in text:
        head, _, tail = text.partition(":")
        host_type = HOST_ALIASES.get(head.strip().lower())
        return host_type, (tail.strip() or None)
    host_type = HOST_ALIASES.get(text.lower())
    if host_type is None:
        # 允许直接给版本号：``host="25.0"``
        return None, text
    return host_type, None


class HostAdapter:
    """一个宿主的平台适配层。v2 增加宿主时只需实现这个接口。

    子类必须声明 :attr:`host_type`、:attr:`cep_host_id`、:attr:`executable_name`、
    :attr:`process_names`、:attr:`registry_locations`；缺失会在**导入时**直接报错，
    而不是等到运行期才发现。
    """

    host_type: ClassVar[HostType]
    cep_host_id: ClassVar[str]
    executable_name: ClassVar[str]
    process_names: ClassVar[tuple[str, ...]]
    registry_locations: ClassVar[tuple[RegistryLocation, ...]]

    _REQUIRED_ATTRS: ClassVar[tuple[str, ...]] = (
        "host_type",
        "cep_host_id",
        "executable_name",
        "process_names",
        "registry_locations",
    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "__abstract__", False):
            return
        missing = [
            name for name in cls._REQUIRED_ATTRS if getattr(cls, name, None) is None
        ]
        if missing:
            raise TypeError(f"{cls.__name__} 缺少必需的类属性（或值为 None）：{missing}")

    # ---- 发现 ----------------------------------------------------------
    def discover(self, reader: Any | None = None) -> list[HostDescriptor]:
        from .registry import default_reader, read_host_installs

        reader = reader or default_reader()
        found: list[HostDescriptor] = []
        seen: set[tuple[str, str]] = set()
        for candidate in read_host_installs(
            self.registry_locations,
            reader,
            executable_name=self.executable_name,
            product_dir_names=self.product_dir_names,
            uninstall_match=self.uninstall_match,
        ):
            executable = Path(candidate.install_path) / self.executable_name
            key = (candidate.version, str(executable).lower())
            if key in seen:
                continue
            seen.add(key)
            notes: list[str] = [f"发现来源：{candidate.source}"]
            if not executable.is_file():
                notes.append(
                    f"该路径下没有找到 {self.executable_name}；"
                    "宿主可能被移动或卸载残留，启动前请人工确认"
                )
            found.append(
                HostDescriptor(
                    host_type=self.host_type,
                    version=candidate.version,
                    install_path=str(candidate.install_path),
                    executable=str(executable),
                    cep_host_id=self.cep_host_id,
                    notes=notes,
                )
            )
        found.sort(key=lambda item: version_sort_key(item.version), reverse=True)
        return found

    def select(
        self, descriptors: Sequence[HostDescriptor], version: str | None = None
    ) -> HostSelection:
        """按 `hosts.<宿主>.version` 选择目标；未指定时选版本号最高者，并报告歧义。"""
        if not descriptors:
            return HostSelection(chosen=None, requested_version=version)
        ordered = sorted(descriptors, key=lambda item: version_sort_key(item.version), reverse=True)
        if version:
            for descriptor in ordered:
                if descriptor.version == version or descriptor.version.startswith(version):
                    return HostSelection(
                        chosen=descriptor,
                        alternatives=[d for d in ordered if d is not descriptor],
                        requested_version=version,
                    )
            return HostSelection(
                chosen=None,
                alternatives=list(ordered),
                requested_version=version,
                note=f"未安装指定版本 {version}；已安装版本：{[d.version for d in ordered]}",
            )
        if len(ordered) == 1:
            return HostSelection(chosen=ordered[0], alternatives=[])
        return HostSelection(
            chosen=ordered[0],
            alternatives=list(ordered[1:]),
            ambiguous=True,
            note=(
                f"本机安装了多个版本 {[d.version for d in ordered]}；"
                f"默认选择最高版本 {ordered[0].version}，但**启动宿主前必须确认版本**："
                f"请在调用中给出 host=\"premiere:{ordered[0].version}\"，"
                f"或把配置项 hosts.{self.host_type.value}.version 固定下来"
            ),
        )

    # ---- 运行状态 ------------------------------------------------------
    def find_processes(self) -> list[tuple[int, str]]:
        """返回本宿主的 (pid, exe) 列表。使用 psutil，**绝不使用 os.kill(pid, 0)**。"""
        import psutil

        targets = {name.lower() for name in self.process_names}
        found: list[tuple[int, str]] = []
        for process in psutil.process_iter(["pid", "name", "exe"]):
            try:
                info = process.info
                name = (info.get("name") or "").lower()
                exe = info.get("exe") or ""
                if name in targets:
                    found.append((int(info["pid"]), exe))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return found

    def is_running(self, descriptor: HostDescriptor | None = None) -> tuple[bool, int | None]:
        processes = self.find_processes()
        if not processes:
            return False, None
        if descriptor is not None and descriptor.executable:
            wanted = str(Path(descriptor.executable)).lower()
            for pid, exe in processes:
                if exe and str(Path(exe)).lower() == wanted:
                    return True, pid
        # 无法按路径区分版本时，报告第一个进程并让上层提示"版本可能不是目标版本"。
        return True, processes[0][0]

    def refresh(self, descriptor: HostDescriptor) -> HostDescriptor:
        running, pid = self.is_running(descriptor)
        descriptor.running = running
        descriptor.pid = pid
        return descriptor

    # ---- 启动 ----------------------------------------------------------
    def launch_command(self, descriptor: HostDescriptor, project_path: str | None = None) -> list[str]:
        command = [descriptor.executable]
        if project_path:
            command.append(project_path)
        return command

    def launch(self, descriptor: HostDescriptor, project_path: str | None = None) -> int:
        command = self.launch_command(descriptor, project_path)
        creationflags = 0
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(  # noqa: S603 - 路径来自注册表，命令由本适配器构造
            command,
            cwd=str(Path(descriptor.executable).parent),
            close_fds=True,
            creationflags=creationflags,
        )
        return process.pid

    def describe(self) -> str:
        """人类可读的宿主说明。"""
        return f"{self.host_type.value}（CEP 标识 {self.cep_host_id}，进程名 {self.process_names[0]}）"
