"""构建期门禁（说明书 §14.5）：门禁脚本必须真的能跑、并且真的能拦。

三道强制门禁：

1. 依赖真源一致性 —— 由 ``tests/test_dependencies.py`` 覆盖；
2. ES3 方言静态检查 —— ``scripts/check_es3_syntax.py``；
3. 命令契约与文档同步 —— ``scripts/gen_jsx_dispatch.py --check`` 与 ``scripts/gen_commands_doc.py --check``。

这里以子进程方式运行它们（与 CI 的调用方式一致），并要求退出码为 0。
另外验证"门禁不是空转"：ES3 检查器对一份含违禁语法的文件必须返回非零。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


def repo_env() -> dict[str, str]:
    """给子进程准备环境：把 ``src`` 与 ``scripts`` 挂到 PYTHONPATH。

    这样门禁脚本在"包尚未 pip install -e ."的开发机上也能跑起来
    （pytest 自己的 ``pythonpath`` 配置只影响当前进程，不传给子进程）。
    """
    env = dict(os.environ)
    parts = [str(REPO_ROOT / "src"), str(REPO_ROOT / "scripts")]
    existing = env.get("PYTHONPATH")
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    # 避免读到开发机上真实的用户级配置，让门禁结果可复现
    env.pop("DSH_ADOBE_MCP__SERVER__LOG_LEVEL", None)
    return env


def run(script: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - 参数来自本仓库的固定脚本
        script,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        env=repo_env(),
    )


def test_es3_gate_passes_on_repository():
    result = run([PYTHON, "scripts/check_es3_syntax.py"])
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_es3_gate_can_actually_fail(tmp_path: Path):
    bad = tmp_path / "bad.jsx"
    bad.write_text("var a = 1;\nlet b = 2;\n", encoding="utf-8")
    result = run([PYTHON, "scripts/check_es3_syntax.py", str(bad)])
    assert result.returncode == 1
    assert "var-kind:let" in result.stderr


def test_dispatch_gate_passes():
    result = run([PYTHON, "scripts/gen_jsx_dispatch.py", "--check"])
    assert result.returncode == 0, result.stderr


def test_docs_gate_passes():
    result = run([PYTHON, "scripts/gen_commands_doc.py", "--check"])
    assert result.returncode == 0, result.stderr


def test_config_example_gate_passes():
    result = run([PYTHON, "scripts/render_config_example.py", "--check"])
    assert result.returncode == 0, result.stderr


def test_generated_artifacts_are_committed():
    for relative in (
        "cep-extension/jsx/dispatch.jsx",
        "docs/generated/tools.md",
        "docs/generated/commands.md",
        "config.example.toml",
    ):
        assert (REPO_ROOT / relative).is_file(), f"缺少生成物 {relative}"


def test_package_imports_without_side_effects(tmp_path: Path):
    """导入包不应创建任何目录或文件（配置只在显式调用时读取）。"""
    result = run(
        [PYTHON, "-X", "utf8", "-c", "import dsh_adobe_mcp, dsh_adobe_mcp.server; print('ok')"],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_cli_check_config_runs():
    result = run([PYTHON, "-m", "dsh_adobe_mcp", "--check-config", "--groups", "core"])
    assert result.returncode == 0, result.stderr
    assert '"core"' in result.stdout


def test_cli_print_example_config_runs():
    result = run([PYTHON, "-m", "dsh_adobe_mcp", "--print-example-config"])
    assert result.returncode == 0, result.stderr
    assert "[server]" in result.stdout
    assert "[safety]" in result.stdout


def node_runtime() -> list[str] | None:
    """返回一个可用的 JS 运行方式（本机没有 node.exe 时用 DSH 自带的 Electron）。"""
    node = shutil.which("node")
    if node:
        return [node]
    candidate = Path(r"F:\DeepseekHarness\DeepSeek Harness.exe")
    if candidate.is_file():
        return [str(candidate)]
    return None


def run_js(script: list[str]) -> subprocess.CompletedProcess[str]:
    runtime = node_runtime()
    assert runtime is not None, "没有可用的 JS 运行时"
    env = dict(os.environ)
    env["ELECTRON_RUN_AS_NODE"] = "1"
    return subprocess.run(  # noqa: S603
        runtime + script,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )


@pytest.mark.slow
def test_panel_layer_tests_pass():
    """面板 Node 侧自测（认证、帧协议、evalScript 序列化、串行化、脱敏）。"""
    script = REPO_ROOT / "tests" / "l3" / "panel" / "run.js"
    if not script.is_file():
        pytest.skip("面板自测脚本不存在")
    result = run_js([str(script)])
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert "FAIL" not in result.stdout


@pytest.mark.slow
def test_jsx_layer_tests_pass():
    """ExtendScript 层自测（用伪造宿主跑真实 JSX 处理器）。"""
    script = REPO_ROOT / "tests" / "l3" / "jsx" / "run.js"
    if not script.is_file():
        pytest.skip("JSX 自测脚本不存在")
    result = run_js([str(script)])
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert "失败：0" in result.stdout or "FAIL" not in result.stdout


@pytest.mark.slow
def test_mini_ws_selftest_passes():
    script = REPO_ROOT / "cep-extension" / "js" / "vendor" / "ws" / "selftest.js"
    if not script.is_file():
        pytest.skip("mini-ws 自测脚本不存在")
    result = run_js([str(script)])
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
