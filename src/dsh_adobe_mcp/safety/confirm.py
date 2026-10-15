"""T2 确认令牌（说明书 §8.2）。

流程：

1. 客户端调用 T2 工具 → L1 **不执行任何宿主操作**；
2. L1 计算影响说明、生成**一次性**确认令牌（带过期时间，与操作参数哈希绑定），
   返回 ``dry_run`` 形式的预期结果结构；
3. 客户端调用 ``safety_confirm_operation`` 携带令牌；
4. L1 校验令牌（存在、未过期、参数哈希匹配、未被使用）→ 执行检查点 → 执行操作 → 消费令牌；
5. 令牌一次性消费，重放无效。

**参数哈希绑定是关键**：防止确认了"A 操作"却执行了"B 操作"。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

__all__ = ["PendingOperation", "ConfirmError", "ConfirmTokenStore", "params_hash"]


def params_hash(params: Mapping[str, Any]) -> str:
    """对操作参数做稳定哈希（键排序、str 兜底），用于把令牌绑定到具体参数。"""
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


@dataclass
class PendingOperation:
    """一次等待确认的 T2 操作。"""

    token: str
    tool: str
    args: dict[str, Any]
    impact: dict[str, Any]
    created_at: float
    expires_at: float
    host_value: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    used: bool = False
    used_at: float | None = None
    executed: dict[str, Any] | None = None

    @property
    def signature(self) -> str:
        return params_hash(self.args)

    def to_public(self, *, now: float | None = None) -> dict[str, Any]:
        """对外暴露的令牌信息（**不含**参数快照里的敏感内容之外的东西，且不回传哈希）。"""
        now = time.time() if now is None else now
        return {
            "tool": self.tool,
            "impact": self.impact,
            "expires_at": self.expires_at,
            "expires_in_s": max(0, int(self.expires_at - now)),
            "parameters": self.args,
            "used": self.used,
        }


class ConfirmError(Exception):
    """令牌校验失败。``code`` 对应 errors.py 中的错误码。"""

    def __init__(self, code: str, message: str, remedy: str) -> None:
        self.code = code
        self.message = message
        self.remedy = remedy
        super().__init__(message)


class ConfirmTokenStore:
    """确认令牌的签发、校验与消费。

    :param ttl_s: 令牌有效期（秒），来自 ``safety.confirm_token_ttl_s``。
    :param clock: 可注入的时钟，便于测试过期与重放。
    """

    def __init__(self, ttl_s: float = 300, *, clock: Callable[[], float] | None = None) -> None:
        self.ttl_s = float(ttl_s)
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._pending: dict[str, PendingOperation] = {}
        self._history: list[dict[str, Any]] = []

    # ---- 签发 ----------------------------------------------------------
    def issue(
        self,
        tool: str,
        args: Mapping[str, Any],
        impact: Mapping[str, Any],
        *,
        host_value: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        ttl_s: float | None = None,
    ) -> PendingOperation:
        now = self._clock()
        ttl = self.ttl_s if ttl_s is None else float(ttl_s)
        operation = PendingOperation(
            token=secrets.token_urlsafe(24),
            tool=tool,
            args=dict(args),
            impact=dict(impact),
            created_at=now,
            expires_at=now + ttl,
            host_value=dict(host_value or {}),
            trace_id=trace_id,
        )
        with self._lock:
            self._purge_locked(now)
            self._pending[operation.token] = operation
        return operation

    # ---- 校验与消费 ----------------------------------------------------
    def peek(self, token: str) -> PendingOperation:
        """校验令牌但**不消费**（用于执行前的参数一致性检查）。"""
        now = self._clock()
        with self._lock:
            operation = self._pending.get(token)
            if operation is None:
                self._purge_locked(now)
                raise ConfirmError(
                    "CONFIRM_TOKEN_INVALID",
                    "确认令牌不存在或已被消费",
                    "重新发起该 T2 工具调用以获得新令牌",
                )
            if operation.used:
                raise ConfirmError(
                    "CONFIRM_TOKEN_USED", "确认令牌已被使用", "重新发起该 T2 工具调用获得新令牌"
                )
            if now > operation.expires_at:
                # 先给出"已过期"这个精确原因，再清理——否则调用者只会看到"令牌不存在"，
                # 无法区分"抄错了令牌"与"确认得太慢"。
                self._purge_locked(now)
                raise ConfirmError(
                    "CONFIRM_TOKEN_EXPIRED",
                    f"确认令牌已于 {int(now - operation.expires_at)} 秒前过期",
                    "重新发起该 T2 工具调用获得新令牌",
                )
            self._purge_locked(now)
            return operation

    def validate(
        self,
        token: str,
        *,
        tool: str | None = None,
        args: Mapping[str, Any] | None = None,
    ) -> PendingOperation:
        """完整校验：存在、未使用、未过期、工具名与参数哈希匹配。"""
        operation = self.peek(token)
        if tool is not None and operation.tool != tool:
            raise ConfirmError(
                "CONFIRM_TOKEN_INVALID",
                f"令牌绑定的是 {operation.tool}，但本次要执行 {tool}",
                "令牌与参数哈希绑定；请在原工具调用上确认，或用 safety_confirm_operation 直接确认",
            )
        if args is not None and params_hash(args) != operation.signature:
            raise ConfirmError(
                "CONFIRM_TOKEN_INVALID",
                "令牌绑定的参数与本次参数不一致（令牌与操作参数哈希绑定）",
                "请用与签发令牌时完全相同的一组参数重新确认",
            )
        return operation

    def consume(self, token: str) -> PendingOperation:
        """消费令牌（一次性）。校验失败时抛 :class:`ConfirmError`。"""
        operation = self.peek(token)
        with self._lock:
            current = self._pending.get(token)
            if current is None or current.used:
                raise ConfirmError(
                    "CONFIRM_TOKEN_USED", "确认令牌已被使用", "重新发起该 T2 工具调用获得新令牌"
                )
            current.used = True
            current.used_at = self._clock()
            self._history.append(
                {
                    "token_id": token[:6],
                    "tool": current.tool,
                    "used_at": current.used_at,
                    "trace_id": current.trace_id,
                }
            )
            return current

    def record_result(self, token: str, result: Mapping[str, Any]) -> None:
        with self._lock:
            operation = self._pending.get(token)
            if operation is not None:
                operation.executed = dict(result)

    # ---- 查询与清理 ----------------------------------------------------
    def list_pending(self) -> list[dict[str, Any]]:
        """列出等待确认的操作（**不回传令牌本体**，只给前 6 位便于日志对照）。"""
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            items: list[dict[str, Any]] = []
            for op in self._pending.values():
                public = op.to_public(now=now)
                public["token_prefix"] = op.token[:6]
                items.append(public)
            return items

    def purge_expired(self) -> int:
        now = self._clock()
        with self._lock:
            return self._purge_locked(now)

    def _purge_locked(self, now: float) -> int:
        expired = [token for token, op in self._pending.items() if now > op.expires_at]
        for token in expired:
            self._pending.pop(token, None)
        return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)

    # ---- 落盘（便于审计；令牌本体不落盘，只记录前 6 位） ----------------
    def audit_line(self, operation: PendingOperation, action: str) -> dict[str, Any]:
        return {
            "action": action,
            "token_prefix": operation.token[:6],
            "tool": operation.tool,
            "args": operation.args,
            "impact": operation.impact,
            "created_at": operation.created_at,
            "expires_at": operation.expires_at,
            "trace_id": operation.trace_id,
        }
