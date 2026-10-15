"""CEP manifest 校验（说明书 §4.3）。

特别注意两条曾经踩过的坑，这里都有回归测试：
* ``RequiredRuntimeList/RequiredRuntime`` 的 Name/Version 是**属性**；
* ``DispatchInfo/Resources/MainPath`` 的值是**元素文本**。
按错误的层级或属性去读，会把一份完全合规的清单判为不合规。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dsh_adobe_mcp.bridge.manifest_check import check_manifest, check_manifest_text

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_MANIFEST = REPO_ROOT / "cep-extension" / "CSXS" / "manifest.xml"

GOOD = """\
<?xml version="1.0" encoding="UTF-8"?>
<ExtensionManifest Version="7.0" ExtensionBundleId="com.example.bridge"
                   ExtensionBundleVersion="1.0.0" ExtensionBundleName="Bridge">
  <ExtensionList>
    <Extension Id="com.example.bridge.panel" Version="1.0.0"/>
  </ExtensionList>
  <ExecutionEnvironment>
    <HostList>
      <Host Name="PPRO" Version="[13.0,99.9]"/>
    </HostList>
    <LocaleList><Locale Code="All"/></LocaleList>
    <RequiredRuntimeList>
      <RequiredRuntime Name="CSXS" Version="9.0"/>
    </RequiredRuntimeList>
  </ExecutionEnvironment>
  <DispatchInfoList>
    <Extension Id="com.example.bridge.panel">
      <DispatchInfo>
        <Resources>
          <MainPath>./index.html</MainPath>
          <CEFCommandLine>
            <Parameter>--enable-nodejs</Parameter>
            <Parameter>--mixed-context</Parameter>
          </CEFCommandLine>
        </Resources>
        <Lifecycle><AutoVisible>true</AutoVisible></Lifecycle>
        <UI>
          <Type>Panel</Type>
          <Menu>Bridge</Menu>
          <Geometry><Size><Width>470</Width><Height>360</Height></Size></Geometry>
        </UI>
      </DispatchInfo>
    </Extension>
  </DispatchInfoList>
</ExtensionManifest>
"""


def issue_codes(check) -> set[str]:
    return {issue.code for issue in check.issues}


def test_real_repository_manifest_is_valid():
    check = check_manifest(REAL_MANIFEST)
    assert check.ok is True, check.summary()
    assert check.blocking_issues == []
    assert check.bundle_id == "com.dsh.adobe.bridge"
    assert check.hosts == ["PPRO"]
    assert check.csxs_version
    assert check.main_path
    assert check.ui_type == "Panel"
    assert "--enable-nodejs" in check.cef_params
    assert "--mixed-context" in check.cef_params


def test_standard_manifest_parses_required_runtime_and_mainpath():
    check = check_manifest_text(GOOD)
    assert check.ok is True, check.summary()
    assert check.csxs_version == "9.0"
    assert check.main_path == "./index.html"


def test_attribute_style_is_also_accepted():
    """兼容把值写成属性的写法（向后兼容，避免把合规清单判成不合规）。"""
    attribute_style = GOOD.replace(
        "<MainPath>./index.html</MainPath>", '<MainPath Value="./index.html"/>'
    )
    check = check_manifest_text(attribute_style)
    assert check.ok is True, check.summary()
    assert check.main_path == "./index.html"


def test_missing_mixed_context_is_blocking():
    """这是 CEP 里 'require is not defined' 的最常见根因，必须是阻断项。"""
    broken = GOOD.replace("<Parameter>--mixed-context</Parameter>", "")
    check = check_manifest_text(broken)
    assert check.ok is False
    assert "MANIFEST_CEF_MIXED_CONTEXT" in issue_codes(check)
    assert any("require" in issue.hint for issue in check.blocking_issues)


def test_missing_enable_nodejs_is_blocking():
    broken = GOOD.replace("<Parameter>--enable-nodejs</Parameter>", "")
    check = check_manifest_text(broken)
    assert check.ok is False
    assert "MANIFEST_CEF_NODEJS" in issue_codes(check)


def test_missing_ppro_host_is_blocking():
    broken = GOOD.replace('<Host Name="PPRO" Version="[13.0,99.9]"/>', "")
    check = check_manifest_text(broken)
    assert check.ok is False
    assert "MANIFEST_HOSTLIST" in issue_codes(check)


def test_missing_csxs_runtime_is_blocking():
    broken = GOOD.replace('<RequiredRuntime Name="CSXS" Version="9.0"/>', "")
    check = check_manifest_text(broken)
    assert check.ok is False
    assert "MANIFEST_RUNTIME" in issue_codes(check)


def test_missing_main_path_is_blocking():
    broken = GOOD.replace("<MainPath>./index.html</MainPath>", "")
    check = check_manifest_text(broken)
    assert check.ok is False
    assert "MANIFEST_MAINPATH" in issue_codes(check)


def test_wrong_ui_type_is_blocking():
    broken = GOOD.replace("<Type>Panel</Type>", "<Type>ModalDialog</Type>")
    check = check_manifest_text(broken)
    assert check.ok is False
    assert "MANIFEST_UI_TYPE" in issue_codes(check)


def test_missing_bundle_id_is_blocking():
    broken = GOOD.replace('ExtensionBundleId="com.example.bridge"', "")
    check = check_manifest_text(broken)
    assert check.ok is False
    assert "MANIFEST_BUNDLE_ID" in issue_codes(check)


def test_missing_geometry_is_only_a_warning():
    broken = GOOD.replace(
        "<Geometry><Size><Width>470</Width><Height>360</Height></Size></Geometry>", ""
    )
    check = check_manifest_text(broken)
    assert check.ok is True
    assert "MANIFEST_GEOMETRY" in issue_codes(check)
    assert any(issue.code == "MANIFEST_GEOMETRY" for issue in check.warnings)


def test_invalid_xml_is_reported_not_crashed():
    check = check_manifest_text("<ExtensionManifest><unclosed>")
    assert check.ok is False
    assert "MANIFEST_XML_INVALID" in issue_codes(check)


def test_non_html_main_path_is_warning():
    broken = GOOD.replace("<MainPath>./index.html</MainPath>", "<MainPath>./main.js</MainPath>")
    check = check_manifest_text(broken)
    assert check.ok is True
    assert "MANIFEST_MAINPATH" in issue_codes(check)


def test_missing_file_reports_clearly(tmp_path: Path):
    check = check_manifest(tmp_path / "nope")
    assert check.ok is False
    assert "MANIFEST_MISSING" in issue_codes(check)


def test_check_accepts_directory(tmp_path: Path):
    target = tmp_path / "CSXS"
    target.mkdir()
    (target / "manifest.xml").write_text(GOOD, encoding="utf-8")
    assert check_manifest(tmp_path).ok is True


def test_public_report_is_json_serialisable():
    check = check_manifest(REAL_MANIFEST)
    payload = check.to_public()
    assert payload["ok"] is True
    assert isinstance(payload["issues"], list)
    assert payload["cef_params"]


@pytest.mark.parametrize("field", ["bundle_id", "bundle_version", "csxs_version", "main_path", "ui_type"])
def test_summary_fields_present(field: str):
    check = check_manifest(REAL_MANIFEST)
    assert getattr(check, field)
