"""桥接服务：宿主发现 → 面板部署 → 握手连接 → 求值路由（L2 的对外接口）。

L1 只调用这里的少数几个方法：

* :meth:`BridgeService.list_hosts` / :meth:`BridgeService.status` —— 发现与状态
* :meth:`BridgeService.launch` / :meth:`BridgeService.wait_ready` —— 启动与就绪
* :meth:`BridgeService.call` —— **唯一的执行原语**："执行一条命令并返回结果"
* :meth:`BridgeService.resync` —— 差异报告与 DESYNC 清除
* :meth:`BridgeService.reload_panel` —— 开发期重载

架构约束（说明书 §3.2）：L2 是唯一接触宿主的层；安全策略绝不下放到 L3。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .. import __version__
from ..config import Config
from ..contract import CommandSpec, Contract, load_contract
from ..errors import ToolError
from ..hosts import (
    CEP_HOST_IDS,
    HostAdapter,
    HostDescriptor,
    HostSelection,
    HostType,
    adapter_for,
    parse_host_key,
)
from ..hosts.registry import RegistryReader, default_reader
from .cep_client import CepClient, Handshake, HandshakeWatcher, pid_alive
from .deployer import CepDeployer, DeployError, DeployResult
from .jsx_runner import BridgeOutcome, JsxRunner, ScriptSourceIndex
from .manifest_check import ManifestCheck, check_manifest

__all__ = ["HostSession", "BridgeService", "ResolutionResult"]

#: 上报给面板的运行期配置（panel 侧用于日志与 QE 门禁）
_PANEL_CONFIG_KEYS = ("enable_qe", "log_level", "log_dir")


@dataclass
class HostSession:
    """一个已连接的面板会话。"""

    descriptor: HostDescriptor
    handshake: Handshake
    client: CepClient
    runner: JsxRunner
    connected_at: float = field(default_factory=time.time)
    last_state: dict[str, Any] | None = None
    last_call_at: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.descriptor.key

    def to_public(self) -> dict[str, Any]:
        return {
            "host_key": self.key,
            "descriptor": self.descriptor.to_public(),
            "connection": self.client.snapshot(),
            "connected_at": self.connected_at,
            "last_call_at": self.last_call_at,
            "last_state": self.last_state,
            "runner": self.runner.snapshot(),
        }


@dataclass
class ResolutionResult:
    """宿主选择结果。"""

    descriptor: HostDescriptor | None
    selection: HostSelection
    running: list[HostDescriptor] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    installs: list[HostDescriptor] = field(default_factory=list)

    def require(self, *, for_launch: bool) -> HostDescriptor:
        if self.descriptor is None:
            if self.selection.requested_version:
                raise ToolError(
                    "HOST_NOT_INSTALLED",
                    f"没有安装版本 {self.selection.requested_version} 的宿主",
                    "调用 host_list 查看已发现的宿主与版本，或修正配置项 hosts.<宿主>.version",
                    detail={"discovered": [item.to_public() for item in self.installs]},
                )
            raise ToolError(
                "HOST_NOT_INSTALLED",
                "本机没有发现已安装的该宿主",
                "调用 host_list 查看已发现的宿主；若确认已安装，请检查注册表键 "
                "HKLM\\SOFTWARE\\Adobe\\Premiere Pro\\<版本>\\InstallPath 是否存在",
                detail={"discovered": [item.to_public() for item in self.installs]},
            )
        if for_launch and self.selection.ambiguous:
            raise ToolError(
                "HOST_AMBIGUOUS_VERSION",
                self.selection.note or "多个版本并存，未指定目标版本",
                "在调用中显式给出 host=\"premiere:<版本>\"，或设置配置项 hosts.premiere.version；"
                "**不得在不告知用户的情况下接管任意一个正在运行的版本**",
                detail={
                    "alternatives": [item.to_public() for item in self.selection.alternatives],
                    "chosen_if_forced": self.descriptor.to_public(),
                },
            )
        return self.descriptor


class BridgeService:
    """L2 的门面。"""

    def __init__(
        self,
        config: Config,
        *,
        logger: Any = None,
        contract: Contract | None = None,
        registry_reader: RegistryReader | None = None,
        process_probe: Callable[[HostAdapter, HostDescriptor], tuple[bool, int | None]] | None = None,
        discovery_ttl_s: float = 10.0,
        source_index: ScriptSourceIndex | None = None,
    ) -> None:
        self.config = config
        self.logger = logger
        self.contract = contract or load_contract()
        self._reader = registry_reader or default_reader()
        self._process_probe = process_probe
        self._discovery_ttl_s = discovery_ttl_s
        self._adapters: dict[HostType, HostAdapter] = {
            HostType.premiere: adapter_for(HostType.premiere),
            HostType.media_encoder: adapter_for(HostType.media_encoder),
        }
        self._descriptors: dict[HostType, list[HostDescriptor]] = {}
        self._discovered_at: float = 0.0
        self._sessions: dict[str, HostSession] = {}
        #: key → 尚未通过 host_resync 清除的 DESYNC 原因（跨会话保留）
        self._carry_desync: dict[str, str] = {}
        self._deployer = CepDeployer(
            source_dir=config.cep.source_dir,
            extensions_dir=config.cep.extensions_dir,
            extension_id=config.cep.extension_id,
            backup_dir=config.cep.backup_dir,
            logger=logger,
        )
        self._source_index = source_index or ScriptSourceIndex(config.cep.source_dir)
        self._deploy_result: DeployResult | None = None
        self._deploy_checked = False
        self._lock = asyncio.Lock()
        self._shutdown = False

    # ================= 发现 =================
    def _default_selector(self, host_type: HostType) -> str | None:
        host_config = (
            self.config.hosts.premiere
            if host_type is HostType.premiere
            else self.config.hosts.media_encoder
        )
        return host_config.version

    def _host_config(self, host_type: HostType) -> Any:
        return (
            self.config.hosts.premiere
            if host_type is HostType.premiere
            else self.config.hosts.media_encoder
        )

    def _probe(self, adapter: HostAdapter, descriptor: HostDescriptor) -> tuple[bool, int | None]:
        """探测宿主进程；默认走适配器的 psutil 检测，测试可注入替身。"""
        if self._process_probe is not None:
            return self._process_probe(adapter, descriptor)
        return adapter.is_running(descriptor)

    def _refresh_descriptor(self, descriptor: HostDescriptor) -> HostDescriptor:
        adapter = self._adapters[descriptor.host_type]
        running, pid = self._probe(adapter, descriptor)
        descriptor.running = running
        descriptor.pid = pid
        return descriptor

    def refresh_discovery(self, *, force: bool = False) -> dict[HostType, list[HostDescriptor]]:
        """重新读注册表并刷新运行状态（带短 TTL 缓存）。"""
        now = time.monotonic()
        if not force and self._descriptors and now - self._discovered_at < self._discovery_ttl_s:
            return self._descriptors

        found: dict[HostType, list[HostDescriptor]] = {}
        for host_type, adapter in self._adapters.items():
            if not self._host_config(host_type).enabled:
                found[host_type] = []
                continue
            descriptors = adapter.discover(self._reader)
            for descriptor in descriptors:
                self._refresh_descriptor(descriptor)
                handshake = self._handshake_for(descriptor)
                descriptor.connected = handshake is not None
                if handshake is not None:
                    descriptor.panel_version = handshake.panel_version
                    descriptor.contract_schema_version = handshake.contract_schema_version
            found[host_type] = descriptors
        self._descriptors = found
        self._discovered_at = now
        return found

    def _watcher(self) -> HandshakeWatcher:
        return HandshakeWatcher(
            self.config.bridge.handshake_dir,
            poll_s=self.config.bridge.handshake_poll_s,
            logger=self.logger,
        )

    def _handshake_for(self, descriptor: HostDescriptor, *, exclude: set[str] | None = None) -> Handshake | None:
        """找出与该宿主的运行进程对应的握手。"""
        handshakes = self._watcher().find(descriptor.cep_host_id)
        if exclude:
            handshakes = [item for item in handshakes if item.key not in exclude]
        if descriptor.pid:
            for handshake in handshakes:
                if handshake.pid == descriptor.pid:
                    return handshake
        return None

    def resolve(self, host_selector: str | None, *, refresh: bool = False) -> ResolutionResult:
        """把工具的 ``host`` 参数解析成一个具体宿主。"""
        host_type, version = parse_host_key(host_selector)
        installs_by_type = self.refresh_discovery(force=refresh)

        warnings: list[str] = []
        if host_type is None:
            host_type = HostType.premiere
        adapter = self._adapters[host_type]
        installs = installs_by_type.get(host_type, [])
        running = [item for item in installs if item.running]

        requested = version or self._default_selector(host_type)
        selection = adapter.select(installs, requested)

        # 只有一个正在运行的实例时，用它在运行这一点消除歧义（比"版本号最高"更可靠）。
        if selection.chosen is not None and selection.ambiguous and running:
            if len(running) == 1:
                selection = HostSelection(
                    chosen=running[0],
                    alternatives=[item for item in installs if item is not running[0]],
                    ambiguous=False,
                    note=f"选择当前正在运行的版本 {running[0].version}",
                    requested_version=requested,
                )
            else:
                warnings.append(
                    f"有多个版本正在运行：{[item.version for item in running]}；"
                    f"默认选择版本最高的 {selection.chosen.version}"
                )
        if selection.ambiguous and selection.chosen is not None:
            warnings.append(selection.note)
        if selection.chosen is not None:
            self._refresh_descriptor(selection.chosen)
        return ResolutionResult(
            descriptor=selection.chosen,
            selection=selection,
            running=running,
            warnings=warnings,
            installs=installs,
        )

    # ================= 部署 =================
    def deployment_status(self) -> dict[str, Any]:
        status = self._deployer.status()
        if self._deploy_result is not None:
            status["last_deploy"] = self._deploy_result.to_public()
        return status

    def manifest_check(self) -> ManifestCheck:
        return self._deployer.check()

    def deploy_panel(self, *, force: bool = False) -> DeployResult:
        """部署面板（幂等：清单哈希一致时跳过）。"""
        try:
            result = self._deployer.deploy(force=force)
        except DeployError as exc:
            raise ToolError(exc.code, exc.message, exc.remedy, detail=exc.detail) from exc
        self._deploy_result = result
        self._deploy_checked = True
        return result

    async def ensure_deployed(self, *, force: bool = False) -> dict[str, Any]:
        """确保面板已部署且与仓库版本一致；失败时抛出带 remedy 的错误。"""
        if self._deploy_checked and not force:
            status = self._deployer.status()
            if self._deploy_result is not None:
                status["last_deploy"] = self._deploy_result.to_public()
            return status
        status = self._deployer.status()
        manifest = status["manifest"]
        if not manifest["ok"]:
            raise ToolError(
                "PANEL_MANIFEST_INVALID",
                "面板 manifest 不合规，已拒绝部署：" + "；".join(
                    f"{issue['code']}: {issue['message']}"
                    for issue in manifest["issues"]
                    if issue["blocking"]
                ),
                "按 detail 列出的缺失项修正 cep-extension/CSXS/manifest.xml 后重试；"
                "其中 --mixed-context 缺失会导致面板里 require 未定义",
                detail=manifest,
            )
        if status["in_sync"]:
            self._deploy_checked = True
            return status
        result = self.deploy_panel(force=force)
        status["last_deploy"] = result.to_public()
        status["in_sync"] = True
        return status

    # ================= 会话 =================
    @property
    def sessions(self) -> dict[str, HostSession]:
        return dict(self._sessions)

    def session_for(self, host_selector: str | None = None) -> HostSession | None:
        if host_selector:
            host_type, version = parse_host_key(host_selector)
            for session in self._sessions.values():
                if host_type is not None and session.descriptor.host_type is not host_type:
                    continue
                if version and session.descriptor.version != version:
                    continue
                return session
        for session in self._sessions.values():
            if session.client.connected:
                return session
        return None

    def _drop_session(self, key: str, reason: str) -> None:
        session = self._sessions.pop(key, None)
        if session is not None:
            # DESYNC 描述的是**宿主状态未知**，与连接是否活着无关：
            # 丢掉会话不能让这个事实消失（否则一次重连就会把"状态未知"洗白成"状态正常"，
            # 后续写操作会在错误的前提上执行）。这里把它带到下一个会话。
            if session.runner.desynced:
                self._carry_desync[key] = session.runner.desync_reason or reason
            if self.logger is not None:
                self.logger.info(
                    "session_dropped",
                    host_key=key,
                    reason=reason,
                    carried_desync=self._carry_desync.get(key),
                )
        elif self.logger is not None:
            self.logger.info("session_dropped", host_key=key, reason=reason)

    async def ensure_session(
        self,
        host_selector: str | None = None,
        *,
        launch_if_needed: bool | None = None,
        timeout_s: float | None = None,
    ) -> HostSession:
        """确保有一个可用的、已认证的会话；必要时部署面板、启动宿主、等待握手。"""
        if self._shutdown:
            raise ToolError("INTERNAL_ERROR", "桥接服务已关闭", "重启 MCP 服务器")
        resolution = self.resolve(host_selector)
        descriptor = resolution.require(for_launch=bool(launch_if_needed))
        adapter = self._adapters[descriptor.host_type]

        existing = self._sessions.get(descriptor.key)
        if existing is not None:
            if existing.client.connected and existing.client.is_stale():
                if self.logger is not None:
                    self.logger.warning(
                        "session_heartbeat_stale",
                        host_key=descriptor.key,
                        heartbeat_age_s=existing.client.heartbeat_age(),
                    )
                existing.runner.mark_desync("心跳超时，连接判定失效")
                await existing.client.close(reason="heartbeat_stale")
                self._drop_session(descriptor.key, "heartbeat_stale")
            elif existing.client.connected and pid_alive(existing.handshake.pid):
                return existing
            else:
                self._drop_session(descriptor.key, "connection_lost")

        descriptor = self._refresh_descriptor(descriptor)
        handshake = self._handshake_for(descriptor)
        exclude: set[str] = set()

        if handshake is None and descriptor.running:
            # 宿主在跑但没有握手：面板未加载，或者宿主刚启动还没写握手。
            handshakes = self._watcher().find(descriptor.cep_host_id)
            if handshakes and not descriptor.pid:
                handshake = handshakes[0]

        if handshake is None:
            deployment = await self._deployment_for_error()
            host_config = self._host_config(descriptor.host_type)
            should_launch = (
                host_config.launch_on_demand if launch_if_needed is None else launch_if_needed
            )
            if not descriptor.running:
                if not should_launch:
                    raise ToolError(
                        "HOST_NOT_RUNNING",
                        f"{descriptor.host_type.value} 未运行（版本 {descriptor.version}）",
                        "调用 host_launch 启动宿主，或在配置中把 "
                        f"hosts.{descriptor.host_type.value}.launch_on_demand 设为 true",
                        detail={
                            "descriptor": descriptor.to_public(),
                            "launch_on_demand": False,
                            "deployment": deployment,
                        },
                    )
                await self.launch(host_selector, wait=False)
                exclude = set()
                timeout = float(
                    timeout_s if timeout_s is not None else host_config.launch_timeout_s
                )
                handshake = await self._wait_for_handshake(descriptor, timeout=timeout, exclude=exclude)
            else:
                # 宿主在运行但面板没起来：先尝试等待面板写出握手，超时后给出部署与调试指引。
                # 等待时长沿用 launch_timeout_s：对使用者来说"等面板"只有一个旋钮。
                timeout = float(
                    timeout_s
                    if timeout_s is not None
                    else host_config.launch_timeout_s
                )
                try:
                    handshake = await self._wait_for_handshake(
                        descriptor, timeout=timeout, exclude=exclude
                    )
                except ToolError as exc:
                    raise ToolError(
                        "PANEL_NOT_LOADED",
                        "宿主正在运行，但面板没有写出握手文件",
                        "确认 CEP 扩展已部署到用户级扩展目录，且已按官方开发设置启用 "
                        "PlayerDebugMode；部署或改设置后需要**重启宿主**才会加载面板；"
                        "也可用 host_reload_panel（面板已在运行时）重载",
                        detail={
                            "descriptor": descriptor.to_public(),
                            "deployment": deployment,
                            "wait_error": {
                                "code": exc.code,
                                "detail": exc.detail,
                            },
                        },
                    ) from exc

        return await self._connect(descriptor, handshake)

    async def _deployment_for_error(self) -> dict[str, Any]:
        try:
            return await self.ensure_deployed()
        except ToolError as exc:
            return {"error": {"code": exc.code, "message": exc.message}, "ok": False}

    async def _wait_for_handshake(
        self, descriptor: HostDescriptor, *, timeout: float, exclude: set[str]
    ) -> Handshake:
        watcher = self._watcher()
        return await watcher.wait_for(
            descriptor.cep_host_id, timeout=timeout, exclude=exclude
        )

    async def _connect(self, descriptor: HostDescriptor, handshake: Handshake) -> HostSession:
        async with self._lock:
            existing = self._sessions.get(descriptor.key)
            if existing is not None and existing.client.connected:
                return existing

            client = CepClient(
                handshake,
                bridge_config=self.config.bridge,
                logger=self.logger,
                client_version=__version__,
                on_close=lambda error, key=descriptor.key: self._mark_session_desync(key, error),
            )
            if self.logger is not None:
                self.logger.info(
                    "session_connecting", host_key=descriptor.key, uri=handshake.uri
                )
            await client.connect()

            warnings: list[str] = []
            if client.contract_schema_version is not None and client.contract_schema_version != self.contract.schema_version:
                warnings.append(
                    f"面板契约版本 {client.contract_schema_version} 与 L1 的 "
                    f"{self.contract.schema_version} 不一致：请重新部署面板"
                )
            if handshake.host_version and handshake.host_version != descriptor.version:
                warnings.append(
                    f"面板报告的宿主版本 {handshake.host_version} 与注册表记录的 "
                    f"{descriptor.version} 不一致，请确认目标版本"
                )

            runner = JsxRunner(
                client=client,
                eval_timeout_s=self._host_config(descriptor.host_type).eval_timeout_s,
                logger=self.logger,
                source_index=self._source_index,
                host_key=descriptor.key,
            )
            carried = self._carry_desync.get(descriptor.key)
            if carried:
                # 上一个会话留下的 DESYNC 必须继承：重连不等于状态已知。
                runner.mark_desync(f"上一个会话遗留：{carried}")

            session = HostSession(
                descriptor=descriptor,
                handshake=handshake,
                client=client,
                runner=runner,
                warnings=warnings,
            )
            self._sessions[descriptor.key] = session

            try:
                await client.send_config(self._panel_config())
            except ToolError as exc:  # pragma: no cover - 配置下发失败不致命
                if self.logger is not None:
                    self.logger.warning("panel_config_failed", error=exc.message)
            return session

    def _panel_config(self) -> dict[str, Any]:
        return {
            "enable_qe": bool(self.config.extendscript.enable_qe),
            "log_level": self.config.server.log_level,
            "log_dir": str(self.config.server.log_dir),
            "heartbeat_s": self.config.bridge.heartbeat_s,
            "max_response_chars": self.config.bridge.max_response_chars,
            "client_version": __version__,
        }

    def _mark_session_desync(self, key: str, error: Exception | None) -> None:
        """连接层回调：把对应会话标记为 DESYNC，等待显式 resync（说明书 §5.3、§5.4）。"""
        session = self._sessions.get(key)
        if session is None:
            return
        session.runner.mark_desync(
            f"连接断开：{error}" if error else "连接被关闭，在途命令结果不确定"
        )

    async def reconnect(self, host_selector: str | None = None, *, timeout_s: float = 30) -> HostSession:
        """重连（退避上限见配置）。"""
        resolution = self.resolve(host_selector)
        descriptor = resolution.require(for_launch=False)
        delay = 1.0
        deadline = time.monotonic() + timeout_s
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            self._drop_session(descriptor.key, "reconnect")
            try:
                return await self.ensure_session(
                    host_selector, launch_if_needed=False, timeout_s=min(10, timeout_s)
                )
            except ToolError as exc:
                last_error = exc
                if exc.code not in ("PANEL_NOT_LOADED", "BRIDGE_DISCONNECTED", "BRIDGE_AUTH_FAILED"):
                    raise
                await asyncio.sleep(min(delay, self.config.bridge.reconnect_backoff_max_s))
                delay = min(delay * 2, self.config.bridge.reconnect_backoff_max_s)
        raise ToolError(
            "BRIDGE_DISCONNECTED",
            "重连失败：超时",
            "确认宿主仍在运行且面板已加载；必要时 host_reload_panel 或重启宿主",
            detail={"last_error": str(last_error)},
        )

    # ================= 命令执行 =================
    async def call(
        self,
        command: CommandSpec,
        args: Mapping[str, Any] | None = None,
        *,
        host_selector: str | None = None,
        trace_id: str | None = None,
        dry_run: bool = False,
        idempotency_key: str | None = None,
        confirmed: bool = False,
        token_prefix: str | None = None,
        timeout: float | None = None,
        launch_if_needed: bool | None = None,
    ) -> BridgeOutcome:
        """执行一条命令。这是 L2 对上层暴露的**唯一**执行原语。"""
        if command.capability == "qe" and not self.config.extendscript.enable_qe:
            raise ToolError(
                "QE_DISABLED",
                f"命令 {command.name} 依赖未公开的 QE 接口，当前配置未启用",
                "若确实需要：把配置项 extendscript.enable_qe 设为 true 并重启宿主面板。"
                "注意 QE 是 Adobe 未公开的接口，可能导致宿主崩溃（说明书 §6.2）",
                detail={"command": command.name, "capability": command.capability},
            )

        session = await self.ensure_session(host_selector, launch_if_needed=launch_if_needed)
        outcome = await session.runner.evaluate(
            command,
            dict(args or {}),
            trace_id=trace_id,
            dry_run=dry_run,
            idempotency_key=idempotency_key,
            confirmed=confirmed,
            token_prefix=token_prefix,
            timeout=timeout,
        )
        session.last_call_at = time.time()
        if outcome.host:
            session.last_state = dict(outcome.host)
        warnings = list(session.warnings)
        if command.capability in ("qe", "public_or_qe") and self.config.extendscript.enable_qe:
            warnings.append(
                "QE 接口已启用：这是 Adobe 未公开的接口，异常时可能导致宿主崩溃（说明书 §6.2）"
            )
        warnings.extend(outcome.warnings)
        if warnings:
            outcome.warnings = list(dict.fromkeys(warnings))
        return outcome

    # ================= 启动与就绪 =================
    def list_hosts(self) -> dict[str, Any]:
        discovered = self.refresh_discovery(force=True)
        items: list[dict[str, Any]] = []
        for host_type, descriptors in discovered.items():
            adapter = self._adapters[host_type]
            selection = adapter.select(descriptors, self._default_selector(host_type))
            for descriptor in descriptors:
                public = descriptor.to_public()
                public["host_type"] = host_type.value
                public["selected_by_default"] = bool(
                    selection.chosen is not None and selection.chosen.key == descriptor.key
                )
                public["session"] = (
                    self._sessions[descriptor.key].client.snapshot()
                    if descriptor.key in self._sessions
                    else None
                )
                items.append(public)
        return {
            "hosts": items,
            "enabled": {
                host_type.value: self._host_config(host_type).enabled
                for host_type in self._adapters
            },
            "installs_count": len(items),
            "notes": [
                "发现方式为注册表读取（不使用文件系统猜测）",
                "多个版本并存且未指定版本时，host_list/host_status 会报告歧义，"
                "启动宿主前必须确认目标版本",
            ],
        }

    async def launch(
        self,
        host_selector: str | None = None,
        *,
        wait: bool = True,
        project_path: str | None = None,
    ) -> dict[str, Any]:
        resolution = self.resolve(host_selector)
        descriptor = resolution.require(for_launch=True)
        adapter = self._adapters[descriptor.host_type]

        running, pid = self._probe(adapter, descriptor)
        if running:
            descriptor.running = True
            descriptor.pid = pid
            return {
                "started": False,
                "already_running": True,
                "descriptor": descriptor.to_public(),
                "note": "该宿主的进程已经在运行，未重复启动",
            }
        if not descriptor.executable_exists:
            raise ToolError(
                "HOST_NOT_INSTALLED",
                f"注册表给出的安装路径下找不到可执行文件：{descriptor.executable}",
                "确认宿主安装完整（未被移动/卸载残留）；必要时修配置或重装宿主",
                detail=descriptor.to_public(),
            )

        pre_existing = {item.key for item in self._watcher().find(descriptor.cep_host_id)}
        started_at = time.time()
        new_pid = adapter.launch(descriptor, project_path)
        descriptor.running = True
        descriptor.pid = new_pid
        if self.logger is not None:
            self.logger.info(
                "host_launched", host_key=descriptor.key, pid=new_pid, project=project_path
            )
        payload: dict[str, Any] = {
            "started": True,
            "already_running": False,
            "pid": new_pid,
            "descriptor": descriptor.to_public(),
        }
        if not wait:
            return payload

        timeout = float(self._host_config(descriptor.host_type).launch_timeout_s)
        handshake = await self._wait_for_handshake_new(
            descriptor, timeout=timeout, exclude=pre_existing, min_started_at=started_at - 5
        )
        session = await self._connect(descriptor, handshake)
        payload["session"] = session.client.snapshot()
        return payload

    async def _wait_for_handshake_new(
        self,
        descriptor: HostDescriptor,
        *,
        timeout: float,
        exclude: set[str],
        min_started_at: float | None,
    ) -> Handshake:
        watcher = self._watcher()
        try:
            return await watcher.wait_for(
                descriptor.cep_host_id,
                timeout=timeout,
                exclude=exclude,
                min_started_at=min_started_at,
            )
        except ToolError as exc:
            raise ToolError(
                "HOST_LAUNCH_TIMEOUT",
                f"宿主启动后 {timeout:.0f} 秒内没有完成面板握手",
                "增大 hosts.premiere.launch_timeout_s；确认 CEP 扩展已部署且已设置 "
                "PlayerDebugMode；若宿主弹出登录/欢迎对话框，先手工处理",
                detail={"descriptor": descriptor.to_public(), "wait": exc.detail},
            ) from exc

    async def wait_ready(
        self, host_selector: str | None = None, *, timeout_s: float | None = None
    ) -> dict[str, Any]:
        resolution = self.resolve(host_selector)
        descriptor = resolution.require(for_launch=False)
        timeout = float(timeout_s if timeout_s is not None else 30)
        handshake = self._handshake_for(descriptor)
        if handshake is None:
            handshake = await self._wait_for_handshake(descriptor, timeout=timeout, exclude=set())
        session = await self._connect(descriptor, handshake)
        return {
            "ready": True,
            "host_key": session.key,
            "connection": session.client.snapshot(),
            "warnings": list(session.warnings) + list(session.client.warnings),
        }

    async def reload_panel(self, host_selector: str | None = None) -> dict[str, Any]:
        session = await self.ensure_session(host_selector, launch_if_needed=False)
        command = self.contract.by_name["panel.reload"]
        outcome = await session.runner.evaluate(command, {}, trace_id=None)
        self._drop_session(session.key, "panel_reload")
        if not outcome.ok:
            raise ToolError(
                outcome.error.get("code", "EVAL_SCRIPT_ERROR") if outcome.error else "EVAL_SCRIPT_ERROR",
                (outcome.error or {}).get("message", "重载面板失败"),
                (outcome.error or {}).get("remedy", "在宿主中手动关闭并重新打开面板"),
                detail=outcome.error,
            )
        return {
            "reloaded": True,
            "host_key": session.key,
            "note": "面板正在重载，桥接连接会短暂中断；随后用 host_wait_ready 重新建立连接",
        }

    # ================= 重新同步 =================
    async def resync(self, host_selector: str | None = None) -> dict[str, Any]:
        """重新读取宿主真实状态并与 L1 记录比对（说明书 §5.3 第 4 条）。"""
        resolution = self.resolve(host_selector)
        descriptor = resolution.require(for_launch=False)
        reconnected = False
        session = self._sessions.get(descriptor.key)
        if session is None or not session.client.connected:
            session = await self.ensure_session(host_selector, launch_if_needed=False)
            reconnected = True

        previous = session.last_state
        command = self.contract.by_name["host.state"]
        outcome = await session.runner.evaluate(command, {}, override_desync=True)
        if not outcome.ok:
            error = outcome.error or {}
            raise ToolError(
                error.get("code", "SCRIPT_ERROR"),
                error.get("message", "读取宿主状态失败"),
                error.get("remedy", "确认宿主窗口没有模态对话框，然后重试 host_resync"),
                detail=error,
            )

        fresh = dict(outcome.data or {})
        differences = _diff_states(previous, fresh)
        session.last_state = fresh
        was_desynced = session.runner.desynced or bool(self._carry_desync.pop(session.key, None))
        session.runner.clear_desync("host_resync")
        return {
            "host_key": session.key,
            "previous": previous,
            "current": fresh,
            "differences": differences,
            "changed": bool(differences),
            "desync_cleared": was_desynced,
            "reconnected": reconnected,
            "note": (
                "差异为空表示宿主状态与 L1 记录一致；有差异时请以 current 为准，"
                "不要假设先前的写操作没有生效"
            ),
        }

    # ================= 状态与生命周期 =================
    async def status(self, host_selector: str | None = None) -> dict[str, Any]:
        resolution = self.resolve(host_selector, refresh=True)
        deployment = self.deployment_status()
        sessions = [session.to_public() for session in self._sessions.values()]
        payload: dict[str, Any] = {
            "host": {
                "selected": resolution.descriptor.to_public() if resolution.descriptor else None,
                "ambiguous": resolution.selection.ambiguous,
                "selection_note": resolution.selection.note,
                "alternatives": [item.to_public() for item in resolution.selection.alternatives],
                "installs": [item.to_public() for item in resolution.installs],
                "running": [item.to_public() for item in resolution.running],
            },
            "deployment": deployment,
            "sessions": sessions,
            "contract": {
                "schema_version": self.contract.schema_version,
                "commands": len(self.contract.commands),
            },
            "config": {
                "hosts": {
                    "premiere": self.config.hosts.premiere.model_dump(mode="json"),
                    "media_encoder": self.config.hosts.media_encoder.model_dump(mode="json"),
                },
                "bridge": self.config.bridge.model_dump(mode="json"),
                "extendscript": self.config.extendscript.model_dump(mode="json"),
                "handshake_dir": str(self.config.bridge.handshake_dir),
            },
        }
        warnings = list(resolution.warnings) + list(self._health_warnings(resolution, deployment))
        payload["warnings"] = warnings
        payload["handshakes"] = [item.to_safe_public() for item in self._watcher().scan()]
        return payload

    def _health_warnings(self, resolution: ResolutionResult, deployment: Mapping[str, Any]) -> list[str]:
        warnings: list[str] = []
        if not deployment.get("installed"):
            warnings.append("CEP 面板尚未部署到用户级扩展目录")
        elif not deployment.get("in_sync"):
            warnings.append("CEP 面板已部署，但仓库内容更新过：下一次连接前会重新部署")
        if deployment.get("extensions_dir_writable") is False:
            warnings.append(
                "CEP 扩展目录不可写：" + str(deployment.get("writability_note") or "未知原因")
            )
        player_debug = deployment.get("player_debug_mode") or {}
        if player_debug.get("applicable") and not player_debug.get("enabled"):
            warnings.append(
                "未检测到 PlayerDebugMode=1：未签名扩展可能无法加载"
                "（Adobe 官方的扩展开发设置；与宿主授权无关）"
            )
        if not resolution.installs:
            warnings.append("注册表中没有发现该宿主的安装记录")
        return warnings

    async def shutdown(self) -> None:
        self._shutdown = True
        for key, session in list(self._sessions.items()):
            try:
                await session.client.close(reason="server_shutdown")
            except Exception:  # pragma: no cover - 关闭失败不应阻塞退出
                pass
            self._sessions.pop(key, None)

    async def start_background_tasks(self) -> None:
        """启动后台任务（当前只有会话健康检查）。"""
        return None

    def adapter(self, host_type: HostType) -> HostAdapter:
        return self._adapters[host_type]

    def cep_host_id(self, host_type: HostType) -> str:
        return CEP_HOST_IDS[host_type]


def _diff_states(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> list[dict[str, Any]]:
    """比对两次宿主状态，产出可读的差异列表。"""
    if previous is None:
        return [
            {
                "field": "(baseline)",
                "previous": None,
                "current": None,
                "note": "L1 此前没有该宿主的状态记录，本次仅建立基线",
            }
        ]
    keys: Sequence[str] = ("has_project", "project_path", "dirty", "active_sequence", "playhead_s")
    differences: list[dict[str, Any]] = []
    for key in keys:
        old = previous.get(key)
        new = current.get(key)
        if old != new:
            differences.append({"field": key, "previous": old, "current": new})
    return differences
