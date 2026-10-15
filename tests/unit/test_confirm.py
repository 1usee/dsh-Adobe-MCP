"""确认令牌的生命周期：签发/过期/参数不匹配/重放（说明书 §8.2）。"""

from __future__ import annotations

import pytest

from dsh_adobe_mcp.safety.confirm import ConfirmError, ConfirmTokenStore, params_hash


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def store() -> ConfirmTokenStore:
    return ConfirmTokenStore(ttl_s=300, clock=FakeClock())


def test_issue_and_validate_roundtrip(store: ConfirmTokenStore):
    operation = store.issue("ppro_timeline_remove_clip", {"ref": "V:1:0"}, {"tool": "x"})
    assert operation.token
    assert len(store) == 1

    validated = store.validate(
        operation.token, tool="ppro_timeline_remove_clip", args={"ref": "V:1:0"}
    )
    assert validated.tool == "ppro_timeline_remove_clip"
    assert validated.signature == params_hash({"ref": "V:1:0"})


def test_parameter_mismatch_is_rejected(store: ConfirmTokenStore):
    operation = store.issue("tool", {"ref": "V:1:0"}, {})
    with pytest.raises(ConfirmError) as excinfo:
        store.validate(operation.token, tool="tool", args={"ref": "V:1:1"})
    assert excinfo.value.code == "CONFIRM_TOKEN_INVALID"
    assert "哈希绑定" in excinfo.value.message


def test_tool_mismatch_is_rejected(store: ConfirmTokenStore):
    operation = store.issue("tool_a", {"x": 1}, {})
    with pytest.raises(ConfirmError) as excinfo:
        store.validate(operation.token, tool="tool_b", args={"x": 1})
    assert excinfo.value.code == "CONFIRM_TOKEN_INVALID"


def test_parameter_order_does_not_matter(store: ConfirmTokenStore):
    operation = store.issue("tool", {"a": 1, "b": 2}, {})
    store.validate(operation.token, tool="tool", args={"b": 2, "a": 1})


def test_expiry(store: ConfirmTokenStore):
    clock: FakeClock = store._clock  # type: ignore[assignment]
    operation = store.issue("tool", {}, {}, ttl_s=10)
    clock.advance(11)
    with pytest.raises(ConfirmError) as excinfo:
        store.validate(operation.token, tool="tool", args={})
    assert excinfo.value.code == "CONFIRM_TOKEN_EXPIRED"
    # 过期令牌会被清理，不再出现在列表中
    assert store.list_pending() == []


def test_single_use_consume(store: ConfirmTokenStore):
    operation = store.issue("tool", {"a": 1}, {})
    consumed = store.consume(operation.token)
    assert consumed.used is True
    assert consumed.used_at is not None
    with pytest.raises(ConfirmError) as excinfo:
        store.consume(operation.token)
    assert excinfo.value.code == "CONFIRM_TOKEN_USED"


def test_unknown_token_is_invalid(store: ConfirmTokenStore):
    with pytest.raises(ConfirmError) as excinfo:
        store.validate("not-a-real-token")
    assert excinfo.value.code == "CONFIRM_TOKEN_INVALID"


def test_tokens_are_unique(store: ConfirmTokenStore):
    tokens = {store.issue("tool", {"i": i}, {}).token for i in range(20)}
    assert len(tokens) == 20


def test_pending_listing_never_leaks_full_token(store: ConfirmTokenStore):
    operation = store.issue("tool", {"a": 1}, {"irreversible": ["x"]})
    pending = store.list_pending()
    assert len(pending) == 1
    item = pending[0]
    assert "token" not in item
    assert item["token_prefix"] == operation.token[:6]
    assert item["impact"]["irreversible"] == ["x"]
    assert item["expires_in_s"] == 300


def test_record_result_keeps_audit_trail(store: ConfirmTokenStore):
    operation = store.issue("tool", {}, {})
    store.consume(operation.token)
    store.record_result(operation.token, {"ok": True})
    assert operation.executed == {"ok": True}
    line = store.audit_line(operation, "confirmed")
    assert line["action"] == "confirmed"
    assert line["token_prefix"] == operation.token[:6]


def test_purge_expired(store: ConfirmTokenStore):
    clock: FakeClock = store._clock  # type: ignore[assignment]
    store.issue("tool", {}, {}, ttl_s=5)
    clock.advance(6)
    assert store.purge_expired() == 1
    assert len(store) == 0
