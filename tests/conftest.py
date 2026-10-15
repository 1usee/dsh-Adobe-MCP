"""pytest 公共夹具。

设计原则：测试默认**不接触用户的真实目录**——所有可写路径（日志、检查点、握手、
任务状态、CEP 扩展目录）都指向 ``tmp_path``；宿主发现用注入的假注册表；
宿主进程探测用注入的替身。这样测试既能覆盖真实链路，又不会污染开发机。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for _extra in (REPO_ROOT / "src", REPO_ROOT / "scripts", REPO_ROOT / "tests"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from dsh_adobe_mcp.bridge.service import BridgeService  # noqa: E402
from dsh_adobe_mcp.config import Config, load_config  # noqa: E402
from dsh_adobe_mcp.hosts.registry import DictReader  # noqa: E402
from dsh_adobe_mcp.logging_setup import Redactor, StructuredLogger  # noqa: E402
from dsh_adobe_mcp.tools.context import ToolContext, set_context  # noqa: E402
from fakes.fake_panel import FakePanel  # noqa: E402


class SandboxTmpFactory:
    """``tmp_path_factory`` 的替身：用**默认权限**创建目录。

    背景（实测）：本项目的开发环境在 DSH 沙箱下运行，而沙箱会拒绝对
    ``os.mkdir(path, 0o700)`` 建出来的目录做目录枚举（``PermissionError: WinError 5``）；
    pytest 内置的 ``tmp_path_factory`` 恰好用 0o700 创建目录，于是每次会话结束时的
    ``cleanup_dead_symlinks`` 都会崩掉。用默认权限建目录即可绕开，
    而 ``tmp_path`` 的语义（每个测试一个独占目录）完全保留。

    同时把基准目录放在仓库内（``.pytest-work/``），因为系统临时目录的长路径形式
    在本环境同样不被放行。
    """

    def __init__(self, base: Path) -> None:
        self._base = base
        self._counter = 0
        self._created: list[Path] = []
        # pytest 的 tmp_path 清理钩子会读这两个内部属性；保留值为 "all" 表示
        # "不按测试结果删除"（本仓库在会话开始时统一清理）。
        self._retention_policy = "all"
        self._given_basetemp: Path | None = None

    def getbasetemp(self) -> Path:
        return self._base

    def mktemp(self, basename: str, numbered: bool = True) -> Path:
        cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in basename)[:40]
        cleaned = cleaned or "test"
        for _ in range(1000):
            self._counter += 1
            name = f"{cleaned}{self._counter}" if numbered else cleaned
            candidate = self._base / name
            if not candidate.exists():
                candidate.mkdir(parents=True)  # 默认权限：不要传 mode
                self._created.append(candidate)
                return candidate
        raise RuntimeError("无法分配临时目录（同名目录过多）")

    def cleanup(self) -> None:
        for path in reversed(self._created):
            shutil.rmtree(path, ignore_errors=True)


def pytest_configure(config: pytest.Config) -> None:
    """准备仓库内的临时目录基准，并为本次会话安装 :class:`SandboxTmpFactory`。

    刻意**不**设置 ``config.option.basetemp``：那会启用 pytest 自带的
    ``cleanup_dead_symlinks``，而它正是上面提到的枚举失败点。
    若历史会话留下了不可枚举的基准目录（早期运行时用 0o700 建过），
    自动改用带进程号的新目录，避免一次环境残留挡住整轮测试。
    """
    base = _prepare_tmp_base(REPO_ROOT / ".pytest-work")
    config._dsh_tmp_factory = SandboxTmpFactory(base)  # type: ignore[attr-defined]


def _prepare_tmp_base(preferred: Path) -> Path:
    # 先顺手清掉历史会话残留的基准目录（它们可能因权限受限而删不掉，忽略即可）。
    for stale in preferred.parent.glob(f"{preferred.name}*"):
        if stale == preferred:
            continue
        try:
            shutil.rmtree(stale, ignore_errors=True)
        except OSError:
            continue

    for candidate in (preferred, preferred.with_name(f"{preferred.name}-{os.getpid()}")):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            for stale in list(candidate.iterdir()):
                try:
                    if stale.is_dir():
                        shutil.rmtree(stale, ignore_errors=True)
                    else:
                        stale.unlink(missing_ok=True)
                except OSError:
                    continue
            return candidate
        except OSError:
            continue
    raise RuntimeError(
        "无法准备测试临时目录：工作区内既有的 .pytest-work 目录不可访问"
        "（历史上由权限受限的方式创建）。请手工删除该目录后重试。"
    )


@pytest.fixture(scope="session")
def tmp_path_factory(request: pytest.FixtureRequest) -> SandboxTmpFactory:
    """覆盖 pytest 内置的 ``tmp_path_factory``（见 :class:`SandboxTmpFactory`）。"""
    return request.config._dsh_tmp_factory  # type: ignore[attr-defined]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help="更新 tests/contract/schemas.snapshot.json（仅在 schema 有意变更时使用）",
    )
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="运行需要真机宿主的集成测试（等同 DSH_ADOBE_MCP_INTEGRATION=1）",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """集成测试默认跳过（说明书 §14.3：标记为 integration，由环境变量开启）。"""
    enabled = config.getoption("--integration") or os.environ.get(
        "DSH_ADOBE_MCP_INTEGRATION", ""
    ) in ("1", "true", "yes")
    if enabled:
        return
    skip = pytest.mark.skip(
        reason="集成测试默认跳过：加 --integration 或设置 DSH_ADOBE_MCP_INTEGRATION=1"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


# --------------------------------------------------------------------------
# 配置与基础设施
# --------------------------------------------------------------------------
@pytest.fixture
def tmp_config(tmp_path: Path) -> Config:
    """把所有可写路径指向 tmp_path 的配置（并缩短超时，让测试秒级完成）。"""
    return load_config(
        env={},
        # root=tmp_path 避免读到仓库根的 config.toml；user_config 指向不存在的文件
        root=tmp_path,
        user_config=tmp_path / "no-user-config.toml",
        cli_overrides={
            "server": {"log_dir": str(tmp_path / "logs"), "log_level": "debug"},
            "bridge": {
                "handshake_dir": str(tmp_path / "handshake"),
                "handshake_poll_s": 0.02,
                "heartbeat_s": 0.5,
                "connect_timeout_s": 2,
                "auth_timeout_s": 1,
                "reconnect_backoff_max_s": 0.2,
            },
            "hosts": {
                "premiere": {
                    "eval_timeout_s": 2,
                    "launch_timeout_s": 3,
                    "launch_on_demand": False,
                }
            },
            "safety": {
                "checkpoint_dir": str(tmp_path / "checkpoints"),
                "allowed_path_roots": [str(tmp_path)],
                "max_checkpoints": 3,
            },
            "jobs": {"state_dir": str(tmp_path / "jobs"), "poll_interval_s": 0.05},
            "cep": {
                "source_dir": str(REPO_ROOT / "cep-extension"),
                "extensions_dir": str(tmp_path / "extensions"),
                "backup_dir": str(tmp_path / "cep-backup"),
            },
            "tools": {"enabled_groups": ["core", "project_edit", "timeline_edit", "effects", "media", "export"]},
        },
    )


@pytest.fixture
def logger(tmp_config: Config) -> Iterator[StructuredLogger]:
    log = StructuredLogger(
        tmp_config.server.log_dir,
        level="debug",
        console_level="none",
        redactor=Redactor(),
    )
    yield log
    log.close()


@pytest.fixture
def fake_registry(tmp_path: Path) -> DictReader:
    """一台装了 PPro 25.0 与 24.0 的"机器"（安装路径用 tmp 目录，避免依赖真实安装）。"""
    install_25 = tmp_path / "Adobe" / "Premiere Pro 2025"
    install_24 = tmp_path / "Adobe" / "Premiere Pro 2024"
    exe_25 = install_25 / "Adobe Premiere Pro.exe"
    exe_24 = install_24 / "Adobe Premiere Pro.exe"
    for exe in (exe_25, exe_24):
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"stub")
    return DictReader(
        {
            r"HKLM\SOFTWARE\Adobe\Premiere Pro\25.0": {"InstallPath": str(install_25)},
            r"HKLM\SOFTWARE\Adobe\Premiere Pro\24.0": {"InstallPath": str(install_24)},
        }
    )


@pytest.fixture
def running_pid() -> int:
    """"正在运行的宿主"的进程号：用当前进程号，psutil 一定认为它存活。"""
    return os.getpid()


@pytest.fixture
def process_probe(running_pid: int):
    """宿主进程探测替身：一律报告"正在运行"，pid 为当前进程。"""

    def _probe(adapter: Any, descriptor: Any) -> tuple[bool, int | None]:
        return True, running_pid

    return _probe


# --------------------------------------------------------------------------
# 假面板与桥接服务
# --------------------------------------------------------------------------
@pytest.fixture
async def panel(tmp_config: Config) -> Iterator[FakePanel]:
    """一个已启动并写好握手文件的假面板。"""
    fake = FakePanel(tmp_config.bridge.handshake_dir)
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()


@pytest.fixture
def bridge(
    tmp_config: Config,
    fake_registry: DictReader,
    logger: StructuredLogger,
    process_probe: Any,
) -> Iterator[BridgeService]:
    service = BridgeService(
        tmp_config,
        logger=logger,
        registry_reader=fake_registry,
        process_probe=process_probe,
        discovery_ttl_s=0.0,
    )
    yield service


@pytest.fixture
def ctx(
    tmp_config: Config,
    logger: StructuredLogger,
    bridge: BridgeService,
) -> Iterator[ToolContext]:
    """完整的 L1 工具上下文（不启动后台任务，测试自己驱动）。"""
    context = ToolContext(tmp_config, logger=logger, bridge=bridge)
    set_context(context)
    try:
        yield context
    finally:
        set_context(None)


@pytest.fixture
def project_file(tmp_path: Path) -> Path:
    """一个真实存在的 .prproj 文件。

    检查点是**文件级复制**（说明书 §8.3），因此 T2 相关测试必须有一个真实文件；
    否则检查点会正确地失败并拒绝执行破坏性操作（那条路径另有专门的测试）。
    """
    path = tmp_path / "demo.prproj"
    path.write_bytes(b"<Project/>" * 512)
    return path


@pytest.fixture
def project_panel(panel: FakePanel, project_file: Path) -> FakePanel:
    """让假面板报告一个真实存在的工程路径。"""
    info = dict(panel.payloads["project.info"])
    info.update({"path": str(project_file), "name": project_file.stem})
    panel.payloads["project.info"] = info
    state = dict(panel.payloads["host.state"])
    state.update({"project_path": str(project_file)})
    panel.payloads["host.state"] = state
    app_info = dict(panel.payloads["app.info"])
    app_info.update({"project_path": str(project_file), "project_name": project_file.stem})
    panel.payloads["app.info"] = app_info
    return panel


@pytest.fixture
def contract():
    from dsh_adobe_mcp.contract import load_contract

    return load_contract()
