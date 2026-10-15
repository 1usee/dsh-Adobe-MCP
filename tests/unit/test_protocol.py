"""桥接协议：帧解析、请求配对（说明书 §5.2）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from dsh_adobe_mcp.bridge.protocol import (
    AuthFailedFrame,
    AuthFrame,
    AuthOkFrame,
    HeartbeatFrame,
    PairingTable,
    ProtocolError,
    RequestFrame,
    ResponseFrame,
    build_request,
    encode_frame,
    make_request_id,
    parse_frame,
)


def test_request_frame_roundtrip():
    frame = build_request(
        "timeline.insert_clip",
        {"item": "\\Root\\a.mp4", "at_s": 1.5},
        trace_id="t-1",
        dry_run=True,
        idempotency_key="key-1",
    )
    text = encode_frame(frame)
    parsed = parse_frame(text)
    assert isinstance(parsed, RequestFrame)
    assert parsed.cmd == "timeline.insert_clip"
    assert parsed.args["at_s"] == 1.5
    assert parsed.meta["traceId"] == "t-1"
    assert parsed.meta["dryRun"] is True
    assert parsed.meta["idempotencyKey"] == "key-1"


def test_response_frame_fields():
    frame = parse_frame(
        json.dumps(
            {
                "type": "res",
                "id": "abc",
                "ok": False,
                "error": {"code": "ITEM_NOT_FOUND", "message": "x", "remedy": "y"},
                "host": {"project_path": "D:\\p.prproj", "dirty": True},
                "timing": {"host_ms": 12, "rtt_ms": 15},
                "warnings": ["w"],
            }
        )
    )
    assert isinstance(frame, ResponseFrame)
    assert frame.ok is False
    assert frame.error_code() == "ITEM_NOT_FOUND"
    assert frame.error_message() == "x"
    assert frame.host["dirty"] is True


def test_auth_frames():
    auth = parse_frame(encode_frame(AuthFrame(token="t", client_version="1.1.0")))
    assert isinstance(auth, AuthFrame) and auth.protocol == 1
    ok = parse_frame(json.dumps({"type": "auth_ok", "capabilities": {"qe_enabled": False}}))
    assert isinstance(ok, AuthOkFrame) and ok.capabilities["qe_enabled"] is False
    failed = parse_frame(json.dumps({"type": "auth_failed", "reason": "bad_token"}))
    assert isinstance(failed, AuthFailedFrame) and failed.reason == "bad_token"


def test_heartbeat_frame_tolerates_missing_optional_fields():
    frame = parse_frame(json.dumps({"type": "heartbeat"}))
    assert isinstance(frame, HeartbeatFrame)
    assert frame.ts is None


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        json.dumps([1, 2, 3]),
        json.dumps({"no_type": True}),
        json.dumps({"type": "unknown_frame"}),
        json.dumps({"type": "req"}),  # 缺 id / cmd
        json.dumps({"type": "res", "ok": "not-a-bool"}),
    ],
)
def test_invalid_frames_raise_protocol_error(payload: str):
    with pytest.raises(ProtocolError):
        parse_frame(payload)


def test_oversized_frame_is_rejected():
    huge = json.dumps({"type": "res", "data": "x" * (33 * 1024 * 1024)})
    with pytest.raises(ProtocolError) as excinfo:
        parse_frame(huge)
    assert "过大" in str(excinfo.value)


async def test_pairing_resolves_matching_request():
    table = PairingTable()
    future = table.register("id-1")
    assert table.inflight == 1
    frame = ResponseFrame(id="id-1", ok=True, data={"x": 1})
    assert table.resolve("id-1", frame) is True
    assert (await future).data == {"x": 1}
    assert table.inflight == 0


async def test_pairing_ignores_unknown_and_duplicate_responses():
    table = PairingTable()
    table.register("id-1")
    assert table.resolve("id-2", ResponseFrame(id="id-2")) is False
    assert table.resolve("id-1", ResponseFrame(id="id-1", ok=True)) is True
    assert table.resolve("id-1", ResponseFrame(id="id-1", ok=True)) is False


async def test_fail_all_settles_every_pending_future():
    table = PairingTable()
    first = table.register("a")
    second = table.register("b")
    count = table.fail_all(RuntimeError("连接断开"))
    assert count == 2
    assert table.inflight == 0
    for future in (first, second):
        with pytest.raises(RuntimeError):
            await future


async def test_discard_removes_pending():
    table = PairingTable()
    table.register("a")
    table.discard("a")
    assert table.inflight == 0
    assert table.pending_ids() == []


def test_request_ids_are_unique():
    assert len({make_request_id() for _ in range(100)}) == 100


def test_encode_frame_handles_non_ascii():
    text = encode_frame({"type": "res", "id": "1", "data": {"name": "中文素材.mp4"}})
    assert "中文素材" in text
    assert parse_frame(text).data["name"] == "中文素材.mp4"


def test_build_request_default_meta():
    frame = build_request("app.info")
    assert frame.meta["client"] == "dsh-adobe-mcp"
    assert frame.meta["dryRun"] is False
    assert frame.args == {}


def test_pairing_requires_running_loop():
    """同步上下文里没有事件循环，register 必须立刻报错而不是挂住。"""
    table = PairingTable()
    with pytest.raises(RuntimeError):
        table.register("no-loop")
