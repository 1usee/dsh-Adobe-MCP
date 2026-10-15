"""结构化日志、WAL 与追踪号（说明书 §8.5、§11）。

三类输出：

* ``logs/server-YYYY-MM-DD.jsonl`` —— L1/L2 结构化事件，含追踪号；
* ``logs/wal-YYYY-MM-DD.jsonl`` —— 每条发给宿主的命令，**发送之前**先落盘，
  字段见说明书 §8.5；其中 ``script_text`` 是本次调用对应的 ExtendScript 原文，
  这是排查"AI 到底对工程做了什么"最有价值的一条记录；
* ``logs/panel-<宿主>-<进程号>.log`` —— 面板侧日志的落点（由面板自己写入）。

**脱敏**：握手令牌与用户目录中的用户名在写入前脱敏（说明书 §11）。令牌另有硬性要求
"日志中不得记录令牌明文"，因此 :class:`Redactor` 会把它已经知道的所有令牌替换掉，
并且任何键名含 token/secret/password 的字段一律替换为 ``***``。
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, TextIO

__all__ = [
    "Redactor",
    "StructuredLogger",
    "current_trace_id",
    "set_trace_id",
    "new_trace_id",
    "panel_log_path",
]

_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dsh_adobe_mcp_trace_id", default=None
)

_SENSITIVE_KEY = re.compile(r"(token|secret|password|passwd|api[_-]?key)", re.IGNORECASE)
_WINDOWS_PROFILE = re.compile(r"([A-Za-z]:\\Users\\)([^\\]+)", re.IGNORECASE)
_POSIX_HOME = re.compile(r"(/home/)([^/]+)")


def new_trace_id() -> str:
    """生成一个短追踪号（贯穿 DSH 调用 → L1 → L2 → 面板 → ExtendScript）。"""
    import uuid

    return uuid.uuid4().hex[:16]


def current_trace_id() -> str | None:
    return _TRACE_ID.get()


def set_trace_id(trace_id: str | None) -> contextvars.Token[str | None]:
    return _TRACE_ID.set(trace_id)


class Redactor:
    """日志脱敏器。"""

    def __init__(self, home: Path | None = None, extra_secrets: Iterable[str] = ()) -> None:
        self._home = Path(home) if home else Path.home()
        self._secrets: list[str] = []
        self._lock = threading.Lock()
        for secret in extra_secrets:
            self.register(secret)

    def register(self, secret: str | None) -> None:
        """登记一个需要脱敏的明文（例如握手令牌）。"""
        if not secret or len(str(secret)) < 8:
            return
        text = str(secret)
        with self._lock:
            if text not in self._secrets:
                # 长串先替换，避免短串是长串子串时留下残片。
                self._secrets.append(text)
                self._secrets.sort(key=len, reverse=True)

    def redact_text(self, text: str) -> str:
        out = text
        with self._lock:
            for secret in self._secrets:
                out = out.replace(secret, "***")
        out = out.replace(str(self._home), "%USERPROFILE%")
        out = _WINDOWS_PROFILE.sub(r"\1<user>", out)
        out = _POSIX_HOME.sub(r"\1<user>", out)
        return out

    def redact(self, value: Any, _depth: int = 0) -> Any:
        """递归脱敏：字符串走文本脱敏，敏感键名整体替换。"""
        if _depth > 12:
            return "***"
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            out: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if _SENSITIVE_KEY.search(key_text):
                    out[key_text] = "***"
                else:
                    out[key_text] = self.redact(item, _depth + 1)
            return out
        if isinstance(value, (list, tuple)):
            return [self.redact(item, _depth + 1) for item in value]
        if isinstance(value, Path):
            return self.redact_text(str(value))
        return value


class StructuredLogger:
    """按天分文件的 JSONL 日志器 + WAL 写入器。

    刻意不用 stdlib ``logging`` 的 handler 体系：本项目需要的是"每条记录一个 JSON 对象、
    按天分文件、字段名可控"，直接写入更简单也更好断言（测试里可以直接读文件校验）。
    """

    def __init__(
        self,
        log_dir: Path,
        *,
        level: str = "info",
        console_level: str = "none",
        redactor: Redactor | None = None,
        stream: TextIO | None = None,
        clock: Any = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.level = level
        self.console_level = console_level
        self.redactor = redactor or Redactor()
        self._stream = stream
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._file: TextIO | None = None
        self._current_day: str | None = None
        self._levels = {"debug": 10, "info": 20, "warning": 30, "error": 40}
        self._console_threshold = (
            100 if console_level == "none" else self._levels.get(console_level, 20)
        )

    # ---- 基础 ----------------------------------------------------------
    @property
    def threshold(self) -> int:
        return self._levels.get(self.level, 20)

    def _timestamp(self) -> tuple[str, str]:
        now = self._clock()
        if isinstance(now, datetime):
            return now.isoformat(timespec="milliseconds"), now.strftime("%Y-%m-%d")
        return str(now), str(now)[:10]

    def _handle(self, day: str) -> TextIO:
        if self._file is not None and self._current_day == day:
            return self._file
        if self._file is not None:
            self._file.close()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"server-{day}.jsonl"
        self._file = path.open("a", encoding="utf-8")
        self._current_day = day
        return self._file

    def _write(self, record: dict[str, Any]) -> None:
        text = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            handle = self._handle(record["day"])
            handle.write(text + "\n")
            handle.flush()

    def log(self, level: str, event: str, **fields: Any) -> dict[str, Any]:
        numeric = self._levels.get(level, 20)
        timestamp, day = self._timestamp()
        record: dict[str, Any] = {
            "ts": timestamp,
            "day": day,
            "level": level,
            "event": event,
            "trace_id": current_trace_id(),
        }
        record.update(self.redactor.redact(fields))
        if numeric >= self.threshold:
            self._write(record)
        if numeric >= self._console_threshold:
            payload = {k: v for k, v in record.items() if k != "day"}
            target = self._stream
            if target is None:
                import sys

                target = sys.stderr
            try:
                target.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                target.flush()
            except Exception:  # pragma: no cover - 控制台不可写不应影响主流程
                pass
        return record

    def debug(self, event: str, **fields: Any) -> dict[str, Any]:
        return self.log("debug", event, **fields)

    def info(self, event: str, **fields: Any) -> dict[str, Any]:
        return self.log("info", event, **fields)

    def warning(self, event: str, **fields: Any) -> dict[str, Any]:
        return self.log("warning", event, **fields)

    def error(self, event: str, **fields: Any) -> dict[str, Any]:
        return self.log("error", event, **fields)

    # ---- WAL（说明书 §8.5） -------------------------------------------
    def wal(self, entry: Mapping[str, Any]) -> None:
        """追加一条预写日志。**必须在命令发给宿主之前调用。**"""
        timestamp, day = self._timestamp()
        record: dict[str, Any] = {"ts": timestamp, "kind": "wal"}
        record.update(self.redactor.redact(dict(entry)))
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.log_dir / f"wal-{day}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def panel_log_path(self, host: str, pid: int) -> Path:
        return self.log_dir / f"panel-{host}-{pid}.log"

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None
                self._current_day = None


def panel_log_path(log_dir: Path, host: str, pid: int) -> Path:
    return Path(log_dir) / f"panel-{host}-{pid}.log"


def read_last_lines(path: Path, limit: int = 20) -> list[str]:
    """读取文件尾部若干行（用于诊断信息，不引入额外依赖）。"""
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    return [line.rstrip("\n") for line in lines[-limit:]]


def log_dir_is_writable(log_dir: Path) -> tuple[bool, str | None]:
    """探测日志目录可写性；不可写时返回原因（避免日志失败把主流程带崩）。"""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        probe = log_dir / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True, None
    except OSError as exc:  # pragma: no cover - 依赖环境
        return False, f"{type(exc).__name__}: {exc}"
