"""Adobe Media Encoder 适配（M5 阶段接入；§1.2 明确 v1 不做深度集成）。

保留它的意义是验证 :class:`~dsh_adobe_mcp.hosts.base.HostAdapter` 扩展点确实可用：
新增宿主只需要这一个文件，L1 与 L3 都不需要改动。
"""

from __future__ import annotations

from .base import HostAdapter, HostType, RegistryLocation

__all__ = ["MediaEncoderAdapter", "AME_REGISTRY_LOCATIONS"]

AME_REGISTRY_LOCATIONS: tuple[RegistryLocation, ...] = (
    RegistryLocation(hive="HKLM", path=r"SOFTWARE\Adobe\Adobe Media Encoder", view="64"),
    RegistryLocation(
        hive="HKLM", path=r"SOFTWARE\WOW6432Node\Adobe\Adobe Media Encoder", view="32"
    ),
)


class MediaEncoderAdapter(HostAdapter):
    """Adobe Media Encoder。"""

    host_type = HostType.media_encoder
    cep_host_id = "AME"
    executable_name = "Adobe Media Encoder.exe"
    process_names = (
        "Adobe Media Encoder.exe",
        "Adobe Media Encoder",
    )
    registry_locations = AME_REGISTRY_LOCATIONS

    def describe(self) -> str:
        return "Adobe Media Encoder（CEP 标识 AME）"
