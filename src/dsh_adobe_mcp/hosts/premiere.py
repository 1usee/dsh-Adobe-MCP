"""Adobe Premiere Pro 适配（第一阶段唯一的目标宿主）。"""

from __future__ import annotations

from .base import HostAdapter, HostType, RegistryLocation

__all__ = ["PremiereAdapter", "PREMIERE_REGISTRY_LOCATIONS"]


#: 说明书 §4.1 的注册表位置（含 32 位视图）
PREMIERE_REGISTRY_LOCATIONS: tuple[RegistryLocation, ...] = (
    RegistryLocation(hive="HKLM", path=r"SOFTWARE\Adobe\Premiere Pro", view="64"),
    RegistryLocation(hive="HKLM", path=r"SOFTWARE\WOW6432Node\Adobe\Premiere Pro", view="32"),
)


class PremiereAdapter(HostAdapter):
    """Premiere Pro 的进程名、可执行文件与启动方式。"""

    host_type = HostType.premiere
    cep_host_id = "PPRO"
    executable_name = "Adobe Premiere Pro.exe"
    process_names = (
        "Adobe Premiere Pro.exe",
        "Adobe Premiere Pro",
    )
    registry_locations = PREMIERE_REGISTRY_LOCATIONS

    def describe(self) -> str:
        return "Adobe Premiere Pro（CEP 标识 PPRO）"
