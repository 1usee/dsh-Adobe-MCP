"""宿主发现诊断：逐条打印每个注册表来源读到了什么。

当 ``host_list`` 报"未安装"但用户坚称装了宿主时，用这个脚本取证——
它把**每一条**证据来源与推导结果都打出来，而不是只给一个结论。

用法::

    python scripts/diagnose_hosts.py
    python scripts/diagnose_hosts.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from dsh_adobe_mcp.hosts import PremiereAdapter, adapter_for
from dsh_adobe_mcp.hosts.base import HostType
from dsh_adobe_mcp.hosts.registry import (
    UNINSTALL_PATH,
    RegistryLocation,
    app_paths_location,
    candidate_install_dirs,
    default_reader,
    describe_location,
    normalize_install_path,
    uninstall_locations,
)

APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
UNINSTALL = rf"SOFTWARE\{UNINSTALL_PATH}"


def dump_reader(reader, location: RegistryLocation) -> dict:
    return {
        "location": describe_location(location),
        "subkeys": reader.subkeys(location),
        "value": reader.value(location, location.value_name),
    }


def diagnose(host_type: HostType) -> dict:
    adapter = adapter_for(host_type)
    reader = default_reader()
    report: dict = {
        "host": host_type.value,
        "executable_name": adapter.executable_name,
        "primary_locations": [],
        "app_paths": None,
        "uninstall_entries": [],
        "candidates": [],
        "discovered": [],
    }

    for location in adapter.registry_locations:
        entry = dump_reader(reader, location)
        versions = [name for name in entry["subkeys"] if any(ch.isdigit() for ch in name)]
        entry["version_subkeys"] = versions
        entry["version_values"] = {}
        for version in versions:
            child = RegistryLocation(
                hive=location.hive,
                path=f"{location.path}\\{version}",
                value_name=location.value_name,
                view=location.view,
            )
            entry["version_values"][version] = {
                "install_path": reader.value(child, location.value_name),
                "all_values": {
                    name: reader.value(child, name)
                    for name in ("InstallPath", "SequencePresetsPath", "CommonExporterPresetsPath")
                },
            }
        report["primary_locations"].append(entry)

    app_paths = app_paths_location(adapter.executable_name, "64")
    raw = reader.value(app_paths, "(default)")
    report["app_paths"] = {"location": describe_location(app_paths), "value": raw}
    if raw:
        path = normalize_install_path(raw)
        report["candidates"].append(
            {
                "from": "App Paths",
                "raw": raw,
                "interpretations": [
                    {"path": str(candidate), "exists": candidate.is_dir()}
                    for candidate in dict.fromkeys(
                        ([path] if path else [])
                        + candidate_install_dirs(path, adapter.product_dir_names)
                    )
                ],
            }
        )

    # 证据 3：Uninstall 段（两套视图都扫——本机实测 PPro 记录只在 32 位视图里）
    for uninstall_root in uninstall_locations():
        view_label = "64 位视图" if uninstall_root.view == "64" else "32 位视图"
        report.setdefault("uninstall_views", []).append(
            {"view": view_label, "location": describe_location(uninstall_root), "matched": 0}
        )
        for subkey in reader.subkeys(uninstall_root):
            entry = RegistryLocation(
                hive=uninstall_root.hive,
                path=f"{uninstall_root.path}\\{subkey}",
                value_name="DisplayName",
                view=uninstall_root.view,
            )
            display = reader.value(entry, "DisplayName") or ""
            if adapter.uninstall_match.lower() not in display.lower():
                continue
            record = {
                "view": view_label,
                "subkey": subkey,
                "DisplayName": display,
                "DisplayVersion": reader.value(entry, "DisplayVersion"),
                "InstallLocation": reader.value(entry, "InstallLocation"),
            }
            report["uninstall_entries"].append(record)
            report["uninstall_views"][-1]["matched"] += 1
            if record["InstallLocation"]:
                path = normalize_install_path(record["InstallLocation"])
                report["candidates"].append(
                    {
                        "from": f"{view_label} Uninstall\\{subkey}",
                        "raw": record["InstallLocation"],
                        "interpretations": [
                            {"path": str(candidate), "exists": candidate.is_dir()}
                            for candidate in ([path] if path else [])
                            + candidate_install_dirs(path, adapter.product_dir_names)
                        ],
                    }
                )

    for descriptor in adapter.discover(reader):
        running, pid = adapter.is_running(descriptor)
        report["discovered"].append(
            {**descriptor.to_public(), "pid": pid, "running": running}
        )
    report["conclusion"] = (
        f"发现 {len(report['discovered'])} 个安装"
        if report["discovered"]
        else "没有发现任何安装 —— 请对照上文各来源，确认哪个环节缺少注册表记录"
    )
    return report


def print_report(report: dict) -> None:
    print(f"===== 宿主：{report['host']}（可执行文件 {report['executable_name']}）=====")
    for entry in report["primary_locations"]:
        print(f"\n[主位置] {entry['location']}")
        print(f"  子键：{entry['subkeys'] or '（无）'}")
        if entry["version_values"]:
            for version, values in entry["version_values"].items():
                print(f"  {version}: InstallPath={values['install_path']!r}")
                for name, value in values["all_values"].items():
                    if value and name != "InstallPath":
                        print(f"      {name} = {value}")
        else:
            print(f"  主键值 {entry['location']} = {entry['value']!r}")

    print(f"\n[证据 2] App Paths: {report['app_paths']['location']}")
    print(f"  值 = {report['app_paths']['value']!r}")

    print(f"\n[证据 3] Uninstall 段匹配项：{len(report['uninstall_entries'])} 条")
    for view in report.get("uninstall_views", []):
        print(f"  {view['view']}：{view['location']} → 命中 {view['matched']} 条")
    for record in report["uninstall_entries"]:
        print(
            f"  [{record['view']}] {record['subkey']}: {record['DisplayName']} "
            f"(版本 {record['DisplayVersion']}, InstallLocation={record['InstallLocation']!r})"
        )

    print("\n[路径解释]")
    for candidate in report["candidates"]:
        print(f"  来自 {candidate['from']}：{candidate['raw']!r}")
        for item in candidate["interpretations"]:
            mark = "存在" if item["exists"] else "不存在"
            print(f"      {item['path']}  ->  {mark}")

    print("\n[结论]")
    if report["discovered"]:
        for item in report["discovered"]:
            print(
                f"  {item['version']} | {item['install_path']}\n"
                f"      可执行文件：{item['executable']}（存在={item['executable_exists']}）\n"
                f"      运行中：{item['running']}  pid={item['pid']}"
            )
            for note in item["notes"]:
                print(f"      · {note}")
    print(f"  {report['conclusion']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="宿主发现诊断")
    parser.add_argument("--host", default="premiere", help="premiere / ame")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    adapter = PremiereAdapter() if args.host in ("premiere", "ppro", "pr") else adapter_for(args.host)
    report = diagnose(adapter.host_type)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(report)
    return 3 if not report["discovered"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
