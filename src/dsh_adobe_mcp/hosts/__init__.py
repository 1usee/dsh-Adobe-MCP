"""宿主发现与适配（说明书 §4.1）。"""

from __future__ import annotations

from .base import (
    CEP_HOST_IDS,
    HOST_ALIASES,
    HostAdapter,
    HostDescriptor,
    HostSelection,
    HostType,
    RegistryLocation,
    parse_host_key,
    version_sort_key,
)
from .media_encoder import MediaEncoderAdapter
from .premiere import PremiereAdapter
from .registry import DictReader, RegistryReader, WinregReader, default_reader

__all__ = [
    "HostType",
    "HostDescriptor",
    "HostSelection",
    "HostAdapter",
    "RegistryLocation",
    "CEP_HOST_IDS",
    "HOST_ALIASES",
    "parse_host_key",
    "version_sort_key",
    "PremiereAdapter",
    "MediaEncoderAdapter",
    "RegistryReader",
    "WinregReader",
    "DictReader",
    "default_reader",
    "adapter_for",
    "all_adapters",
]

_ADAPTERS: dict[HostType, HostAdapter] = {
    HostType.premiere: PremiereAdapter(),
    HostType.media_encoder: MediaEncoderAdapter(),
}


def adapter_for(host_type: HostType | str) -> HostAdapter:
    """按宿主类型取适配器。"""
    if isinstance(host_type, str):
        resolved = HOST_ALIASES.get(host_type.lower())
        if resolved is None:
            try:
                resolved = HostType(host_type)
            except ValueError as exc:
                raise KeyError(f"未知宿主类型：{host_type}") from exc
        host_type = resolved
    return _ADAPTERS[host_type]


def all_adapters() -> dict[HostType, HostAdapter]:
    return dict(_ADAPTERS)
