"""ES3 方言静态检查（说明书 §6.1、§14.5 门禁 2）。

**为什么用 AST 而不是正则**：正则无法区分字符串/注释里的 ``let``、``=>``，
会产生大量误报，最终被人关掉——那样的门禁等于没有。这里用 ``esprima`` 解析成
AST 后**遍历节点类型**判定违禁语法。

**关键实现细节**：``esprima`` 4.x 实现的是 ES2017，``let`` / ``const`` / 箭头函数 /
模板字符串**都能正常解析通过**，不会抛异常。所以门禁必须两步走：先解析，再遍历。
绝不能依赖"解析报错"来判定违禁语法——那会让门禁静默失效，比没有门禁更危险。

用法::

    python scripts/check_es3_syntax.py                     # 检查 cep-extension/jsx
    python scripts/check_es3_syntax.py path/to/file.jsx    # 检查指定文件
    python scripts/check_es3_syntax.py --list-rules
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import _bootstrap  # noqa: F401

from dsh_adobe_mcp.contract import repo_root

#: 节点类型 → 说明（说明书 §6.1 的表格逐条落地）
FORBIDDEN_NODES: dict[str, str] = {
    "ArrowFunctionExpression": "箭头函数是 ES6 语法，请改用 function 表达式",
    "TemplateLiteral": "模板字符串是 ES6 语法，请改用字符串拼接",
    "TemplateElement": "模板字符串片段，见 TemplateLiteral",
    "TaggedTemplateExpression": "标签模板是 ES6 语法",
    "ClassDeclaration": "class 是 ES6 语法",
    "ClassExpression": "class 是 ES6 语法",
    "ObjectPattern": "对象解构是 ES6 语法，请逐字段赋值",
    "ArrayPattern": "数组解构是 ES6 语法，请用下标访问",
    "AssignmentPattern": "默认参数/解构默认值是 ES6 语法",
    "SpreadElement": "展开运算符是 ES6 语法",
    "RestElement": "剩余参数是 ES6 语法",
    "ForOfStatement": "for...of 是 ES6 语法，请用 for(;;)",
    "AwaitExpression": "await 是 ES2017 语法；异步由 L1 的任务模型承担",
    "YieldExpression": "生成器是 ES6 语法",
    "MetaProperty": "new.target 等元属性是 ES6 语法",
    "Super": "super 是 ES6 语法",
    "ImportDeclaration": "import 是 ES6 模块语法；ExtendScript 用 #include",
    "ExportNamedDeclaration": "export 是 ES6 模块语法",
    "ExportDefaultDeclaration": "export 是 ES6 模块语法",
    "ExportAllDeclaration": "export 是 ES6 模块语法",
}

#: VariableDeclaration.kind 的取值
FORBIDDEN_VAR_KINDS = {"let", "const"}


@dataclass(frozen=True)
class Violation:
    file: str
    line: int
    column: int
    rule: str
    message: str

    def format(self) -> str:
        return f"{self.file}:{self.line}:{self.column}: [{self.rule}] {self.message}"


def strip_preprocessor(text: str) -> tuple[str, list[int]]:
    """剥离 ``#include`` / ``#target`` 等 ExtendScript 预处理行。

    返回 (剥离后的文本, 每个新行号对应的原行号)。

    ExtendScript 的预处理指令不是合法 JavaScript，解析前必须剥离，
    但要保持行号对应，否则报错位置会误导人。
    """
    kept: list[str] = []
    line_map: list[int] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
        line_map.append(index)
    return "\n".join(kept), line_map


def _walk(node: object, visit) -> None:  # type: ignore[no-untyped-def]
    """深度优先遍历 AST 节点。

    兼容两种节点表示：``esprima`` 的 Python 移植版返回的是 ``nodes.*`` **对象**
    （不是字典），其子节点既可能挂在属性上，也可能是列表元素。早期只处理字典的版本
    会静默地什么都检不出来——这正是门禁最危险的失败模式，因此这里两种都支持，
    并由 :func:`self_check` 用金丝雀样本兜底验证。
    """
    if isinstance(node, dict):
        visit(node)
        for value in node.values():
            _walk(value, visit)
        return
    if isinstance(node, (list, tuple)):
        for item in node:
            _walk(item, visit)
        return
    if hasattr(node, "type"):
        as_dict = node.__dict__ if hasattr(node, "__dict__") else {}
        visit(as_dict)
        for value in as_dict.values():
            _walk(value, visit)


def _to_plain(tree: object) -> object:
    """把 esprima 的节点对象树转换为普通 dict/list 树（若可用）。"""
    import esprima

    to_dict = getattr(esprima, "toDict", None)
    if callable(to_dict):
        try:
            return to_dict(tree)
        except Exception:  # pragma: no cover - 极少数节点类型
            return tree
    return tree


def _literal_rule(node: dict) -> str | None:
    """数值字面量里的 ES6+ 写法（二进制、八进制、数字分隔符）。

    只对**数值**字面量生效：字符串字面量也是 ``Literal`` 节点，如果不过滤，
    ``"\\Root\\bin_1"`` 这类字符串会被误判成数字分隔符（门禁误报会被人直接关掉）。
    """
    raw = node.get("raw")
    if not isinstance(raw, str):
        return None
    head = raw.lstrip("+-")
    if not head or not (head[0].isdigit() or head[0] == "."):
        return None
    if head.lower().startswith(("0b", "0o")):
        return "二进制/八进制字面量是 ES6 语法，请改用十进制或 parseInt(...,2)"
    if "_" in head:
        return "数字分隔符（1_000）是 ES2021 语法"
    return None


def check_text(text: str, filename: str = "<memory>") -> list[Violation]:
    """检查一段源码，返回全部违禁语法。解析失败本身也是失败（显式暴露，不静默通过）。"""
    import esprima

    stripped, line_map = strip_preprocessor(text)

    try:
        tree = esprima.parseScript(stripped, {"loc": True, "range": True, "comment": False})
    except Exception as exc:  # esprima 抛的是普通 Exception 子类
        line = getattr(exc, "lineNumber", None)
        original = line_map[line - 1] if line and 0 < line <= len(line_map) else (line or 0)
        return [
            Violation(
                file=filename,
                line=original,
                column=getattr(exc, "column", 0) or 0,
                rule="parse-error",
                message=f"无法解析（也可能是 ES5+ 语法）：{exc}",
            )
        ]

    tree = _to_plain(tree)

    violations: list[Violation] = []

    def visit(node: dict) -> None:
        node_type = node.get("type")
        location = node.get("loc") or {}
        start = location.get("start") or {}
        line = int(start.get("line") or 0)
        column = int(start.get("column") or 0)
        original_line = line_map[line - 1] if 0 < line <= len(line_map) else line

        def report(rule: str, message: str) -> None:
            violations.append(
                Violation(
                    file=filename,
                    line=original_line,
                    column=column + 1,
                    rule=rule,
                    message=message,
                )
            )

        if isinstance(node_type, str) and node_type in FORBIDDEN_NODES:
            report(node_type, FORBIDDEN_NODES[node_type])

        if node_type == "VariableDeclaration" and node.get("kind") in FORBIDDEN_VAR_KINDS:
            report(f"var-kind:{node.get('kind')}", f"ExtendScript 只支持 var，请改掉 {node.get('kind')}")

        if node_type in ("FunctionDeclaration", "FunctionExpression", "Property"):
            if node.get("async"):
                report("async-function", "async 函数是 ES2017 语法")
            if node.get("generator"):
                report("generator", "生成器函数是 ES6 语法")

        if node_type == "Property":
            if node.get("method"):
                report("object-method-shorthand", "对象方法简写是 ES6 语法")
            if node.get("shorthand"):
                report("object-shorthand", "对象属性简写是 ES6 语法")
            if node.get("computed"):
                report("computed-property", "对象计算属性名是 ES6 语法")
            if node.get("kind") in ("get", "set"):
                report("accessor", "getter/setter 是 ES5 语法，请用普通方法")

        if node_type == "Literal":
            rule = _literal_rule(node)
            if rule:
                report("literal", rule)

    _walk(tree, visit)
    violations.sort(key=lambda v: (v.line, v.column, v.rule))
    return violations


#: 金丝雀样本：检查器必须能检出它，否则说明门禁已静默失效。
CANARY_SOURCE = "var ok = 1;\nlet bad = 2;\nvar arrow = function () { return 1; };\n"
CANARY_MUST_MATCH = "var-kind:let"


def self_check() -> list[str]:
    """检查器自检：用金丝雀样本确认"确实能检出违禁语法"。

    为什么必须有：``esprima`` 的 Python 移植版返回的是节点**对象**而不是字典，
    只按字典遍历的实现会一个违规都报不出来——但它仍然是"0 违规、退出码 0"，
    看起来像全绿。这种静默失效比没有门禁更危险，所以每次运行都先验一下自己。
    """
    problems: list[str] = []
    found = {violation.rule for violation in check_text(CANARY_SOURCE, "<canary>")}
    if CANARY_MUST_MATCH not in found:
        problems.append(
            "ES3 检查器自检失败：金丝雀样本（let 声明）没有被检出。"
            "门禁已静默失效，请检查 esprima 的节点表示是否变化（见 check_es3_syntax._walk）"
        )
    # 反向验证：合法 ES3 不应被误报，否则门禁会被人关掉
    false_positives = check_text("var a = 1;\nfunction f(x) { return x; }\n", "<canary-clean>")
    if false_positives:
        problems.append(
            "ES3 检查器自检失败：合法 ES3 代码被误报为违规（"
            + "；".join(v.format() for v in false_positives)
            + "）"
        )
    return problems


def iter_jsx_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.jsx") if p.is_file())


def check_paths(paths: list[Path], base: Path | None = None) -> list[Violation]:
    violations: list[Violation] = []
    for path in paths:
        base = base or repo_root()
        try:
            display = str(path.relative_to(base))
        except ValueError:
            display = str(path)
        violations.extend(check_text(path.read_text(encoding="utf-8"), display))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ExtendScript ES3 方言静态检查")
    parser.add_argument("paths", nargs="*", help="要检查的文件或目录；缺省为 cep-extension/jsx")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--list-rules", action="store_true", help="列出全部禁止的语法")
    args = parser.parse_args(argv)

    if args.list_rules:
        for rule, message in sorted(FORBIDDEN_NODES.items()):
            print(f"{rule}: {message}")
        return 0

    # 先自检：门禁绝不能"看起来全绿其实什么都没查"
    canary_problems = self_check()
    if canary_problems:
        for problem in canary_problems:
            print(f"[FAIL] {problem}", file=sys.stderr)
        return 1

    root = repo_root()
    targets: list[Path] = []
    if args.paths:
        for raw in args.paths:
            path = Path(raw)
            path = path if path.is_absolute() else (Path.cwd() / path)
            if path.is_dir():
                targets.extend(iter_jsx_files(path))
            else:
                targets.append(path)
    else:
        targets = iter_jsx_files(root / "cep-extension" / "jsx")

    if not targets:
        print("[FAIL] 没有找到任何 .jsx 文件", file=sys.stderr)
        return 1

    violations = check_paths(targets, base=root)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "file": v.file,
                        "line": v.line,
                        "column": v.column,
                        "rule": v.rule,
                        "message": v.message,
                    }
                    for v in violations
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for violation in violations:
            print(violation.format(), file=sys.stderr)
        if not violations:
            print(f"[ OK ] ES3 方言检查通过：{len(targets)} 个文件")

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
