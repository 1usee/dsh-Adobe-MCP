"""由 ``commands.json`` + 风险表 + 实际工具 schema 生成工具文档（说明书 §14.5 门禁 3）。

生成两份文件：

* ``docs/generated/tools.md``    —— 面向 MCP 客户端使用者：工具、风险等级、参数（取自真实 schema）
* ``docs/generated/commands.md`` —— 面向 L3 实现者：命令契约（处理器、dry-run、QE 能力等）

``--check`` 模式下不写文件，只校验磁盘上的文档是否与当前契约一致；
契约改了却忘了重新生成，CI 会直接失败。

用法::

    python scripts/gen_commands_doc.py
    python scripts/gen_commands_doc.py --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from dsh_adobe_mcp.contract import Contract, Group, load_contract, repo_root
from dsh_adobe_mcp.safety.policy import POLICY, policy_for
from dsh_adobe_mcp.tools.registry import schema_catalog_sync

OUTPUT_FILES = {
    "tools": Path("docs") / "generated" / "tools.md",
    "commands": Path("docs") / "generated" / "commands.md",
}

GROUP_TITLES: dict[str, str] = {
    "core": "核心组（始终启用）",
    "project_edit": "工程编辑组",
    "timeline_edit": "时间线编辑组",
    "effects": "效果组",
    "media": "媒体组",
    "export": "导出组",
}

RISK_TITLES = {
    "T0": "T0 只读",
    "T1": "T1 增量/可逆",
    "T2": "T2 破坏性（需确认令牌）",
}


def _header(title: str, note: str) -> list[str]:
    return [
        f"# {title}",
        "",
        "> 本文件由 `python scripts/gen_commands_doc.py` 自动生成，请勿手工编辑。",
        f"> {note}",
        "> 一致性由 `tests/contract/test_tool_docs.py` 强制校验。",
        "",
    ]


def render_tools_doc(contract: Contract, schemas: dict[str, dict[str, Any]]) -> str:
    lines = _header(
        "MCP 工具目录",
        f"数据来源：`cep-extension/jsx/commands.json`（schema_version {contract.schema_version}）"
        "与 `src/dsh_adobe_mcp/safety/policy.py`；参数取自工具的真实 JSON schema。",
    )

    groups: dict[str, list[str]] = {group: [] for group in GROUP_TITLES}
    for name, policy in POLICY.items():
        groups.setdefault(policy.group, []).append(name)

    lines += ["## 总览", "", "| 工具组 | 工具数 | T0 | T1 | T2 | 默认启用 |", "|---|---|---|---|---|---|"]
    for group, title in GROUP_TITLES.items():
        names = sorted(groups.get(group, []))
        risks = [policy_for(name).risk for name in names]
        default = "是" if group in ("core", "project_edit", "timeline_edit") else "否"
        lines.append(
            f"| {title} (`{group}`) | {len(names)} | {risks.count('T0')} | "
            f"{risks.count('T1')} | {risks.count('T2')} | {default} |"
        )
    lines.append(f"| **合计** | **{len(POLICY)}** | "
                 f"**{sum(1 for p in POLICY.values() if p.risk == 'T0')}** | "
                 f"**{sum(1 for p in POLICY.values() if p.risk == 'T1')}** | "
                 f"**{sum(1 for p in POLICY.values() if p.risk == 'T2')}** | |")
    lines.append("")

    lines += [
        "## 风险等级的含义",
        "",
        "| 等级 | 定义 | 处置 |",
        "|---|---|---|",
        "| T0 只读 | 不改变宿主任何状态 | 直接执行，写审计日志 |",
        "| T1 增量/可逆 | 只增加内容，或改动可被撤销栈覆盖 | 直接执行，返回 `undo_hint` |",
        "| T2 破坏性 | 删除内容、覆盖文件、改变工程路径、批量导出 | **不执行**，返回确认令牌与影响说明 |",
        "",
        "## 工具清单",
        "",
    ]

    for group, title in GROUP_TITLES.items():
        names = sorted(groups.get(group, []))
        if not names:
            continue
        lines += [f"### {title}", "", "| 工具 | 风险 | 桥接命令 | 说明 |", "|---|---|---|---|"]
        for name in names:
            policy = policy_for(name)
            command = f"`{policy.command}`" if policy.command else "—（L1 本地）"
            lines.append(f"| `{name}` | {policy.risk} | {command} | {policy.summary} |")
        lines.append("")

    lines += ["## 工具详情", ""]
    for group, title in GROUP_TITLES.items():
        for name in sorted(groups.get(group, [])):
            policy = policy_for(name)
            command = contract.by_name.get(policy.command) if policy.command else None
            lines.append(f"### `{name}`")
            lines.append("")
            lines.append(f"- 工具组：`{group}`（{title}）")
            lines.append(f"- 风险等级：**{RISK_TITLES[policy.risk]}**")
            if command is not None:
                lines.append(f"- 桥接命令：`{command.name}`（{command.executor} 侧）")
            else:
                lines.append("- 实现位置：L1（不经过宿主脚本）")
            lines.append(f"- 说明：{policy.summary}")
            if policy.undo_hint:
                lines.append(f"- 撤销指引：{policy.undo_hint}")
            if command is not None and command.capability != "public":
                lines.append(
                    f"- 宿主接口：`{command.capability}`"
                    + ("（**未公开接口，可能导致宿主崩溃**）" if command.undocumented else "")
                )
            if command is not None and command.job:
                lines.append("- 长任务：立即返回 `job_id`，用 `job_status` 跟踪")
            if command is not None and command.returns:
                lines.append(f"- 返回：{command.returns}")

            schema = schemas.get(name) or {}
            properties = schema.get("properties") or {}
            required = set(schema.get("required") or [])
            lines.append("")
            if properties:
                lines += ["| 参数 | 类型 | 必填 | 默认 |", "|---|---|---|---|"]
                for param, spec in properties.items():
                    type_text = _type_text(spec)
                    default = _default_text(spec)
                    lines.append(
                        f"| `{param}` | {type_text} | {'是' if param in required else '否'} | {default} |"
                    )
            else:
                lines.append("_无参数。_")
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _type_text(spec: dict[str, Any]) -> str:
    if "anyOf" in spec:
        parts = [_type_text(item) for item in spec["anyOf"]]
        return " \\| ".join(parts)
    if "enum" in spec:
        return "enum(" + ", ".join(f"`{item}`" for item in spec["enum"]) + ")"
    type_name = spec.get("type")
    if type_name == "array":
        items = spec.get("items") or {}
        return f"array<{_type_text(items)}>"
    return str(type_name or "any")


def _default_text(spec: dict[str, Any]) -> str:
    if "default" not in spec:
        return "—"
    value = spec["default"]
    if value is None:
        return "`null`"
    if isinstance(value, bool):
        return "`true`" if value else "`false`"
    if isinstance(value, str):
        return f"`\"{value}\"`"
    return f"`{value}`"


def render_commands_doc(contract: Contract) -> str:
    lines = _header(
        "L3 命令契约（cep-extension/jsx/commands.json）",
        "这是 L1 与面板之间的唯一真源；`dispatch.jsx`（L3 路由表）由它生成，"
        "处理器实现位于 `cep-extension/jsx/ops/*.jsx`。",
    )
    lines += ["## 约定", ""]
    for key, value in contract.conventions.items():
        if isinstance(value, list):
            lines.append(f"- **{key}**：")
            for item in value:
                lines.append(f"  - `{item}`")
        else:
            lines.append(f"- **{key}**：{value}")
    lines += ["", f"契约 schema_version：`{contract.schema_version}`；命令总数：**{len(contract.commands)}**", ""]

    lines += ["## 命令汇总", "", "| 命令 | 工具 | 组 | 执行器 | 风险 | 能力 | 幂等 | dry-run | 长任务 |", "|---|---|---|---|---|---|---|---|---|"]
    for command in contract.commands:
        lines.append(
            f"| `{command.name}` | {f'`{command.tool}`' if command.tool else '—（内部）'} | "
            f"{command.group} | {command.executor} | {command.risk} | {command.capability} | "
            f"{'是' if command.idempotent else '否'} | {'是' if command.dry_run else '否'} | "
            f"{'是' if command.job else '否'} |"
        )
    lines.append("")

    lines += ["## 命令详情", ""]
    for command in contract.commands:
        lines.append(f"### `{command.name}`")
        lines.append("")
        lines.append(f"- 工具：{f'`{command.tool}`' if command.tool else '—（内部命令，不直接暴露）'}")
        lines.append(f"- 工具组：`{command.group}`；风险等级：{command.risk}")
        lines.append(f"- 执行器：`{command.executor}`")
        if command.ops_file:
            lines.append(f"- 实现：`{command.ops_file}` :: `{command.handler}`")
        else:
            lines.append(f"- 实现：面板 Node 侧 `{command.handler}`")
        lines.append(
            f"- 能力：`{command.capability}`"
            + ("；**未公开接口（QE），可能导致宿主崩溃**" if command.undocumented else "")
        )
        lines.append(
            f"- 幂等：{'是' if command.idempotent else '否'}；"
            f"支持 dry-run：{'是' if command.dry_run else '否'}；"
            f"长任务：{'是' if command.job else '否'}"
        )
        lines.append(f"- 说明：{command.summary}")
        lines.append(f"- 返回：{command.returns}")
        lines.append("")
        if command.params:
            lines += ["| 参数 | 类型 | 必填 | 默认 | 说明 |", "|---|---|---|---|---|"]
            for param in command.params:
                type_text = param.type
                if param.type == "enum" and param.enum:
                    type_text = "enum(" + ", ".join(f"`{item}`" for item in param.enum) + ")"
                default = "—" if param.required else f"`{param.default}`"
                lines.append(
                    f"| `{param.name}` | {type_text} | {'是' if param.required else '否'} | "
                    f"{default} | {param.description} |"
                )
        else:
            lines.append("_无参数。_")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def generate(check_only: bool = False) -> int:
    root = repo_root()
    contract = load_contract(root)
    schemas = schema_catalog_sync()
    rendered = {
        "tools": render_tools_doc(contract, schemas),
        "commands": render_commands_doc(contract),
    }

    failures: list[str] = []
    for key, relative in OUTPUT_FILES.items():
        target = root / relative
        expected = rendered[key]
        if check_only:
            if not target.is_file():
                failures.append(f"缺少生成物：{relative}")
                continue
            if target.read_text(encoding="utf-8") != expected:
                failures.append(
                    f"{relative} 与契约不同步，请运行：python scripts/gen_commands_doc.py"
                )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(expected, encoding="utf-8")
        print(f"[ OK ] 已生成 {relative}（{len(expected.splitlines())} 行）")

    if check_only:
        if failures:
            for message in failures:
                print(f"[FAIL] {message}", file=sys.stderr)
            return 1
        print(f"[ OK ] 文档与契约一致（{len(contract.commands)} 条命令，{len(POLICY)} 个工具）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成或校验工具文档")
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args(argv)
    return generate(check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
