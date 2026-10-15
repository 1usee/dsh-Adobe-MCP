"""统一返回信封（说明书 §7.1）。

所有工具都返回同一个结构：

    {
      "ok": bool,
      "data": {...} | None,
      "error": {"code", "message", "remedy", "detail"?} | None,
      "warnings": [...],
      "host_state": {"host", "version", "project_path", "dirty", "connected", "qe_enabled"},
      "undo_hint": "..." | None
    }

`remedy` 与 `undo_hint` 是本设计的关键可用性设计（说明书 §7.1）：失败时告诉调用者
"下一步怎么办"，写操作时告诉调用者"怎么撤销"。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .errors import make_error

__all__ = ["success", "failure", "host_state", "merge_warnings", "is_ok", "error_code"]


def _clean_warnings(warnings: Iterable[str] | None) -> list[str]:
    if not warnings:
        return []
    seen: list[str] = []
    for item in warnings:
        text = str(item)
        if text not in seen:
            seen.append(text)
    return seen


def host_state(
    *,
    host: str | None = None,
    version: str | None = None,
    project_path: str | None = None,
    dirty: bool | None = None,
    connected: bool | None = None,
    qe_enabled: bool | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造信封里的 ``host_state`` 字段。只放入已知信息，不用猜测填充。"""
    state: dict[str, Any] = {
        "host": host,
        "version": version,
        "project_path": project_path,
        "dirty": dirty,
        "connected": connected,
        "qe_enabled": qe_enabled,
    }
    if extra:
        state.update(extra)
    return state


def success(
    data: Any = None,
    *,
    warnings: Iterable[str] | None = None,
    state: Mapping[str, Any] | None = None,
    undo_hint: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """成功返回。``extra`` 中的键会并入顶层（例如 job_id）。"""
    result: dict[str, Any] = {
        "ok": True,
        "data": data,
        "error": None,
        "warnings": _clean_warnings(warnings),
        "host_state": dict(state) if state else host_state(),
        "undo_hint": undo_hint,
    }
    result.update(extra)
    return result


def failure(
    code: str,
    *,
    message: str | None = None,
    remedy: str | None = None,
    detail: Any = None,
    data: Any = None,
    warnings: Iterable[str] | None = None,
    state: Mapping[str, Any] | None = None,
    undo_hint: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """失败返回。``data`` 用于承载"虽然失败但调用者需要的东西"（如待确认的影响说明）。"""
    result: dict[str, Any] = {
        "ok": False,
        "data": data,
        "error": make_error(code, message, remedy, detail=detail),
        "warnings": _clean_warnings(warnings),
        "host_state": dict(state) if state else host_state(),
        "undo_hint": undo_hint,
    }
    result.update(extra)
    return result


def merge_warnings(result: dict[str, Any], warnings: Iterable[str] | None) -> dict[str, Any]:
    """把新的 warning 并入既有结果（去重、保序）。"""
    merged = _clean_warnings(list(result.get("warnings") or []) + list(warnings or []))
    result["warnings"] = merged
    return result


def is_ok(result: Mapping[str, Any]) -> bool:
    return bool(result.get("ok"))


def error_code(result: Mapping[str, Any]) -> str | None:
    error = result.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        return str(code) if code else None
    return None
