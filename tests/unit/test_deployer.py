"""CEP 面板部署：清单哈希、原子替换、备份与回滚（说明书 §4.2）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dsh_adobe_mcp.bridge.deployer import (
    CepDeployer,
    DeployError,
    directory_digest,
    directory_manifest,
    player_debug_registry_path,
    set_player_debug_mode,
)

MANIFEST = """\
<?xml version="1.0" encoding="UTF-8"?>
<ExtensionManifest Version="7.0" ExtensionBundleId="com.dsh.adobe.bridge"
                   ExtensionBundleVersion="1.1.0">
  <ExtensionList><Extension Id="com.dsh.adobe.bridge.panel" Version="1.1.0"/></ExtensionList>
  <ExecutionEnvironment>
    <HostList><Host Name="PPRO" Version="[13.0,99.9]"/></HostList>
    <RequiredRuntimeList><RequiredRuntime Name="CSXS" Version="9.0"/></RequiredRuntimeList>
  </ExecutionEnvironment>
  <DispatchInfoList>
    <Extension Id="com.dsh.adobe.bridge.panel">
      <DispatchInfo>
        <Resources>
          <MainPath>./index.html</MainPath>
          <CEFCommandLine>
            <Parameter>--enable-nodejs</Parameter>
            <Parameter>--mixed-context</Parameter>
          </CEFCommandLine>
        </Resources>
        <UI><Type>Panel</Type><Geometry><Size><Width>470</Width><Height>360</Height></Size></Geometry></UI>
      </DispatchInfo>
    </Extension>
  </DispatchInfoList>
</ExtensionManifest>
"""


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "cep-extension"
    (root / "CSXS").mkdir(parents=True)
    (root / "CSXS" / "manifest.xml").write_text(MANIFEST, encoding="utf-8")
    (root / "index.html").write_text("<html></html>", encoding="utf-8")
    (root / "js").mkdir()
    (root / "js" / "main.js").write_text("// panel", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("ignored", encoding="utf-8")
    return root


@pytest.fixture
def deployer(tmp_path: Path, source: Path) -> CepDeployer:
    return CepDeployer(
        source_dir=source,
        extensions_dir=tmp_path / "extensions",
        extension_id="com.dsh.adobe.bridge",
        backup_dir=tmp_path / "backup",
    )


def test_digest_is_stable_and_ignores_vcs(deployer: CepDeployer, source: Path):
    first = directory_digest(source)
    assert first == directory_digest(source)
    assert ".git" not in {relative for relative, _, _ in directory_manifest(source)}


def test_digest_changes_when_content_changes(deployer: CepDeployer, source: Path):
    before = directory_digest(source)
    (source / "js" / "main.js").write_text("// changed and longer", encoding="utf-8")
    assert directory_digest(source) != before


def test_plan_installs_when_target_missing(deployer: CepDeployer):
    plan = deployer.plan()
    assert plan.action == "install"
    assert plan.manifest.ok is True


def test_deploy_installs_and_then_skips(deployer: CepDeployer):
    result = deployer.deploy()
    assert result.action == "install"
    assert (deployer.target_dir / "index.html").is_file()
    assert (deployer.target_dir / "CSXS" / "manifest.xml").is_file()

    plan = deployer.plan()
    assert plan.action == "skip"
    again = deployer.deploy()
    assert again.action == "skip"


def test_deploy_replaces_and_keeps_backup(deployer: CepDeployer, source: Path):
    deployer.deploy()
    (source / "js" / "main.js").write_text("// v2 content", encoding="utf-8")
    plan = deployer.plan()
    assert plan.action == "replace"

    result = deployer.deploy()
    assert result.action == "replace"
    assert result.backup_dir is not None
    assert Path(result.backup_dir).is_dir()
    assert (Path(result.backup_dir) / "js" / "main.js").read_text(encoding="utf-8") == "// panel"
    assert (deployer.target_dir / "js" / "main.js").read_text(encoding="utf-8") == "// v2 content"


def test_force_deploys_even_when_in_sync(deployer: CepDeployer):
    deployer.deploy()
    assert deployer.plan(force=True).action == "replace"


def test_invalid_manifest_aborts_deployment(deployer: CepDeployer, source: Path):
    (source / "CSXS" / "manifest.xml").write_text(
        MANIFEST.replace("<Parameter>--mixed-context</Parameter>", ""), encoding="utf-8"
    )
    plan = deployer.plan()
    assert plan.action == "abort"
    with pytest.raises(DeployError) as excinfo:
        deployer.deploy()
    assert excinfo.value.code == "PANEL_MANIFEST_INVALID"
    assert not deployer.target_dir.exists()


def test_missing_source_dir_is_reported(tmp_path: Path):
    """源目录缺失 → manifest 校验直接失败，plan 给出 abort，deploy 抛错且不落盘。"""
    deployer = CepDeployer(
        source_dir=tmp_path / "missing",
        extensions_dir=tmp_path / "extensions",
        extension_id="x",
    )
    plan = deployer.plan()
    assert plan.action == "abort"
    assert "manifest 校验失败" in plan.reason
    with pytest.raises(DeployError) as excinfo:
        deployer.deploy()
    assert excinfo.value.code == "PANEL_MANIFEST_INVALID"
    assert not (tmp_path / "extensions" / "x").exists()


def test_unwritable_extensions_dir_is_reported_with_fallback(deployer: CepDeployer, monkeypatch):
    monkeypatch.setattr(CepDeployer, "_writable", lambda self: (False, "Access denied"))
    with pytest.raises(DeployError) as excinfo:
        deployer.deploy()
    assert excinfo.value.code == "PANEL_DEPLOY_BLOCKED"
    assert "dev_install_cep.ps1" in excinfo.value.remedy


def test_permission_error_during_replace_becomes_blocked(deployer: CepDeployer, monkeypatch):
    deployer.deploy()

    def boom(self, source: Path, target: Path) -> None:
        raise PermissionError("拒绝访问")

    monkeypatch.setattr(CepDeployer, "_move_dir", boom)
    (deployer.source_dir / "index.html").write_text("<html>v2</html>", encoding="utf-8")
    with pytest.raises(DeployError) as excinfo:
        deployer.deploy()
    assert excinfo.value.code in ("PANEL_DEPLOY_BLOCKED", "PANEL_DEPLOY_FAILED")
    assert "dev_install_cep.ps1" in excinfo.value.remedy
    # 失败必须自动还原上一版本，不能留下半个面板
    assert (deployer.target_dir / "index.html").read_text(encoding="utf-8") == "<html></html>"


def test_rollback_restores_previous_version(deployer: CepDeployer, source: Path):
    deployer.deploy()
    (source / "index.html").write_text("<html>v2</html>", encoding="utf-8")
    deployer.deploy()
    assert (deployer.target_dir / "index.html").read_text(encoding="utf-8") == "<html>v2</html>"
    result = deployer.rollback()
    assert result.action == "rollback"
    assert (deployer.target_dir / "index.html").read_text(encoding="utf-8") == "<html></html>"


def test_rollback_without_backup_is_reported(tmp_path: Path, source: Path):
    deployer = CepDeployer(
        source_dir=source,
        extensions_dir=tmp_path / "extensions",
        extension_id="com.dsh.adobe.bridge",
        backup_dir=tmp_path / "backup",
    )
    deployer.deploy()
    with pytest.raises(DeployError):
        deployer.rollback()


def test_status_reports_sync_state(deployer: CepDeployer):
    status = deployer.status()
    assert status["installed"] is False
    assert status["in_sync"] is False
    assert status["manifest"]["ok"] is True
    deployer.deploy()
    status = deployer.status()
    assert status["installed"] is True
    assert status["in_sync"] is True


def test_backups_are_pruned(deployer: CepDeployer, source: Path):
    for index in range(4):
        (source / "index.html").write_text(f"<html>{index}</html>", encoding="utf-8")
        deployer.deploy()
    backups = [path for path in deployer.backup_dir.iterdir() if path.is_dir()]
    assert len(backups) <= 2


def test_staging_directory_is_not_left_behind(deployer: CepDeployer):
    deployer.deploy()
    leftovers = [path for path in deployer.extensions_dir.iterdir() if path.name.startswith(".")]
    assert leftovers == []


def test_player_debug_registry_path():
    assert player_debug_registry_path(9) == r"Software\Adobe\CSXS.9"
    assert player_debug_registry_path(12) == r"Software\Adobe\CSXS.12"


def test_set_player_debug_mode_uses_injected_writer():
    written: dict[str, str] = {}

    def writer(path: str, value: str) -> None:
        written[path] = value

    result = set_player_debug_mode((9, 11), writer=writer)
    assert result["ok"] is True
    assert written == {r"Software\Adobe\CSXS.9": "1", r"Software\Adobe\CSXS.11": "1"}
    assert "授权" in result["hint"]


def test_set_player_debug_mode_reports_writer_failures():
    def writer(path: str, value: str) -> None:
        raise OSError("拒绝访问")

    result = set_player_debug_mode((9,), writer=writer)
    assert result["applied"] == []
    assert any("CSXS.9" in item for item in result["errors"])
