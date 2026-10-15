"""宿主发现与选择：注册表读取、多版本歧义、进程探测（说明书 §4.1）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dsh_adobe_mcp.hosts import adapter_for, parse_host_key, version_sort_key
from dsh_adobe_mcp.hosts.base import HostType, RegistryLocation
from dsh_adobe_mcp.hosts.media_encoder import MediaEncoderAdapter
from dsh_adobe_mcp.hosts.premiere import PREMIERE_REGISTRY_LOCATIONS, PremiereAdapter
from dsh_adobe_mcp.hosts.registry import DictReader, read_host_installs


@pytest.fixture
def installs(tmp_path: Path) -> DictReader:
    values = {}
    for version in ("25.0", "24.6", "2023"):
        install = tmp_path / f"PPro {version}"
        install.mkdir(parents=True, exist_ok=True)
        (install / "Adobe Premiere Pro.exe").write_bytes(b"stub")
        values[rf"HKLM\SOFTWARE\Adobe\Premiere Pro\{version}"] = {"InstallPath": str(install)}
    values[r"HKLM\SOFTWARE\Adobe\Adobe Media Encoder\25.0"] = {
        "InstallPath": str(tmp_path / "AME 25.0")
    }
    return DictReader(values)


def test_discover_reads_registry_only(installs: DictReader):
    adapter = PremiereAdapter()
    descriptors = adapter.discover(installs)
    assert [item.version for item in descriptors] == ["25.0", "24.6", "2023"]
    assert all(item.cep_host_id == "PPRO" for item in descriptors)
    assert all(item.executable.endswith("Adobe Premiere Pro.exe") for item in descriptors)
    assert all(Path(item.executable).is_file() for item in descriptors)


def test_missing_registry_returns_empty():
    assert PremiereAdapter().discover(DictReader({})) == []


def test_missing_executable_is_noted_not_fatal(tmp_path: Path):
    reader = DictReader(
        {r"HKLM\SOFTWARE\Adobe\Premiere Pro\25.0": {"InstallPath": str(tmp_path / "gone")}}
    )
    descriptors = PremiereAdapter().discover(reader)
    assert len(descriptors) == 1
    assert any("没有找到" in note for note in descriptors[0].notes)


def test_install_path_on_parent_key_without_version_subkey(tmp_path: Path):
    reader = DictReader(
        {r"HKLM\SOFTWARE\Adobe\Premiere Pro": {"InstallPath": str(tmp_path / "flat")}}
    )
    descriptors = PremiereAdapter().discover(reader)
    assert len(descriptors) == 1


def test_deduplicates_same_version_from_two_views(tmp_path: Path):
    install = tmp_path / "one"
    install.mkdir()
    reader = DictReader(
        {
            r"HKLM\SOFTWARE\Adobe\Premiere Pro\25.0": {"InstallPath": str(install)},
            r"HKLM\SOFTWARE\WOW6432Node\Adobe\Premiere Pro\25.0": {"InstallPath": str(install)},
        }
    )
    assert len(PremiereAdapter().discover(reader)) == 1


def test_select_highest_when_version_not_specified(installs: DictReader):
    adapter = PremiereAdapter()
    selection = adapter.select(adapter.discover(installs))
    assert selection.chosen is not None
    assert selection.chosen.version == "25.0"
    assert selection.ambiguous is True  # 多版本并存必须报告歧义
    assert "确认版本" in selection.note


def test_select_specific_version(installs: DictReader):
    adapter = PremiereAdapter()
    selection = adapter.select(adapter.discover(installs), "24.6")
    assert selection.chosen.version == "24.6"
    assert selection.ambiguous is False


def test_select_unknown_version_reports_available(installs: DictReader):
    adapter = PremiereAdapter()
    selection = adapter.select(adapter.discover(installs), "99.0")
    assert selection.chosen is None
    assert "未安装指定版本" in selection.note


def test_select_single_install_not_ambiguous(tmp_path: Path):
    install = tmp_path / "only"
    install.mkdir()
    reader = DictReader({r"HKLM\SOFTWARE\Adobe\Premiere Pro\25.0": {"InstallPath": str(install)}})
    selection = PremiereAdapter().select(PremiereAdapter().discover(reader))
    assert selection.ambiguous is False


def test_version_sort_key_handles_year_and_dotted():
    assert version_sort_key("25.0") > version_sort_key("24.6")
    assert version_sort_key("2024") > version_sort_key("2023")
    assert version_sort_key("25.1.2") > version_sort_key("25.1")


def test_parse_host_key_variants():
    assert parse_host_key(None) == (None, None)
    assert parse_host_key("premiere") == (HostType.premiere, None)
    assert parse_host_key("PPRO:25.0") == (HostType.premiere, "25.0")
    assert parse_host_key("ame") == (HostType.media_encoder, None)
    assert parse_host_key("25.0") == (None, "25.0")


def test_adapter_for_aliases():
    assert isinstance(adapter_for("premiere"), PremiereAdapter)
    assert isinstance(adapter_for("ame"), MediaEncoderAdapter)
    with pytest.raises(KeyError):
        adapter_for("after_effects")


def test_media_encoder_is_disabled_by_default_in_config():
    from dsh_adobe_mcp.config import Config

    assert Config().hosts.media_encoder.enabled is False
    assert Config().hosts.premiere.enabled is True


def test_registry_locations_cover_32bit_view():
    paths = [location.path for location in PREMIERE_REGISTRY_LOCATIONS]
    assert any("WOW6432Node" in path for path in paths)
    assert all(location.value_name == "InstallPath" for location in PREMIERE_REGISTRY_LOCATIONS)


def test_read_host_installs_reports_source():
    reader = DictReader({r"HKLM\SOFTWARE\Adobe\Premiere Pro\25.0": {"InstallPath": "C:\\x"}})
    installs = read_host_installs(
        (RegistryLocation(hive="HKLM", path=r"SOFTWARE\Adobe\Premiere Pro"),), reader
    )
    assert installs[0].source == r"HKLM\SOFTWARE\Adobe\Premiere Pro\25.0\InstallPath"


def test_adapter_subclass_validation():
    with pytest.raises(TypeError):

        class Incomplete(PremiereAdapter):
            __abstract__ = False
            host_type = None  # type: ignore[assignment]
            cep_host_id = None  # type: ignore[assignment]
