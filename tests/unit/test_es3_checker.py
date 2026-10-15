"""ES3 方言静态检查器自身必须有正反样本（说明书 §6.1）。

门禁最危险的失败模式是"静默通过"：如果以为"解析失败即含 ES5+ 语法"，
而 esprima 4.x 其实**能正常解析** let/const/箭头函数/模板字符串，
门禁就会给出虚假的安全感。因此这里对每种禁止语法各准备一个应失败的样本，
对合法 ES3 准备应通过的样本。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import check_es3_syntax as checker

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 每种违禁语法一个样本（第 3 行是违禁写法，用于验证行号映射）
NEGATIVE_SAMPLES: dict[str, str] = {
    "let": "var a = 1;\n\nlet b = 2;\n",
    "const": "var a = 1;\n\nconst b = 2;\n",
    "arrow": "var f;\nvar h = function (x) { return x; };\nvar g = (x) => x;\n",
    "template": "var name = 'x';\n\nvar s = `hello ${name}`;\n",
    "class": "var a;\n\nclass Thing { }\n",
    "default-param": "var a;\n\nfunction f(x = 1) { return x; }\n",
    "object-pattern": "var a;\nvar f = function (o) { return o; };\nvar { y } = { y: 1 };\n",
    "array-pattern": "var a;\n\nvar [p, q] = [1, 2];\n",
    "spread": "var a;\nvar b = Math.max.apply(null, [1]);\nvar c = [1, ...[2]];\n",
    "for-of": "var a = [1];\n\nfor (var v of a) { v; }\n",
    "async": "var a;\n\nasync function f() { return 1; }\n",
    "generator": "var a;\n\nfunction* g() { yield 1; }\n",
    "object-shorthand": "var a = 1;\n\nvar o = { a };\n",
    "computed-key": "var k = 'a';\n\nvar o = { [k]: 1 };\n",
    "accessor": "var o = {\n  k: 1,\n  get x() { return 1; }\n};\n",
    "import": "var a;\n\nimport fs from 'fs';\n",
    "binary-literal": "var a;\n\nvar b = 0b1010;\n",
    "numeric-separator": "var a;\n\nvar b = 1_000_000;\n",
    "meta-property": "function f() {\n  var a = 1;\n  return new.target;\n}\n",
}


@pytest.mark.parametrize("name", sorted(NEGATIVE_SAMPLES))
def test_forbidden_syntax_is_detected(name: str):
    violations = checker.check_text(NEGATIVE_SAMPLES[name], f"{name}.jsx")
    assert violations, f"违禁语法 {name} 没有被检出（门禁静默失效！）"
    # 行号必须映射回原文件（预处理行被剥离时也不能错位）
    assert all(v.line == 3 for v in violations), [v.format() for v in violations]


def test_parse_success_alone_is_not_enough():
    """核心断言：这些写法在 esprima 里解析**成功**，门禁只能靠 AST 遍历发现。"""
    import esprima

    source = "let a = 1;\nvar f = () => 1;\nvar s = `x`;\n"
    esprima.parseScript(source)  # 不抛异常 —— 所以说"解析失败即违禁"是错的
    assert checker.check_text(source, "sneaky.jsx")


@pytest.mark.parametrize(
    "source",
    [
        "var a = 1;\nfunction f(x) { return x + a; }\n",
        "var o = { k: 1, 'quoted': 2 };\n",
        "#include \"json2.jsx\"\n#target premierepro\nvar a = 1;\n",
        "var s = 'let x = 1';  // 注释里出现 let 不应误报\n",
        "var s = \"const arrow => template\";\n",
        "for (var i = 0; i < 10; i++) { i += 1; }\n",
        "try { throw { code: 'X' }; } catch (e) { var c = e.code; }\n",
        "var f = function () { return function () { return 1; }; };\n",
        "var n = 0x1F, m = 1e3, p = .5;\n",
        "switch (a) { case 1: break; default: break; }\n",
        "var arr = []; arr[arr.length] = 1;\n",
        "var d = new Date(); var t = d.getTime();\n",
    ],
)
def test_valid_es3_passes(source: str):
    assert checker.check_text(source, "ok.jsx") == [], checker.check_text(source, "ok.jsx")


def test_preprocessor_stripping_preserves_line_numbers():
    source = "#include \"a.jsx\"\n#include \"b.jsx\"\nvar ok = 1;\nlet bad = 2;\n"
    violations = checker.check_text(source, "lines.jsx")
    assert len(violations) == 1
    assert violations[0].line == 4


def test_syntax_error_is_reported_not_silently_passed():
    violations = checker.check_text("var a = ;\n", "broken.jsx")
    assert violations
    assert violations[0].rule == "parse-error"
    assert violations[0].line == 1


def test_repository_jsx_tree_is_es3_clean():
    """真实仓库的 jsx/** 必须全绿（说明书 §14.5 门禁 2）。"""
    root = REPO_ROOT / "cep-extension" / "jsx"
    files = checker.iter_jsx_files(root)
    if not files:
        pytest.skip("仓库里还没有 jsx 文件")
    violations = checker.check_paths(files, base=REPO_ROOT)
    assert violations == [], "\n".join(v.format() for v in violations)


def test_command_line_entry_point_returns_zero_for_clean_tree(capsys):
    exit_code = checker.main([])
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err


def test_command_line_entry_point_detects_violation(tmp_path: Path, capsys):
    bad = tmp_path / "bad.jsx"
    bad.write_text("let x = 1;\n", encoding="utf-8")
    exit_code = checker.main([str(bad)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "var-kind:let" in captured.err


def test_list_rules_outputs_every_rule(capsys):
    assert checker.main(["--list-rules"]) == 0
    out = capsys.readouterr().out
    for rule in checker.FORBIDDEN_NODES:
        assert rule in out


def test_json_output_is_machine_readable(tmp_path: Path, capsys):
    bad = tmp_path / "bad.jsx"
    bad.write_text("const x = 1;\n", encoding="utf-8")
    assert checker.main(["--json", str(bad)]) == 1
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["rule"] == "var-kind:const"
    assert payload[0]["file"].endswith("bad.jsx")
