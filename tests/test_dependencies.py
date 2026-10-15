"""依赖真源三方同步（说明书 §13.1、§14.5 门禁 1）。

`pyproject.toml` 的 `[project.dependencies]` 是唯一真源，`requirements.txt` 与
`environment.yml` 面向 pip / conda 两条安装路径。三者不同步即版本漂移，
是**必须修复的问题**，不是警告。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 说明书 §13.1 的运行时依赖（包名 → 版本约束）
SPEC_RUNTIME = {
    "fastmcp": ">=2.11,<3",
    "pydantic": ">=2.7",
    "websockets": ">=13",
    "psutil": ">=5.9",
    "esprima": ">=4.0",
}

#: 说明书 §13.1 的开发/测试依赖
SPEC_DEV = {
    "pytest": ">=7.4",
    "pytest-asyncio": ">=0.23",
}

#: 说明书 §13.1 明确"不声明"的依赖
FORBIDDEN = {"mcp", "tomli", "PyYAML", "pywin32", "watchdog", "watchfiles", "numpy", "pandas"}


def parse_requirement(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    text = text.split("#", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9_.\-]+)\s*(.*)$", text)
    if not match:
        return None
    return match.group(1).lower().replace("_", "-"), match.group(2).strip()


def pyproject_data() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def requirements_file(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        item = parse_requirement(line)
        if item:
            parsed[item[0]] = item[1]
    return parsed


def environment_yml_pip_section() -> dict[str, str]:
    """只解析 ``pip:`` 段（conda 段保持最小）。

    conda 环境文件里该段写成 ``  - pip:``，因此匹配要允许前导的列表符号。
    """
    text = (REPO_ROOT / "environment.yml").read_text(encoding="utf-8")
    parsed: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.match(r"^-?\s*pip:\s*$", stripped):
            inside = True
            continue
        if not inside:
            continue
        if stripped.startswith("- "):
            item = parse_requirement(stripped[2:])
            if item:
                parsed[item[0]] = item[1]
    return parsed


def test_pyproject_runtime_dependencies_match_spec():
    data = pyproject_data()
    declared = dict(parse_requirement(item) for item in data["project"]["dependencies"])  # type: ignore[misc]
    assert declared == SPEC_RUNTIME


def test_pyproject_dev_dependencies_match_spec():
    data = pyproject_data()
    declared = dict(parse_requirement(item) for item in data["project"]["optional-dependencies"]["dev"])  # type: ignore[misc]
    assert declared == SPEC_DEV


def test_python_requires():
    assert pyproject_data()["project"]["requires-python"] == ">=3.11"


def test_requirements_txt_is_in_sync():
    parsed = requirements_file(REPO_ROOT / "requirements.txt")
    assert {name: spec for name, spec in parsed.items() if name in SPEC_RUNTIME} == SPEC_RUNTIME
    assert {name: spec for name, spec in parsed.items() if name in SPEC_DEV} == SPEC_DEV


def test_environment_yml_pip_section_is_in_sync():
    parsed = environment_yml_pip_section()
    assert parsed == {**SPEC_RUNTIME, **SPEC_DEV}


def test_environment_yml_declares_python_and_pip():
    text = (REPO_ROOT / "environment.yml").read_text(encoding="utf-8")
    assert "python>=3.11" in text
    assert re.search(r"^\s*-\s*pip\s*$", text, flags=re.MULTILINE)


def test_forbidden_dependencies_are_not_declared():
    declared = set(requirements_file(REPO_ROOT / "requirements.txt"))
    declared |= set(environment_yml_pip_section())
    declared |= {parse_requirement(item)[0] for item in pyproject_data()["project"]["dependencies"]}  # type: ignore[index]
    overlap = {name.lower() for name in declared} & {name.lower() for name in FORBIDDEN}
    assert not overlap, f"以下依赖不应被声明（说明书 §13.1）：{sorted(overlap)}"
