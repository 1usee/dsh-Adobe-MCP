"""CEP 客户端：握手发现、WebSocket 连接、认证、心跳与请求配对（说明书 §5.1、§5.4）。

连接建立流程：

1. 面板启动时绑定 ``127.0.0.1:0``，由操作系统分配空闲端口；
2. 面板生成随机令牌（每次启动刷新）；
3. 面板把连接信息写入 ``%TEMP%\\dsh-adobe-mcp\\handshake-<宿主>-<进程号>.json``；
4. 本模块**以固定间隔轮询该目录**（默认 0.5 秒，纯异步轮询，不引入文件系统事件库）；
5. WebSocket 连接建立后的第一帧是认证帧，携带该令牌；校验失败面板立即断开。

**安全收益**：仅绑定回环地址 + 每次启动的随机令牌，同机其他进程无法冒充或劫持桥接连接。
日志中**不记录令牌明文**。

**进程存活校验必须使用 psutil，禁止 ``os.kill(pid, 0)``**：后者在 Windows 上会经
``TerminateProcess`` 无条件终止目标进程——用它检测 Premiere Pro 是否存活，
会直接把用户的宿主进程杀掉（说明书 §5.1）。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from pydantic import BaseModel, Field, ValidationError

from ..config import BridgeConfig
from ..errors import ToolError
from .protocol import (
    PROTOCOL_VERSION,
    AuthFailedFrame,
    AuthFrame,
    AuthOkFrame,
    ConfigFrame,
    HeartbeatFrame,
    PairingTable,
    ProtocolError,
    ResponseFrame,
    build_request,
    encode_frame,
    make_request_id,
    parse_frame,
)

__all__ = ["Handshake", "HandshakeWatcher", "CepClient", "pid_alive", "handshake_file_name"]


def handshake_file_name(host: str, pid: int) -> str:
    return f"handshake-{host}-{pid}.json"


def pid_alive(pid: int | None) -> bool:
    """用 psutil 判断进程是否存活。**绝不用 ``os.kill(pid, 0)``**（见模块文档）。"""
    if not pid or pid <= 0:
        return False
    try:
        import psutil
    except ImportError:  # pragma: no cover - 依赖缺失时保守处理
        return False
    try:
        return bool(psutil.pid_exists(int(pid)))
    except Exception:  # pragma: no cover - psutil 在极端情况下会抛错
        return False


class Handshake(BaseModel):
    """握手文件内容（说明书 §5.1）。"""

    protocol: int = PROTOCOL_VERSION
    port: int
    token: str
    host: str
    pid: int
    host_version: str | None = None
    panel_version: str | None = None
    contract_schema_version: int | None = None
    started_at: float | None = None

    @property
    def key(self) -> str:
        return f"{self.host}:{self.pid}"

    @property
    def uri(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def to_safe_public(self) -> dict[str, Any]:
        """对外展示用（**不含令牌**）。"""
        return {
            "host": self.host,
            "pid": self.pid,
            "port": self.port,
            "uri": self.uri,
            "protocol": self.protocol,
            "host_version": self.host_version,
            "panel_version": self.panel_version,
            "contract_schema_version": self.contract_schema_version,
            "started_at": self.started_at,
        }


class HandshakeWatcher:
    """轮询握手目录，读取有效握手文件并清理陈旧文件。"""

    def __init__(
        self,
        directory: Path,
        *,
        poll_s: float = 0.5,
        logger: Any = None,
        alive: Callable[[int | None], bool] | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.poll_s = float(poll_s)
        self.logger = logger
        self._alive = alive or pid_alive
        self.last_errors: list[str] = []

    # ---- 扫描 ----------------------------------------------------------
    def scan(self, *, clean: bool = True) -> list[Handshake]:
        """返回当前有效的握手记录；顺带清理进程已消失的陈旧文件。"""
        self.last_errors = []
        if not self.directory.is_dir():
            return []
        found: list[Handshake] = []
        for path in sorted(self.directory.glob("handshake-*.json")):
            handshake = self._read(path)
            if handshake is None:
                continue
            if not self._alive(handshake.pid):
                if clean:
                    try:
                        path.unlink()
                        if self.logger is not None:
                            self.logger.info(
                                "handshake_stale_removed",
                                file=path.name,
                                pid=handshake.pid,
                                host=handshake.host,
                            )
                    except OSError as exc:  # pragma: no cover - 依赖环境
                        self.last_errors.append(f"清理陈旧握手文件失败：{path.name}: {exc}")
                continue
            found.append(handshake)
        return found

    def _read(self, path: Path) -> Handshake | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.last_errors.append(f"握手文件不可读：{path.name}: {exc}")
            return None
        try:
            return Handshake.model_validate(raw)
        except ValidationError as exc:
            self.last_errors.append(f"握手文件字段不合法：{path.name}: {exc.errors()[:2]}")
            return None

    def find(self, host: str | None = None) -> list[Handshake]:
        found = self.scan()
        if host is None:
            return found
        return [item for item in found if item.host == host]

    # ---- 等待 ----------------------------------------------------------
    async def wait_for(
        self,
        host: str,
        *,
        timeout: float,
        exclude: Iterable[str] = (),
        min_started_at: float | None = None,
    ) -> Handshake:
        """等待一个**新出现**的握手（用于 launch/wait_ready）。

        :param exclude: 需要忽略的握手 key（启动前已存在的那些）。
        :param min_started_at: 只接受在该时间点之后启动的面板。
        """
        excluded = set(exclude)
        deadline = time.monotonic() + float(timeout)
        last_note = ""
        while True:
            for handshake in self.find(host):
                if handshake.key in excluded:
                    continue
                if min_started_at is not None and handshake.started_at is not None:
                    if handshake.started_at < min_started_at:
                        continue
                return handshake
            if time.monotonic() >= deadline:
                detail = {
                    "handshake_dir": str(self.directory),
                    "host": host,
                    "timeout_s": timeout,
                    "observed": [item.to_safe_public() for item in self.scan()],
                    "errors": self.last_errors,
                    "note": last_note,
                }
                raise ToolError(
                    "PANEL_NOT_LOADED",
                    f"等待 {host} 面板在 {timeout:.0f} 秒内没有出现握手文件",
                    "确认 CEP 扩展已部署（host_status 会给出部署状态）且已设置 PlayerDebugMode；"
                    "宿主要重启后才会加载新部署的面板",
                    detail=detail,
                )
            await asyncio.sleep(self.poll_s)


# --------------------------------------------------------------------------
# WebSocket 客户端
# --------------------------------------------------------------------------
def _connect_factory() -> Any:
    """延迟导入 websockets，便于在缺依赖时给出可读错误。"""
    try:
        from websockets.asyncio.client import connect
    except ImportError:  # pragma: no cover - 依赖缺失
        try:
            from websockets import connect  # type: ignore[no-redef]
        except ImportError as exc:
            raise ToolError(
                "INTERNAL_ERROR",
                "缺少 websockets 依赖，无法连接宿主面板",
                "在运行环境执行 pip install -e . 安装声明的依赖",
                detail=str(exc),
            ) from exc
    return connect


class CepClient:
    """与单个面板实例的连接。"""

    def __init__(
        self,
        handshake: Handshake,
        *,
        bridge_config: BridgeConfig,
        logger: Any = None,
        client_version: str = "0.0.0",
        on_close: Callable[[Exception | None], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.handshake = handshake
        self.config = bridge_config
        self.logger = logger
        self.client_version = client_version
        self._on_close = on_close
        self._clock = clock or time.monotonic

        self._ws: Any = None
        self._read_task: asyncio.Task[None] | None = None
        self._pairing = PairingTable()
        self._auth_future: asyncio.Future[AuthOkFrame] | None = None
        self._connected = False
        self._closing = False
        self._last_heartbeat: float | None = None
        self._connected_at: float | None = None
        self._late_responses = 0
        self.capabilities: dict[str, Any] = {}
        self.panel_version: str | None = handshake.panel_version
        self.contract_schema_version: int | None = handshake.contract_schema_version
        self.warnings: list[str] = []

    # ---- 属性 ----------------------------------------------------------
    @property
    def uri(self) -> str:
        return self.handshake.uri

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def inflight(self) -> int:
        return self._pairing.inflight

    @property
    def late_responses(self) -> int:
        return self._late_responses

    def heartbeat_age(self) -> float | None:
        if self._last_heartbeat is None:
            return None
        return self._clock() - self._last_heartbeat

    def is_stale(self, *, factor: float = 3.0) -> bool:
        """超过 ``factor`` 个心跳周期没收到心跳即判定连接失效（说明书 §5.4）。"""
        age = self.heartbeat_age()
        if age is None:
            # 刚连上还没收到第一个心跳时不算失效。
            if self._connected_at is None:
                return False
            age = self._clock() - self._connected_at
        return age > self.config.heartbeat_s * factor

    # ---- 连接 ----------------------------------------------------------
    async def connect(self) -> AuthOkFrame:
        if self._connected:
            raise ToolError("BRIDGE_PROTOCOL_ERROR", "连接已经建立", "这是实现错误，请上报")
        connect = _connect_factory()
        max_size = max(1 << 20, int(self.config.max_response_chars) * 4)
        try:
            self._ws = await asyncio.wait_for(
                connect(
                    self.uri,
                    max_size=max_size,
                    open_timeout=self.config.connect_timeout_s,
                    ping_interval=None,
                    close_timeout=2,
                ),
                timeout=self.config.connect_timeout_s + 1,
            )
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                "BRIDGE_DISCONNECTED",
                f"无法连接到宿主面板 {self.uri}",
                "宿主要重启后才会加载新部署的面板；确认面板已加载（host_status）",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        self._connected = True
        self._connected_at = self._clock()
        self._closing = False
        loop = asyncio.get_running_loop()
        self._auth_future = loop.create_future()
        self._read_task = asyncio.create_task(self._read_loop(), name=f"cep-read-{self.handshake.key}")

        auth = AuthFrame(
            token=self.handshake.token,
            client_version=self.client_version,
        )
        try:
            await self._ws.send(encode_frame(auth))
        except Exception as exc:
            await self.close()
            raise ToolError(
                "BRIDGE_DISCONNECTED",
                "认证帧发送失败，连接已断开",
                "重试；若持续失败请 host_reload_panel 重载面板",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        try:
            auth_ok = await asyncio.wait_for(
                asyncio.shield(self._auth_future), timeout=self.config.auth_timeout_s
            )
        except asyncio.TimeoutError:
            await self.close()
            raise ToolError(
                "BRIDGE_AUTH_FAILED",
                f"面板在 {self.config.auth_timeout_s:.0f} 秒内没有回应认证帧",
                "面板可能正在启动或已崩溃：用 host_reload_panel 重载面板后重试",
                detail={"uri": self.uri},
            ) from None
        except ToolError:
            await self.close()
            raise

        self.capabilities = dict(auth_ok.capabilities or {})
        self.panel_version = auth_ok.panel_version or self.panel_version
        self.contract_schema_version = (
            auth_ok.contract_schema_version or self.contract_schema_version
        )
        self.warnings = list(auth_ok.warnings or [])
        if self.logger is not None:
            self.logger.info(
                "bridge_connected",
                host=self.handshake.host,
                pid=self.handshake.pid,
                port=self.handshake.port,
                panel_version=self.panel_version,
                contract_schema_version=self.contract_schema_version,
                capabilities=self.capabilities,
            )
        return auth_ok

    # ---- 请求 ----------------------------------------------------------
    async def request(
        self,
        cmd: str,
        args: dict[str, Any] | None = None,
        *,
        trace_id: str | None = None,
        dry_run: bool = False,
        idempotency_key: str | None = None,
        timeout: float,
        request_id: str | None = None,
    ) -> ResponseFrame:
        if not self._connected or self._ws is None:
            raise ToolError(
                "BRIDGE_DISCONNECTED",
                "与宿主面板的连接未建立",
                "调用 host_status 查看连接状态，必要时 host_launch 重启宿主",
            )
        rid = request_id or make_request_id()
        frame = build_request(
            cmd,
            args,
            trace_id=trace_id,
            dry_run=dry_run,
            idempotency_key=idempotency_key,
            request_id=rid,
        )
        future = self._pairing.register(rid)
        try:
            await self._ws.send(encode_frame(frame))
        except Exception as exc:
            self._pairing.discard(rid)
            raise ToolError(
                "BRIDGE_DISCONNECTED",
                "发送命令失败，连接已断开",
                "调用 host_resync 确认宿主状态后重试",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError:
            # 超时**不等于**命令失败：ExtendScript 没有取消机制，命令可能仍在宿主内执行。
            self._pairing.discard(rid)
            raise ToolError(
                "BRIDGE_TIMEOUT",
                f"命令 {cmd} 在 {timeout:.0f} 秒内没有返回；"
                "命令可能仍在宿主内执行，状态不确定",
                "先调用 host_resync 重新读取宿主真实状态并比对差异，再决定是否重发；"
                "禁止直接重试（说明书 C-4）。若宿主窗口有未响应的模态对话框，先手工关闭它",
                detail={"command": cmd, "request_id": rid, "timeout_s": timeout},
            ) from None

    async def send_config(self, config: dict[str, Any]) -> None:
        if not self._connected or self._ws is None:
            raise ToolError("BRIDGE_DISCONNECTED", "连接未建立，无法下发配置", "先连接面板")
        await self._ws.send(encode_frame(ConfigFrame(config=config)))

    # ---- 读循环 --------------------------------------------------------
    async def _read_loop(self) -> None:
        error: Exception | None = None
        try:
            async for raw in self._ws:
                self._handle_text(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 连接层任何异常都意味着连接失效
            error = exc
        finally:
            self._connected = False
            if not self._closing:
                pending = self._pairing.fail_all(
                    ToolError(
                        "BRIDGE_DISCONNECTED",
                        "连接已断开，在途命令的结果不确定",
                        "调用 host_resync 重新读取宿主真实状态并比对差异",
                    )
                )
                if self.logger is not None:
                    self.logger.warning(
                        "bridge_disconnected",
                        host=self.handshake.host,
                        pid=self.handshake.pid,
                        inflight=pending,
                        error=f"{type(error).__name__}: {error}" if error else None,
                    )
                if self._on_close is not None:
                    self._on_close(error)

    def _handle_text(self, raw: str | bytes) -> None:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        try:
            frame = parse_frame(text)
        except ProtocolError as exc:
            if self.logger is not None:
                self.logger.error("bridge_protocol_error", error=str(exc), frame_excerpt=text[:400])
            return

        if isinstance(frame, ResponseFrame):
            if frame.id is None or not self._pairing.resolve(frame.id, frame):
                self._late_responses += 1
                if self.logger is not None:
                    self.logger.warning(
                        "bridge_late_response",
                        request_id=frame.id,
                        late_total=self._late_responses,
                    )
            return

        if isinstance(frame, AuthOkFrame):
            if self._auth_future is not None and not self._auth_future.done():
                self._auth_future.set_result(frame)
            self._last_heartbeat = self._clock()
            return

        if isinstance(frame, AuthFailedFrame):
            error = ToolError(
                "BRIDGE_AUTH_FAILED",
                f"面板拒绝了认证帧：{frame.reason}",
                "握手令牌每次面板启动都会刷新；等待下一次握手文件更新后重试",
            )
            if self._auth_future is not None and not self._auth_future.done():
                self._auth_future.set_exception(error)
            return

        if isinstance(frame, HeartbeatFrame):
            self._last_heartbeat = self._clock()
            return

        if self.logger is not None:
            self.logger.debug("bridge_frame_ignored", frame_type=frame.type)

    # ---- 关闭 ----------------------------------------------------------
    async def close(self, *, reason: str | None = None) -> None:
        self._closing = True
        self._connected = False
        self._pairing.fail_all(
            ToolError("BRIDGE_DISCONNECTED", "连接已关闭", "重新连接后重试")
        )
        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except (asyncio.CancelledError, Exception):
                pass
            self._read_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # pragma: no cover - 连接可能已经断了
                pass
            self._ws = None
        if self.logger is not None:
            self.logger.info(
                "bridge_closed", host=self.handshake.host, pid=self.handshake.pid, reason=reason
            )

    def snapshot(self) -> dict[str, Any]:
        """连接状态快照（供 host_status 使用，不含令牌）。"""
        return {
            **self.handshake.to_safe_public(),
            "connected": self._connected,
            "inflight": self.inflight,
            "heartbeat_age_s": (
                round(self.heartbeat_age(), 2) if self.heartbeat_age() is not None else None
            ),
            "stale": self.is_stale(),
            "late_responses": self._late_responses,
            "capabilities": dict(self.capabilities),
            "panel_version": self.panel_version,
            "contract_schema_version": self.contract_schema_version,
            "warnings": list(self.warnings) + list(self.handshake_notes()),
        }

    def handshake_notes(self) -> list[str]:
        notes: list[str] = []
        if self.handshake.protocol != PROTOCOL_VERSION:
            notes.append(
                f"面板协议版本 {self.handshake.protocol} 与 L1 的 {PROTOCOL_VERSION} 不一致"
            )
        return notes


class HandshakeIndex:
    """按宿主分组索引握手记录（多版本宿主并存时用得上）。"""

    def __init__(self, handshakes: Iterable[Handshake] = ()) -> None:
        self._items: dict[str, list[Handshake]] = {}
        for handshake in handshakes:
            self.add(handshake)

    def add(self, handshake: Handshake) -> None:
        self._items.setdefault(handshake.host, []).append(handshake)

    def for_host(self, cep_host_id: str) -> list[Handshake]:
        return list(self._items.get(cep_host_id, []))

    def all(self) -> list[Handshake]:
        return [item for items in self._items.values() for item in items]

    def get(self, key: str) -> Handshake | None:
        for handshake in self.all():
            if handshake.key == key:
                return handshake
        return None
