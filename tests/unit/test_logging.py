"""结构化日志、WAL 与脱敏（说明书 §8.5、§11）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dsh_adobe_mcp.logging_setup import (
    Redactor,
    StructuredLogger,
    current_trace_id,
    new_trace_id,
    set_trace_id,
)


@pytest.fixture
def logger(tmp_path: Path) -> StructuredLogger:
    log = StructuredLogger(
        tmp_path / "logs",
        level="debug",
        console_level="none",
        redactor=Redactor(home=Path.home()),
        clock=lambda: datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc),
    )
    yield log
    log.close()


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_event_written_as_jsonl(tmp_path: Path, logger: StructuredLogger):
    logger.info("tool_call", tool="host_list", elapsed_ms=3)
    files = list((tmp_path / "logs").glob("server-*.jsonl"))
    assert len(files) == 1
    assert files[0].name == "server-2026-10-15.jsonl"
    record = read_lines(files[0])[0]
    assert record["event"] == "tool_call"
    assert record["level"] == "info"
    assert record["tool"] == "host_list"
    assert record["ts"].startswith("2026-10-15T12:00:00")


def test_level_filtering(tmp_path: Path):
    log = StructuredLogger(tmp_path / "logs", level="warning", console_level="none")
    log.info("silent_event")
    log.error("loud_event")
    log.close()
    records = read_lines(next((tmp_path / "logs").glob("server-*.jsonl")))
    assert [record["event"] for record in records] == ["loud_event"]


def test_trace_id_is_attached_and_clearable(tmp_path: Path, logger: StructuredLogger):
    token = set_trace_id("trace-123")
    try:
        logger.info("during")
        assert current_trace_id() == "trace-123"
    finally:
        set_trace_id(None)
    logger.info("after")
    records = read_lines(next((tmp_path / "logs").glob("server-*.jsonl")))
    assert records[0]["trace_id"] == "trace-123"
    assert records[1]["trace_id"] is None


def test_new_trace_id_is_unique():
    assert len({new_trace_id() for _ in range(50)}) == 50


def test_wal_entry_goes_to_daily_file(tmp_path: Path, logger: StructuredLogger):
    logger.wal({"phase": "request", "command": "app.info", "script_text": "DSH.dispatch('x');"})
    logger.wal({"phase": "result", "command": "app.info", "ok": True})
    records = read_lines(next((tmp_path / "logs").glob("wal-*.jsonl")))
    assert [record["phase"] for record in records] == ["request", "result"]
    assert records[0]["kind"] == "wal"
    assert records[0]["script_text"] == "DSH.dispatch('x');"


def test_tokens_are_redacted_everywhere(tmp_path: Path):
    redactor = Redactor(home=Path.home())
    redactor.register("super-secret-token-value")
    log = StructuredLogger(tmp_path / "logs", level="debug", console_level="none", redactor=redactor)
    log.info("bridge_connected", token="super-secret-token-value", nested={"token": "super-secret-token-value"})
    log.wal({"phase": "request", "token": "super-secret-token-value", "header": "Bearer super-secret-token-value"})
    log.close()
    server_text = next((tmp_path / "logs").glob("server-*.jsonl")).read_text(encoding="utf-8")
    wal_text = next((tmp_path / "logs").glob("wal-*.jsonl")).read_text(encoding="utf-8")
    assert "super-secret-token-value" not in server_text
    assert "super-secret-token-value" not in wal_text
    assert "***" in server_text


def test_sensitive_keys_are_scrubbed_even_if_unknown(tmp_path: Path):
    redactor = Redactor(home=Path.home())
    payload = redactor.redact({"token": "abc", "password": "def", "api_key": "ghi", "keep": "ok"})
    assert payload == {"token": "***", "password": "***", "api_key": "***", "keep": "ok"}


def test_user_home_is_masked():
    redactor = Redactor(home=Path(r"C:\Users\tester"))
    assert redactor.redact_text(r"C:\Users\tester\media\a.mp4") == r"%USERPROFILE%\media\a.mp4"
    masked = redactor.redact_text(r"other: C:\Users\someone\docs\b.txt")
    assert "someone" not in masked


def test_short_secrets_are_not_registered():
    redactor = Redactor(home=Path.home())
    redactor.register("abc")
    assert redactor.redact_text("abc") == "abc"  # 太短，注册会被忽略以免误伤正常文本


def test_panel_log_path(tmp_path: Path, logger: StructuredLogger):
    path = logger.panel_log_path("PPRO", 4321)
    assert path.name == "panel-PPRO-4321.log"


def test_log_dir_writability_probe(tmp_path: Path):
    from dsh_adobe_mcp.logging_setup import log_dir_is_writable

    ok, reason = log_dir_is_writable(tmp_path / "fresh")
    assert ok is True and reason is None
