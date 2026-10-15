"""错误码、remedy 与统一返回信封（说明书 §7.1、§12）。"""

from __future__ import annotations

import pytest

from dsh_adobe_mcp.errors import (
    ERRORS,
    ToolError,
    code_of,
    describe_exception,
    error_codes,
    make_error,
    spec,
)
from dsh_adobe_mcp.result import error_code, failure, host_state, is_ok, success

SPEC_CODES = {
    "HOST_NOT_INSTALLED",
    "HOST_NOT_RUNNING",
    "PANEL_NOT_LOADED",
    "BRIDGE_TIMEOUT",
    "BRIDGE_DESYNC",
    "CONFIRM_REQUIRED",
    "CONFIRM_TOKEN_INVALID",
    "PATH_NOT_ALLOWED",
    "SCRIPT_ERROR",
    "HOST_CRASHED",
}


def test_every_code_has_message_and_actionable_remedy():
    for code, found in ERRORS.items():
        assert found.message, code
        assert found.remedy, code
        # remedy 必须"可执行"：太短的多半是"请重试"这类空话
        assert len(found.remedy) >= 12, f"{code} 的 remedy 太短：{found.remedy}"
        assert found.remedy != found.message


def test_spec_codes_are_present():
    assert SPEC_CODES <= set(ERRORS)


def test_error_codes_are_stable_and_sorted():
    codes = error_codes()
    assert codes == sorted(codes)
    assert len(codes) == len(set(codes))


def test_make_error_shape_and_overrides():
    payload = make_error("PATH_NOT_ALLOWED", "路径 X 越界", detail={"raw": "X"})
    assert payload["code"] == "PATH_NOT_ALLOWED"
    assert payload["message"] == "路径 X 越界"
    assert payload["remedy"]
    assert payload["detail"] == {"raw": "X"}


def test_make_error_marks_retryable_codes():
    assert make_error("HOST_NOT_RUNNING").get("retryable") is True
    assert "retryable" not in make_error("PATH_NOT_ALLOWED")


def test_unknown_code_falls_back_without_losing_original_code():
    payload = make_error("SOMETHING_NEW")
    assert payload["code"] == "SOMETHING_NEW"
    assert "未登记" in payload["message"]
    assert payload["remedy"]


def test_tool_error_carries_code_message_remedy():
    error = ToolError("BRIDGE_DESYNC")
    assert error.code == "BRIDGE_DESYNC"
    assert error.remedy == spec("BRIDGE_DESYNC").remedy
    assert error.payload()["code"] == "BRIDGE_DESYNC"
    assert "BRIDGE_DESYNC" in str(error)


def test_describe_exception_for_foreign_exceptions():
    from dsh_adobe_mcp.safety.checkpoint import CheckpointError
    from dsh_adobe_mcp.safety.confirm import ConfirmError

    described = describe_exception(CheckpointError("CHECKPOINT_FAILED", "boom", "fix it"))
    assert described["code"] == "CHECKPOINT_FAILED"
    assert described["message"] == "boom"
    assert described["remedy"] == "fix it"

    unknown = describe_exception(RuntimeError("boom"))
    assert unknown["code"] == "INTERNAL_ERROR"
    assert "RuntimeError" in unknown["detail"]

    assert code_of(ConfirmError("CONFIRM_TOKEN_EXPIRED", "expired", "reissue")) == (
        "CONFIRM_TOKEN_EXPIRED"
    )


def test_success_envelope_shape():
    result = success({"a": 1}, warnings=["w1", "w1", "w2"], state=host_state(host="premiere"))
    assert is_ok(result)
    assert result["error"] is None
    assert result["data"] == {"a": 1}
    assert result["warnings"] == ["w1", "w2"]  # 去重且保序
    assert result["host_state"]["host"] == "premiere"
    assert result["undo_hint"] is None


def test_failure_envelope_shape_and_extras():
    result = failure(
        "CONFIRM_REQUIRED",
        data={"confirm_token": "t"},
        state=host_state(connected=False),
    )
    assert is_ok(result) is False
    assert error_code(result) == "CONFIRM_REQUIRED"
    assert result["data"] == {"confirm_token": "t"}
    assert result["error"]["remedy"]
    assert result["host_state"]["connected"] is False


def test_host_state_keeps_unknown_as_none_not_guess():
    state = host_state()
    assert state["project_path"] is None
    assert state["dirty"] is None
    assert state["connected"] is None
