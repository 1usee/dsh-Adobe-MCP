"""工具 schema 快照测试（说明书 §14.2 第 5 条）。

schema 是客户端看到的**公开契约**：参数改名、类型变化、必填性变化都会破坏兼容性。
因此这里把全部 56 个工具的 JSON schema 做快照，任何变化都必须显式更新快照——
防止"无意中改了客户端接口"。

有意变更时运行：

    pytest tests/contract/test_schema_snapshot.py --update-snapshots
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dsh_adobe_mcp.safety.policy import POLICY
from dsh_adobe_mcp.tools.registry import schema_catalog_sync

SNAPSHOT = Path(__file__).resolve().parent / "schemas.snapshot.json"


def normalize(catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """只保留对客户端有意义的字段，并按名字排序。"""
    normalized: dict[str, Any] = {}
    for name in sorted(catalog):
        schema = catalog[name] or {}
        properties = {
            key: schema.get("properties", {}).get(key)
            for key in sorted(schema.get("properties", {}))
        }
        normalized[name] = {
            "type": schema.get("type"),
            "properties": properties,
            "required": sorted(schema.get("required", []) or []),
        }
    return normalized


@pytest.fixture(scope="module")
def catalog() -> dict[str, Any]:
    return normalize(schema_catalog_sync())


def test_catalog_covers_every_tool(catalog: dict[str, Any]):
    assert set(catalog) == set(POLICY)
    assert len(catalog) == 56


def test_snapshot_matches(request: pytest.FixtureRequest, catalog: dict[str, Any]):
    if request.config.getoption("--update-snapshots"):
        SNAPSHOT.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip("已更新 schema 快照")

    if not SNAPSHOT.is_file():
        pytest.fail(
            f"缺少 schema 快照 {SNAPSHOT.name}；"
            "首次生成请运行：pytest tests/contract/test_schema_snapshot.py --update-snapshots"
        )

    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert catalog == expected, (
        "工具 schema 发生了变化。若是有意变更，请运行 "
        "`pytest tests/contract/test_schema_snapshot.py --update-snapshots` 并提交新快照；"
        "若是无意变更，请修正实现。\n差异："
        + describe_diff(expected, catalog)
    )


def describe_diff(expected: dict[str, Any], actual: dict[str, Any]) -> str:
    lines: list[str] = []
    for name in sorted(set(expected) | set(actual)):
        if name not in expected:
            lines.append(f"+ 新增工具 {name}")
            continue
        if name not in actual:
            lines.append(f"- 删除工具 {name}")
            continue
        old = expected[name]
        new = actual[name]
        if old == new:
            continue
        old_params = set((old.get("properties") or {}))
        new_params = set((new.get("properties") or {}))
        if old_params != new_params:
            lines.append(
                f"~ {name} 参数集合变化：新增 {sorted(new_params - old_params)}，"
                f"移除 {sorted(old_params - new_params)}"
            )
        if set(old.get("required") or []) != set(new.get("required") or []):
            lines.append(
                f"~ {name} 必填集合变化：{old.get('required')} → {new.get('required')}"
            )
        for key in sorted(old_params & new_params):
            if old["properties"][key] != new["properties"][key]:
                lines.append(
                    f"~ {name}.{key} 类型变化：{old['properties'][key]} → {new['properties'][key]}"
                )
    return "\n".join(lines) or "（无法定位差异，请直接对比快照文件）"


def test_snapshot_is_readable_json():
    if not SNAPSHOT.is_file():
        pytest.skip("快照尚未生成")
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert all(isinstance(value, dict) for value in payload.values())
