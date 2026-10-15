"""校验一次 MCP 握手的响应文件（配合 ``scripts/verify_mcp_stdio.ps1`` 使用）。

用法::

    python scripts/check_mcp_handshake.py <response.jsonl> [--expect-tools 45] [--groups core]

响应文件里是服务器逐行输出的 JSON-RPC 响应（含 ``initialize`` 与 ``tools/list`` 的结果）。
退出码 0 表示通过；任何缺失或不合规都会打印出来并返回 1。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from dsh_adobe_mcp.safety.policy import DEFAULT_ENABLED_GROUPS, POLICY, enabled_tools

REQUIRED_TOOLS = (
    "host_list",
    "host_status",
    "host_launch",
    "host_wait_ready",
    "host_resync",
    "ppro_app_info",
    "ppro_project_info",
    "job_list",
    "job_status",
    "job_cancel",
    "safety_confirm_operation",
    "safety_list_checkpoints",
)


def load_responses(path: Path) -> dict[object, dict]:
    responses: dict[object, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if "id" in payload:
            responses[payload["id"]] = payload
    return responses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 MCP 握手响应")
    parser.add_argument("response_file", type=Path)
    parser.add_argument("--expect-tools", type=int, default=None)
    parser.add_argument("--groups", default=None, help="启用的工具组（逗号分隔），用于校验工具集合")
    args = parser.parse_args(argv)

    problems: list[str] = []
    responses = load_responses(args.response_file)
    if not responses:
        print("[FAIL] 响应文件里没有任何 JSON-RPC 响应")
        return 1

    init = (responses.get(1) or {}).get("result")
    if not init:
        problems.append("缺少 initialize 响应（id=1）")
    else:
        info = init.get("serverInfo") or {}
        print(
            f"initialize  -> name={info.get('name')} version={info.get('version')} "
            f"protocol={init.get('protocolVersion')}"
        )

    tools_result = (responses.get(2) or {}).get("result")
    if not tools_result:
        problems.append("缺少 tools/list 响应（id=2）")
    else:
        tools = tools_result.get("tools") or []
        names = sorted(tool["name"] for tool in tools)
        print(f"tools/list  -> {len(names)} 个工具")

        if args.groups:
            expected = set(enabled_tools([g.strip() for g in args.groups.split(",") if g.strip()]))
        else:
            expected = set(enabled_tools(list(DEFAULT_ENABLED_GROUPS)))
        if set(names) != expected:
            problems.append(
                f"工具集合与预期不符：多 {sorted(set(names) - expected)}，"
                f"少 {sorted(expected - set(names))}"
            )
        if args.expect_tools is not None and len(names) != args.expect_tools:
            problems.append(f"工具数量应为 {args.expect_tools}，实际 {len(names)}")

        for required in REQUIRED_TOOLS:
            if required not in names:
                problems.append(f"缺少工具 {required}")

        without_schema = [tool["name"] for tool in tools if not tool.get("inputSchema")]
        if without_schema:
            problems.append(f"以下工具没有 inputSchema：{without_schema}")

        unknown = [name for name in names if name not in POLICY]
        if unknown:
            problems.append(f"出现了风险表中没有的工具：{unknown}")

        print("工具示例    -> " + ", ".join(names[:6]))

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        return 1
    print("[ OK ] 握手与工具发现均符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
