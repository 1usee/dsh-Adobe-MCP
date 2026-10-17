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
    assert installs[0].source == r"HKLM\SOFTWARE\Adobe\Premiere Pro\25.0\InstallPath (InstallPath)"


def test_adapter_subclass_validation():
    with pytest.raises(TypeError):

        class Incomplete(PremiereAdapter):
            __abstract__ = False
            host_type = None  # type: ignore[assignment]
            cep_host_id = None  # type: ignore[assignment]


# --------------------------------------------------------------------------
# 非默认安装路径（本机实测踩到的真实场景）
# --------------------------------------------------------------------------
def _layout(tmp_path: Path, product_dir: str) -> Path:
    """造一个"装到别的盘"的目录结构：<root>/premiere/Adobe/<产品目录>/Adobe Premiere Pro.exe"""
    install = tmp_path / "premiere" / "Adobe" / product_dir
    install.mkdir(parents=True, exist_ok=True)
    (install / "Adobe Premiere Pro.exe").write_bytes(b"stub")
    return install


def test_discovery_without_install_path_uses_app_paths(tmp_path: Path):
    """非默认盘符安装时 Adobe 不写 InstallPath，只有 App Paths —— 必须仍能发现。

    这是本机实测的真实形态：``HKLM\\SOFTWARE\\Adobe\\Premiere Pro\\23.0`` 下
    只有 SequencePresetsPath 等值，没有 InstallPath；真实位置登记在 App Paths 里。
    """
    install = _layout(tmp_path, "Adobe Premiere Pro 2023")
    reader = DictReader(
        {
            r"HKLM\SOFTWARE\Adobe\Premiere Pro\23.0": {
                "SequencePresetsPath": str(install / "Settings" / "SequencePresets")
            },
            r"HKLM\SOFTWARE\Adobe\Premiere Pro": {"CurrentVersion": "23.0"},
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Adobe Premiere Pro.exe": {
                "(default)": f'"{install / "Adobe Premiere Pro.exe"}"'
            },
        }
    )
    descriptors = PremiereAdapter().discover(reader)
    assert len(descriptors) == 1
    assert Path(descriptors[0].executable) == install / "Adobe Premiere Pro.exe"
    assert descriptors[0].executable_exists is True
    assert descriptors[0].version == "2023"
    assert any("App Paths" in note for note in descriptors[0].notes)


def test_discovery_without_install_path_uses_uninstall_install_location(tmp_path: Path):
    """只有 Uninstall 段的 InstallLocation 时也要能定位到真正的安装目录。

    InstallLocation 往往指向产品**父目录**（本机实测为 ``D:\\premiere\\Adobe``），
    必须与产品目录名拼起来才能得到安装目录。
    """
    install = _layout(tmp_path, "Adobe Premiere Pro 2023")
    parent = install.parent
    reader = DictReader(
        {
            r"HKLM\SOFTWARE\Adobe\Premiere Pro\23.0": {"SequencePresetsPath": "x"},
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PPRO_23_1": {
                "DisplayName": "Adobe Premiere Pro 2023",
                "DisplayVersion": "23.1",
                "InstallLocation": str(parent),
            },
        }
    )
    descriptors = PremiereAdapter().discover(reader)
    assert len(descriptors) == 1
    assert Path(descriptors[0].install_path) == install
    assert descriptors[0].executable_exists is True
    assert descriptors[0].version == "23.1"


def test_install_path_still_wins_when_present(installs: DictReader):
    """标准安装（有 InstallPath）时行为不变。"""
    descriptors = PremiereAdapter().discover(installs)
    assert len(descriptors) == 3
    assert all("InstallPath" in d.notes[0] for d in descriptors)


def test_non_premiere_uninstall_entries_are_ignored(tmp_path: Path):
    """Uninstall 段里的其它 Adobe 产品不能被当成 PPro。"""
    install = _layout(tmp_path, "Adobe Premiere Pro 2023")
    reader = DictReader(
        {
            r"HKLM\SOFTWARE\Adobe\Premiere Pro\23.0": {"SequencePresetsPath": "x"},
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Photoshop": {
                "DisplayName": "Adobe Photoshop 2024",
                "InstallLocation": str(install.parent),
            },
        }
    )
    assert PremiereAdapter().discover(reader) == []


def test_missing_executable_still_reported_for_diagnosis(tmp_path: Path):
    """注册表指向不存在的目录时仍登记（供 host_status 报告问题），但标记不可用。"""
    reader = DictReader(
        {
            r"HKLM\SOFTWARE\Adobe\Premiere Pro\23.0": {"SequencePresetsPath": "x"},
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PPRO_23_1": {
                "DisplayName": "Adobe Premiere Pro 2023",
                "InstallLocation": str(tmp_path / "gone"),
            },
        }
    )
    descriptors = PremiereAdapter().discover(reader)
    assert len(descriptors) == 1
    assert descriptors[0].executable_exists is False


def test_media_encoder_display_name_matching():
    reader = DictReader(
        {
            r"HKLM\SOFTWARE\Adobe\Adobe Media Encoder\25.0": {"SomethingElse": "1"},
            r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\AME_25": {
                "DisplayName": "Adobe Media Encoder 2025",
                "InstallLocation": "C:\\Program Files\\Adobe",
            },
        }
    )
    descriptors = MediaEncoderAdapter().discover(reader)
    assert len(descriptors) == 1
    assert "Uninstall" in descriptors[0].notes[0]
