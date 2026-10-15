"""桥接协议：认证帧、请求/响应信封与请求配对（说明书 §5.1、§5.2）。

帧一律是 UTF-8 JSON 文本帧。Python 侧是 WebSocket **客户端**，面板是服务端，
连接建立后的第一帧必须是认证帧。

信封字段（说明书 §5.2）：

    id      双向  请求唯一标识（UUID），响应必须原样回填
    cmd     请求  命令名，命名空间点分
    args    请求  命令参数
    meta    请求  幂等键、追踪号、dry-run 标志
    ok      响应  整体成败
    data    响应  成功时的负载
    error   响应  失败时的 code / message / line / script_excerpt
    host    响应  宿主版本、工程路径、是否有未保存改动
    timing  响应  宿主内耗时、往返耗时
"""

from __future__ import annotations

import asyncio
import json
import uuid
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, Field, ValidationError

__all__ = [
    "PROTOCOL_VERSION",
    "FrameType",
    "AuthFrame",
    "AuthOkFrame",
    "AuthFailedFrame",
    "RequestFrame",
    "ResponseFrame",
    "HeartbeatFrame",
    "ConfigFrame",
    "ProtocolError",
    "parse_frame",
    "encode_frame",
    "build_request",
    "PairingTable",
    "make_request_id",
]


PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 32 * 1024 * 1024


class ProtocolError(Exception):
    """桥接协议违规。``code`` 对应 errors.py 中的错误码。"""

    def __init__(self, message: str, code: str = "BRIDGE_PROTOCOL_ERROR") -> None:
        self.code = code
        super().__init__(message)


class FrameType(str, Enum):
    auth = "auth"
    auth_ok = "auth_ok"
    auth_failed = "auth_failed"
    req = "req"
    res = "res"
    heartbeat = "heartbeat"
    config = "config"
    error = "error"


class _Frame(BaseModel):
    type: str


class AuthFrame(_Frame):
    type: str = FrameType.auth.value
    protocol: int = PROTOCOL_VERSION
    token: str
    client: str = "dsh-adobe-mcp"
    client_version: str = "0.0.0"
    config: dict[str, Any] = Field(default_factory=dict)


class AuthOkFrame(_Frame):
    type: str = FrameType.auth_ok.value
    protocol: int = PROTOCOL_VERSION
    panel_version: str | None = None
    contract_schema_version: int | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AuthFailedFrame(_Frame):
    type: str = FrameType.auth_failed.value
    reason: str = "bad_token"


class RequestFrame(_Frame):
    type: str = FrameType.req.value
    id: str
    cmd: str
    args: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class ResponseFrame(_Frame):
    type: str = FrameType.res.value
    id: str | None = None
    ok: bool = False
    data: Any = None
    error: dict[str, Any] | None = None
    host: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    timing: dict[str, Any] = Field(default_factory=dict)
    #: 面板在响应里额外带回的实现信息（供诊断用，不参与业务判定）
    capability: str | None = None
    qe_enabled: bool | None = None
    replayed: bool | None = None

    def error_code(self) -> str | None:
        if isinstance(self.error, Mapping):
            code = self.error.get("code")
            return str(code) if code else None
        return None

    def error_message(self) -> str | None:
        if isinstance(self.error, Mapping):
            message = self.error.get("message")
            return str(message) if message else None
        return None


class HeartbeatFrame(_Frame):
    type: str = FrameType.heartbeat.value
    ts: float | None = None
    uptime_ms: int | None = None
    inflight: int | None = None


class ConfigFrame(_Frame):
    type: str = FrameType.config.value
    config: dict[str, Any] = Field(default_factory=dict)


_FRAME_MODELS: dict[str, type[_Frame]] = {
    FrameType.auth.value: AuthFrame,
    FrameType.auth_ok.value: AuthOkFrame,
    FrameType.auth_failed.value: AuthFailedFrame,
    FrameType.req.value: RequestFrame,
    FrameType.res.value: ResponseFrame,
    FrameType.heartbeat.value: HeartbeatFrame,
    FrameType.config.value: ConfigFrame,
}


def parse_frame(text: str) -> _Frame:
    """解析一帧文本；结构不合法时抛 :class:`ProtocolError`。"""
    if len(text.encode("utf-8", errors="replace")) > MAX_FRAME_BYTES:
        raise ProtocolError("帧过大，超过 32MB 上限")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"帧不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("帧必须是 JSON 对象")
    frame_type = payload.get("type")
    if not isinstance(frame_type, str):
        raise ProtocolError("帧缺少 type 字段")
    model = _FRAME_MODELS.get(frame_type)
    if model is None:
        if frame_type == FrameType.error.value:
            raise ProtocolError(f"面板报告协议错误：{payload.get('reason')}")
        raise ProtocolError(f"未知帧类型：{frame_type}")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ProtocolError(f"{frame_type} 帧字段不合法：{exc.errors()[:3]}") from exc


def encode_frame(frame: BaseModel | Mapping[str, Any]) -> str:
    if isinstance(frame, BaseModel):
        payload = frame.model_dump(mode="json")
    else:
        payload = dict(frame)
    return json.dumps(payload, ensure_ascii=False, default=str)


def make_request_id() -> str:
    return str(uuid.uuid4())


def build_request(
    cmd: str,
    args: Mapping[str, Any] | None = None,
    *,
    trace_id: str | None = None,
    dry_run: bool = False,
    idempotency_key: str | None = None,
    request_id: str | None = None,
    extra_meta: Mapping[str, Any] | None = None,
) -> RequestFrame:
    meta: dict[str, Any] = {
        "traceId": trace_id,
        "dryRun": bool(dry_run),
        "idempotencyKey": idempotency_key,
        "client": "dsh-adobe-mcp",
    }
    if extra_meta:
        meta.update(extra_meta)
    return RequestFrame(
        id=request_id or make_request_id(),
        cmd=cmd,
        args=dict(args or {}),
        meta=meta,
    )


class PairingTable:
    """请求 id → 等待中的 Future（说明书 §5.2 的配对）。

    连接失效或求值超时后，``fail_all`` 会把全部在途请求置为确定失败——
    说明书 §5.4 要求此时"在途命令标记为不确定状态"，不能悄悄丢掉。
    """

    def __init__(self) -> None:
        self._waiting: dict[str, asyncio.Future[ResponseFrame]] = {}

    def register(self, request_id: str) -> asyncio.Future[ResponseFrame]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ResponseFrame] = loop.create_future()
        self._waiting[request_id] = future
        return future

    def resolve(self, request_id: str, response: ResponseFrame) -> bool:
        future = self._waiting.pop(request_id, None)
        if future is None or future.done():
            return False
        future.set_result(response)
        return True

    def discard(self, request_id: str) -> None:
        self._waiting.pop(request_id, None)

    def fail_all(self, error: Exception) -> int:
        count = 0
        for request_id, future in list(self._waiting.items()):
            self._waiting.pop(request_id, None)
            if not future.done():
                future.set_exception(error)
                count += 1
        return count

    def pending_ids(self) -> list[str]:
        return list(self._waiting)

    @property
    def inflight(self) -> int:
        return len(self._waiting)
