"""CEP manifest 必需元素校验（说明书 §4.3）。

部署前的硬性检查项。特别地，``--mixed-context`` 缺失是 CEP 开发中最常见的
"``require`` is not defined" 根因，因此它被列为**阻断性**检查项，而不是警告。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

__all__ = ["ManifestIssue", "ManifestCheck", "check_manifest", "check_manifest_text"]

_SEMVER = re.compile(r"^\d+(\.\d+){1,3}([-+][0-9A-Za-z.-]+)?$")
_VERSION_RANGE = re.compile(r"^\s*[\[\(].*[\]\)]\s*$|^\d+(\.\d+)*$")


@dataclass
class ManifestIssue:
    """一条 manifest 问题。``blocking`` 为真时拒绝部署。"""

    code: str
    message: str
    hint: str
    blocking: bool = True

    def to_public(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "blocking": self.blocking,
        }


@dataclass
class ManifestCheck:
    ok: bool
    issues: list[ManifestIssue] = field(default_factory=list)
    bundle_id: str | None = None
    bundle_version: str | None = None
    hosts: list[str] = field(default_factory=list)
    cef_params: list[str] = field(default_factory=list)
    ui_type: str | None = None
    main_path: str | None = None
    csxs_version: str | None = None
    csxs_runtimes: list[str] = field(default_factory=list)

    @property
    def blocking_issues(self) -> list[ManifestIssue]:
        return [issue for issue in self.issues if issue.blocking]

    @property
    def warnings(self) -> list[ManifestIssue]:
        return [issue for issue in self.issues if not issue.blocking]

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "hosts": self.hosts,
            "cef_params": self.cef_params,
            "ui_type": self.ui_type,
            "main_path": self.main_path,
            "csxs_version": self.csxs_version,
            "issues": [issue.to_public() for issue in self.issues],
        }

    def summary(self) -> str:
        if self.ok:
            return "manifest 必需元素齐全"
        return "；".join(f"{issue.code}: {issue.message}" for issue in self.blocking_issues)


def _text(node: ET.Element | None) -> str | None:
    if node is None:
        return None
    if node.text is not None and node.text.strip():
        return node.text.strip()
    # 兼容把值写成属性的写法（少见，但出现过）：<MainPath Value="./index.html"/>
    for attribute in ("Value", "value"):
        found = node.get(attribute)
        if found:
            return found.strip()
    return None


def _find_value(root: ET.Element, tag: str) -> str | None:
    """按标签名递归查找，并同时接受"元素文本"与"Value 属性"两种写法。

    CEP 规范里 ``MainPath`` 位于 ``DispatchInfo/Resources/MainPath``、值在元素文本中，
    而 ``RequiredRuntime`` 位于 ``RequiredRuntimeList`` 下、名字与版本在属性上。
    早期版本的本模块按错误的层级/属性假设去读，会把**完全合规**的清单判为不合规；
    这里统一按"递归查找 + 两种写法都接受"实现。
    """
    for node in root.iter(tag):
        value = _text(node)
        if value:
            return value
    return None


def _collect_runtimes(root: ET.Element) -> list[tuple[str | None, str | None]]:
    found: list[tuple[str | None, str | None]] = []
    for node in root.iter():
        if node.tag in ("RequiredRuntime", "Runtime"):
            version = node.get("Version") or (node.text or "").strip() or None
            found.append((node.get("Name"), version))
    return found


def check_manifest_text(text: str, source: str = "<memory>") -> ManifestCheck:
    """校验一份 manifest XML 文本。"""
    issues: list[ManifestIssue] = []
    check = ManifestCheck(ok=False)

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        issues.append(
            ManifestIssue(
                code="MANIFEST_XML_INVALID",
                message=f"manifest 不是合法 XML：{exc}（{source}）",
                hint="用浏览器或 XML 工具检查 CSXS/manifest.xml 的标签闭合",
            )
        )
        check.issues = issues
        return check

    if root.tag != "ExtensionManifest":
        issues.append(
            ManifestIssue(
                code="MANIFEST_ROOT",
                message=f"根元素应为 ExtensionManifest，实际为 {root.tag}",
                hint="CEP 的清单根元素固定是 ExtensionManifest",
            )
        )

    check.bundle_id = root.get("ExtensionBundleId")
    check.bundle_version = root.get("ExtensionBundleVersion")

    if not check.bundle_id:
        issues.append(
            ManifestIssue(
                code="MANIFEST_BUNDLE_ID",
                message="ExtensionManifest 缺少 ExtensionBundleId",
                hint='补上 ExtensionBundleId，例如 "com.dsh.adobe.bridge"',
            )
        )
    elif "." not in check.bundle_id:
        issues.append(
            ManifestIssue(
                code="MANIFEST_BUNDLE_ID",
                message=f"ExtensionBundleId 不像反向域名：{check.bundle_id}",
                hint="使用 com.<组织>.<产品> 形式，避免与其它扩展冲突",
                blocking=False,
            )
        )

    if not check.bundle_version:
        issues.append(
            ManifestIssue(
                code="MANIFEST_BUNDLE_VERSION",
                message="ExtensionManifest 缺少 ExtensionBundleVersion",
                hint="补上语义化版本号，例如 1.1.0",
            )
        )
    elif not _SEMVER.match(check.bundle_version):
        issues.append(
            ManifestIssue(
                code="MANIFEST_BUNDLE_VERSION",
                message=f"ExtensionBundleVersion 不是语义化版本：{check.bundle_version}",
                hint="形如 1.1.0（至少两段数字）",
                blocking=False,
            )
        )

    # ---- HostList -------------------------------------------------------
    hosts: list[str] = []
    host_entries: list[ET.Element] = []
    for host in root.iter("Host"):
        name = host.get("Name")
        if name:
            hosts.append(name)
            host_entries.append(host)
    check.hosts = sorted(set(hosts))

    if "PPRO" not in check.hosts:
        issues.append(
            ManifestIssue(
                code="MANIFEST_HOSTLIST",
                message="HostList 中没有 PPRO 宿主",
                hint='在 <HostList> 中加入 <Host Name="PPRO" Version="[22.0,99.9]"/>',
            )
        )
    for host in host_entries:
        if host.get("Name") == "PPRO":
            version_range = host.get("Version")
            if not version_range:
                issues.append(
                    ManifestIssue(
                        code="MANIFEST_HOSTLIST",
                        message="PPRO 宿主缺少 Version 版本范围",
                        hint='写出覆盖目标版本的范围，例如 Version="[22.0,99.9]"',
                    )
                )
            elif not _VERSION_RANGE.match(version_range):
                issues.append(
                    ManifestIssue(
                        code="MANIFEST_HOSTLIST",
                        message=f"PPRO 的 Version 不是合法范围：{version_range}",
                        hint='形如 "[22.0,99.9]" 或 "25.0"',
                        blocking=False,
                    )
                )

    # ---- RequiredRuntime （CEP 规范：RequiredRuntimeList/RequiredRuntime，Name/Version 为属性）----
    for name, version in _collect_runtimes(root):
        if name == "CSXS":
            check.csxs_version = version
            check.csxs_runtimes.append(version or "")
        if not version:
            issues.append(
                ManifestIssue(
                    code="MANIFEST_RUNTIME",
                    message=f"RequiredRuntime {name} 缺少 Version",
                    hint='例如 <RequiredRuntime Name="CSXS" Version="9.0"/>',
                )
            )
    if check.csxs_version is None:
        issues.append(
            ManifestIssue(
                code="MANIFEST_RUNTIME",
                message="RequiredRuntime 中没有声明 CSXS 运行时",
                hint="加入 <RequiredRuntimeList><RequiredRuntime Name=\"CSXS\" Version=\"9.0\""
                "/></RequiredRuntimeList>",
            )
        )

    # ---- MainPath（CEP 规范：DispatchInfo/Resources/MainPath，值在元素文本中）----
    check.main_path = _find_value(root, "MainPath")
    if not check.main_path:
        issues.append(
            ManifestIssue(
                code="MANIFEST_MAINPATH",
                message="缺少 MainPath",
                hint='在 DispatchInfo/Resources 下加入 <MainPath>./index.html</MainPath>',
            )
        )
    elif not check.main_path.lower().split("?")[0].endswith((".html", ".htm")):
        issues.append(
            ManifestIssue(
                code="MANIFEST_MAINPATH",
                message=f"MainPath 应指向 HTML 面板入口，实际为 {check.main_path}",
                hint='通常写成 <MainPath>./index.html</MainPath>',
                blocking=False,
            )
        )

    # ---- CEFCommandLine（阻断项） --------------------------------------
    cef_params: list[str] = []
    for parameter in root.iter("Parameter"):
        value = (parameter.text or "").strip()
        if value:
            cef_params.append(value)
    check.cef_params = cef_params

    if "--enable-nodejs" not in cef_params:
        issues.append(
            ManifestIssue(
                code="MANIFEST_CEF_NODEJS",
                message="CEFCommandLine 缺少 --enable-nodejs",
                hint="面板需要 Node 运行时来启动 WebSocket 服务，必须启用",
            )
        )
    if "--mixed-context" not in cef_params:
        issues.append(
            ManifestIssue(
                code="MANIFEST_CEF_MIXED_CONTEXT",
                message="CEFCommandLine 缺少 --mixed-context",
                hint="这是 CEP 中 'require is not defined' 的最常见根因："
                "CEP 9+ 需要 --mixed-context 才能让面板 window 作用域使用 require",
            )
        )

    # ---- UI/Type --------------------------------------------------------
    ui_type_node = None
    for ui in root.iter("UI"):
        type_node = ui.find("Type")
        if type_node is not None:
            ui_type_node = type_node
            break
    check.ui_type = _text(ui_type_node)
    if check.ui_type != "Panel":
        issues.append(
            ManifestIssue(
                code="MANIFEST_UI_TYPE",
                message=(
                    "UI/Type 应为 Panel（可停靠面板）"
                    f"，实际为 {check.ui_type or '（缺失）'}"
                ),
                hint="写成 <UI><Type>Panel</Type>...</UI>",
            )
        )

    # ---- Geometry -------------------------------------------------------
    geometry_found = next(root.iter("Geometry"), None) is not None
    size_ok = False
    for geometry in root.iter("Geometry"):
        size = geometry.find("Size")
        if size is not None and size.find("Width") is not None and size.find("Height") is not None:
            size_ok = True
    if not geometry_found or not size_ok:
        issues.append(
            ManifestIssue(
                code="MANIFEST_GEOMETRY",
                message="缺少 Geometry/Size 的初始尺寸",
                hint="加入 <Geometry><Size><Width>480</Width><Height>640</Height></Size></Geometry>",
                blocking=False,
            )
        )

    check.issues = issues
    check.ok = not check.blocking_issues
    return check


def check_manifest(path: str | Path) -> ManifestCheck:
    """校验磁盘上的 manifest 文件。"""
    manifest = Path(path)
    if manifest.is_dir():
        manifest = manifest / "CSXS" / "manifest.xml"
    if not manifest.is_file():
        return ManifestCheck(
            ok=False,
            issues=[
                ManifestIssue(
                    code="MANIFEST_MISSING",
                    message=f"找不到 manifest 文件：{manifest}",
                    hint="确认 cep-extension/CSXS/manifest.xml 存在且随面板一起部署",
                )
            ],
        )
    return check_manifest_text(manifest.read_text(encoding="utf-8"), str(manifest))


def iter_issues(issues: Iterable[ManifestIssue]) -> list[str]:  # pragma: no cover - 便利函数
    return [f"{issue.code}: {issue.message}" for issue in issues]
