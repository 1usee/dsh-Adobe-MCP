"""三方一致性：工具 ↔ commands.json ↔ dispatch.jsx（说明书 §14.2，最高价值的一组测试）。

这三份东西分别属于 L1（Python 工具与风险表）、契约（commands.json）、L3（面板路由与
ops 实现）。任何一侧单独演化都会造成"看起来都在跑、一调用就失败"，所以这里逐项对齐：

1. 风险表与契约的命令名/风险/工具组一致；
2. ``tools/*.py`` 注册的工具集合与契约中的工具集合**双向相等**；
3. ``dispatch.jsx`` 的路由表与契约完全一致；
4. 契约声明的每个处理器在对应的 ops 文件里真实存在且只定义一次；
5. 工具输入 schema 的参数名与契约签名一致（并覆盖 L1 的通用参数规则）；
6. ``dry_run`` 参数的存在性与契约一致。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dsh_adobe_mcp.contract import L1_GENERIC_PARAMS, load_contract, repo_root
from dsh_adobe_mcp.safety.policy import POLICY, check_against_contract
from dsh_adobe_mcp.tools.registry import collect_tools, schema_catalog_sync

REPO_ROOT = repo_root()
ROUTE_LINE = re.compile(
    r'^\s*"(?P<name>[^"]+)":\s*\{\s*file:\s*"(?P<file>[^"]*)",\s*handler:\s*"(?P<handler>[^"]*)",'
    r'\s*risk:\s*"(?P<risk>[^"]*)",\s*dryRun:\s*(?P<dry_run>true|false),'
    r'\s*idempotent:\s*(?P<idempotent>true|false),\s*capability:\s*"(?P<capability>[^"]*)",'
    r'\s*handlerReady:\s*(?P<ready>true|false)',
    re.MULTILINE,
)


@pytest.fixture(scope="module")
def contract():
    return load_contract()


@pytest.fixture(scope="module")
def routes() -> dict[str, dict[str, str]]:
    text = (REPO_ROOT / "cep-extension" / "jsx" / "dispatch.jsx").read_text(encoding="utf-8")
    found: dict[str, dict[str, str]] = {}
    for match in ROUTE_LINE.finditer(text):
        found[match.group("name")] = match.groupdict()
    assert found, "未能从 dispatch.jsx 解析出任何路由（生成器或格式变了？）"
    return found


def test_policy_matches_contract(contract):
    assert check_against_contract(contract) == []


def test_tool_functions_match_contract_tools(contract):
    implemented = set(collect_tools())
    declared = set(POLICY)
    assert implemented == declared, {
        "缺失": sorted(declared - implemented),
        "多余": sorted(implemented - declared),
    }
    assert len(implemented) == 56


def test_every_bridged_tool_has_a_command(contract):
    for name, policy in POLICY.items():
        if policy.bridged:
            assert policy.command in contract.by_name, name
    for command in contract.commands:
        if command.tool:
            assert POLICY[command.tool].command == command.name


def test_dispatch_routes_equal_contract(contract, routes):
    assert set(routes) == set(contract.by_name), {
        "只在契约里": sorted(set(contract.by_name) - set(routes)),
        "只在路由表里": sorted(set(routes) - set(contract.by_name)),
    }
    for command in contract.commands:
        route = routes[command.name]
        assert route["file"] == (command.ops_file or ""), command.name
        assert route["handler"] == command.handler, command.name
        assert route["risk"] == command.risk, command.name
        assert (route["dry_run"] == "true") is command.dry_run, command.name
        assert (route["idempotent"] == "true") is command.idempotent, command.name
        assert route["capability"] == command.capability, command.name
        assert (route["ready"] == "true") is (command.executor == "jsx"), command.name


def test_dispatch_is_generated_and_in_sync():
    """生成物的头部必须声明它是生成物，避免有人手工编辑。"""
    text = (REPO_ROOT / "cep-extension" / "jsx" / "dispatch.jsx").read_text(encoding="utf-8")
    assert "自动生成" in text
    assert "gen_jsx_dispatch.py" in text
    assert "请勿手工编辑" in text


def test_dispatch_includes_every_ops_file(contract):
    text = (REPO_ROOT / "cep-extension" / "jsx" / "dispatch.jsx").read_text(encoding="utf-8")
    includes = set(re.findall(r'#include\s+"([^"]+)"', text))
    for ops_file in contract.ops_files():
        assert f"ops/{ops_file}" in includes, ops_file
    # QE 辅助库随面板载入，但调用点有双重门禁（说明书 §6.2 的实现选择）
    assert "ops/qe.jsx" in includes
    for library in ("json2.jsx", "es3_compat.jsx", "wrap.jsx", "util.jsx"):
        assert library in includes


def test_ops_handlers_exist_exactly_once(contract):
    ops_dir = REPO_ROOT / "cep-extension" / "jsx" / "ops"
    problems: list[str] = []
    for command in contract.commands:
        if not command.ops_file:
            continue
        source = (ops_dir / command.ops_file).read_text(encoding="utf-8")
        occurrences = len(re.findall(rf"function\s+{re.escape(command.handler)}\s*\(", source))
        if occurrences != 1:
            problems.append(
                f"{command.name}: {command.ops_file} 中 function {command.handler}( 出现 {occurrences} 次"
            )
    assert problems == [], "\n".join(problems)


def test_handlers_are_not_shared_between_commands(contract):
    jsx_handlers = [c.handler for c in contract.commands if c.executor == "jsx"]
    assert len(jsx_handlers) == len(set(jsx_handlers)), "同一个 jsx 处理器被多条命令复用（签名会冲突）"


def test_every_jsx_ops_file_declares_the_dsh_namespace(contract):
    ops_dir = REPO_ROOT / "cep-extension" / "jsx" / "ops"
    for ops_file in contract.ops_files():
        source = (ops_dir / ops_file).read_text(encoding="utf-8")
        assert "DSH" in source, ops_file
        assert "#include" not in source, f"{ops_file} 不应包含 #include（由 dispatch.jsx 统一展开）"


def test_tool_schema_parameters_match_contract(contract):
    schemas = schema_catalog_sync()
    problems: list[str] = []
    for command in contract.commands:
        if not command.tool:
            continue
        properties = set((schemas.get(command.tool) or {}).get("properties") or {})
        declared = {param.name for param in command.params}
        expected = declared | set(L1_GENERIC_PARAMS)
        missing = declared - properties
        unexpected = properties - expected
        if missing:
            problems.append(f"{command.tool}: 工具缺少参数 {sorted(missing)}")
        if unexpected:
            problems.append(f"{command.tool}: 工具多出参数 {sorted(unexpected)}")
    assert problems == [], "\n".join(problems)


def test_dry_run_parameter_presence_matches_contract(contract):
    schemas = schema_catalog_sync()
    for command in contract.commands:
        if not command.tool:
            continue
        properties = set((schemas.get(command.tool) or {}).get("properties") or {})
        if command.risk == "T0":
            assert "confirm_token" not in properties, f"{command.tool} 是只读的，不应有确认令牌参数"
        if command.dry_run:
            assert "dry_run" in properties, f"{command.tool} 支持 dry-run，必须有该参数"
        else:
            assert "dry_run" not in properties, f"{command.tool} 不支持 dry-run，不应暴露该参数"


def test_t2_tools_accept_confirm_token():
    schemas = schema_catalog_sync()
    for name, policy in POLICY.items():
        if policy.risk != "T2" or not policy.bridged:
            continue
        properties = set((schemas.get(name) or {}).get("properties") or {})
        assert "confirm_token" in properties, f"T2 工具 {name} 必须能接收确认令牌"


def test_required_parameters_are_required_in_schema(contract):
    schemas = schema_catalog_sync()
    for command in contract.commands:
        if not command.tool:
            continue
        required = set((schemas.get(command.tool) or {}).get("required") or [])
        declared_required = {param.name for param in command.params if param.required}
        assert declared_required <= required, (
            f"{command.tool}: 契约声明的必需参数未在 schema 中标记 required —— "
            f"缺 {sorted(declared_required - required)}"
        )


def test_contract_has_conventions_and_ticks():
    contract = load_contract()
    assert contract.conventions["ticks_per_second"] == 254016000000
    assert "dry_run_contract" in contract.conventions
    assert "clip_ref_format" in contract.conventions


def test_node_executor_commands_have_no_ops_file(contract):
    for command in contract.commands:
        if command.executor == "node":
            assert command.ops_file is None
            assert command.tool is None or POLICY[command.tool].group == "core"


def test_internal_commands_are_not_exposed(contract):
    exposed = {command.tool for command in contract.commands if command.tool}
    for command in contract.internal:
        assert command.tool is None
    assert "host.ping" in [command.name for command in contract.internal]
    assert exposed == {name for name, policy in POLICY.items() if policy.bridged}
