"""MCP 接入验收（说明书 §15 里程碑 M0："DSH 能接入并列出工具"）。

用**真实的 MCP 客户端**对**真实的 FastMCP 服务器对象**做 ``initialize``、
``tools/list``、``tools/call``，走完整协议层（含 lifespan 启停、schema 校验、结果序列化），
而不是直接调用 Python 函数。这样验的是"客户端看到什么"，与 DSH 接入时的形态一致。

关于 stdio 子进程：本项目的开发环境禁止对子进程使用管道 stdio（WinError 5），
因此这里用进程内传输；``python -m dsh_adobe_mcp`` 的入口本身由
``tests/contract/test_gates.py`` 以子进程方式验证（那条路径不需要管道捕获）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dsh_adobe_mcp.config import load_config
from dsh_adobe_mcp.safety.policy import DEFAULT_ENABLED_GROUPS, enabled_tools
from dsh_adobe_mcp.server import build_bundle

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_bundle(tmp_path: Path, groups: list[str] | None = None):
    config = load_config(
        env={},
        root=tmp_path,
        user_config=tmp_path / "none.toml",
        cli_overrides={
            "server": {"log_dir": str(tmp_path / "logs")},
            "safety": {"checkpoint_dir": str(tmp_path / "checkpoints"), "allowed_path_roots": [str(tmp_path)]},
            "jobs": {"state_dir": str(tmp_path / "jobs"), "poll_interval_s": 100},
            "bridge": {"handshake_dir": str(tmp_path / "handshake"), "handshake_poll_s": 0.05},
            "hosts": {"premiere": {"launch_on_demand": False, "launch_timeout_s": 2}},
            "cep": {
                "extensions_dir": str(tmp_path / "extensions"),
                "backup_dir": str(tmp_path / "cep-backup"),
            },
            "tools": {"enabled_groups": groups or list(DEFAULT_ENABLED_GROUPS)},
        },
    )
    return build_bundle(config)


def payload_of(result: Any) -> dict[str, Any]:
    """把 `call_tool` 的结果还原成统一信封（兼容 content / data 两种形态）。"""
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        return json.loads(data)
    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"无法从结果中取出信封：{result!r}")


async def test_client_lists_default_tools(tmp_path: Path):
    from fastmcp import Client

    bundle = make_bundle(tmp_path)
    async with Client(bundle.mcp) as client:
        tools = await client.list_tools()

    names = {tool.name for tool in tools}
    assert names == set(enabled_tools(list(DEFAULT_ENABLED_GROUPS)))
    assert len(names) == 45
    assert len(bundle.registered_tools) == 45

    sample = next(tool for tool in tools if tool.name == "ppro_timeline_insert_clip")
    assert sample.description, "工具必须有描述（客户端靠它判断用途）"
    properties = sample.inputSchema.get("properties") or {}
    assert "item" in properties
    assert "dry_run" in properties
    assert "host" in properties
    assert "item" in (sample.inputSchema.get("required") or [])


async def test_tool_call_roundtrip_without_host(tmp_path: Path):
    from fastmcp import Client

    bundle = make_bundle(tmp_path)
    async with Client(bundle.mcp) as client:
        result = await client.call_tool("host_list", {})
    payload = payload_of(result)
    assert payload["ok"] is True
    assert "hosts" in payload["data"]
    assert payload["error"] is None


async def test_missing_host_returns_actionable_envelope(tmp_path: Path):
    from fastmcp import Client

    bundle = make_bundle(tmp_path)
    async with Client(bundle.mcp) as client:
        payload = payload_of(await client.call_tool("host_status", {"host": "premiere"}))
    assert payload["ok"] is True
    status = payload["data"]
    if not status["host"]["installs"]:
        assert any("注册表" in text for text in payload["warnings"])
    assert "deployment" in status


async def test_disabled_groups_are_not_exposed(tmp_path: Path):
    from fastmcp import Client

    bundle = make_bundle(tmp_path, groups=["core"])
    async with Client(bundle.mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert names == set(enabled_tools(["core"]))
    assert "ppro_project_import" not in names
    assert "ppro_export_encode_sequence" not in names


async def test_t2_over_mcp_returns_confirmation_flow(tmp_path: Path):
    from fastmcp import Client

    bundle = make_bundle(tmp_path)
    async with Client(bundle.mcp) as client:
        payload = payload_of(
            await client.call_tool("ppro_timeline_remove_clip", {"ref": "V:1:0"})
        )
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CONFIRM_REQUIRED"
    assert payload["data"]["confirm_token"]
    assert payload["data"]["impact"]["risk"] == "T2"
    assert "safety_confirm_operation" in payload["data"]["how_to_confirm"]
    assert payload["error"]["remedy"]


async def test_unknown_tool_is_rejected_by_protocol(tmp_path: Path):
    from fastmcp import Client

    bundle = make_bundle(tmp_path)
    async with Client(bundle.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("no_such_tool", {})


async def test_schema_validation_rejects_bad_arguments(tmp_path: Path):
    """协议层的参数校验必须真的生效：这是"参数注入无法执行任意脚本"的第一道防线。"""
    from fastmcp import Client

    bundle = make_bundle(tmp_path)
    async with Client(bundle.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("ppro_timeline_insert_clip", {"at_s": 1.0})  # 缺 item


async def test_server_lifespan_starts_and_stops(tmp_path: Path):
    """lifespan 必须在会话期间建立工具上下文，并在退出时清理（含任务管理器）。"""
    from fastmcp import Client

    from dsh_adobe_mcp.tools.context import context

    bundle = make_bundle(tmp_path)
    async with Client(bundle.mcp) as client:
        await client.list_tools()
        ctx = context()
        assert ctx is bundle.context
    # 退出后任务轮询已停止、日志句柄已关闭
    assert bundle.context.jobs._poll_task is None


async def test_tool_result_is_json_serialisable(tmp_path: Path):
    from fastmcp import Client

    bundle = make_bundle(tmp_path)
    async with Client(bundle.mcp) as client:
        payload = payload_of(await client.call_tool("safety_list_checkpoints", {}))
    assert payload["ok"] is True
    json.dumps(payload, ensure_ascii=False)  # 不得包含不可序列化对象
    assert "checkpoints" in payload["data"]
    assert "writable" in payload["data"]["stats"]
