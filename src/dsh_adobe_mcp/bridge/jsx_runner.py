"""求值器：串行化、超时、DESYNC 与幂等键（说明书 §5.3、§8.5）。

这是全设计最容易出错的部分，规则必须严格执行：

1. **串行化（C-2）**：每个宿主维护一把求值锁，同一时刻最多一个命令在途，发送前获取锁；
2. **超时语义**：超时**不等于**命令失败——ExtendScript 没有取消机制，超时后命令可能仍在执行；
3. **超时后的处置**：该桥接连接被标记为 ``DESYNC``，所有后续命令立刻失败并提示先 ``host_resync``；
4. **非幂等命令**：带幂等键的命令在宿主侧记录已执行的键，重放同键返回上次结果而不重复执行；
5. **WAL**：每条命令在发送**之前**先落盘，记录里带本次调用的完整脚本原文。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..errors import ToolError
from ..logging_setup import current_trace_id, new_trace_id
from ..contract import CommandSpec
from .cep_client import CepClient
from .protocol import ResponseFrame

__all__ = ["BridgeOutcome", "ScriptSourceIndex", "JsxRunner"]


@dataclass
class BridgeOutcome:
    """一次宿主求值的结果。"""

    ok: bool
    command: str
    data: Any = None
    error: dict[str, Any] | None = None
    host: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    timing: dict[str, Any] = field(default_factory=dict)
    response: ResponseFrame | None = None
    replayed: bool = False
    host_key: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "data": self.data,
            "error": self.error,
            "host": self.host,
            "warnings": list(self.warnings),
            "timing": dict(self.timing),
            "replayed": self.replayed,
        }


_FUNCTION_START = "function {name}("


class ScriptSourceIndex:
    """**本地**的 JSX 源码索引。

    用途有两个，都是为了"脚本原文比命令名有价值得多"（说明书 §8.5）：

    * WAL 里记录本次调用对应的脚本原文（dispatch 调用 + 处理器实现）；
    * 面板没给出 ``script_excerpt`` 时，用本地源码补出上下文。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.ops_dir = self.root / "jsx" / "ops"
        self._cache: dict[str, str] = {}

    def _read(self, path: Path) -> str:
        key = str(path)
        if key not in self._cache:
            try:
                self._cache[key] = path.read_text(encoding="utf-8")
            except OSError:
                self._cache[key] = ""
        return self._cache[key]

    def handler_source(self, ops_file: str | None, handler: str) -> tuple[str, str] | None:
        """返回 ``(相对路径, 函数源码)``；找不到返回 None。"""
        if not ops_file:
            return None
        path = self.ops_dir / ops_file
        text = self._read(path)
        if not text:
            return None
        marker = _FUNCTION_START.format(name=handler)
        start = text.find(marker)
        if start < 0:
            return None
        # 用大括号配平找出函数体结束位置（JSX 里函数体不可能出现字符串里的裸大括号错配问题
        # 到不可接受的程度；这里只是给 WAL 做源码摘录，取不到就退化为第一段文本）。
        depth = 0
        index = text.find("{", start)
        if index < 0:
            return (str(path.relative_to(self.root)), text[start : start + 4000])
        end = index
        while end < len(text):
            char = text[end]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end += 1
                    break
            end += 1
        body = text[start:end]
        return (str(path.relative_to(self.root)).replace("\\", "/"), body)

    def excerpt(self, file_name: str, line: int, context: int = 5) -> str | None:
        """取某文件第 line 行前后若干行的源码。"""
        candidate = Path(file_name)
        if not candidate.is_absolute():
            candidate = self.root / file_name
        path = candidate if candidate.is_file() else self.ops_dir / Path(file_name).name
        text = self._read(path)
        if not text:
            return None
        lines = text.splitlines()
        if not lines:
            return None
        index = max(1, min(line, len(lines)))
        start = max(1, index - context)
        end = min(len(lines), index + context)
        return "\n".join(f"{number}: {lines[number - 1]}" for number in range(start, end + 1))

    def render_invocation(
        self,
        command: CommandSpec,
        args: Mapping[str, Any],
        meta: Mapping[str, Any],
        request_id: str,
    ) -> str:
        """渲染本次调用对应的脚本原文（用于 WAL）。"""
        payload = json.dumps(
            {"id": request_id, "cmd": command.name, "args": dict(args), "meta": dict(meta)},
            ensure_ascii=False,
            default=str,
        )
        literal = payload.replace("\\", "\\\\").replace("'", "\\'")
        lines = [
            f"// command : {command.name}",
            f"// tool    : {command.tool or '(internal)'}",
            f"// risk    : {command.risk}   capability: {command.capability}",
            f"// handler : {command.ops_file or '(node side)'} :: {command.handler}",
            f"// dry_run : {bool(meta.get('dryRun'))}   idempotent: {command.idempotent}",
            f"DSH.dispatch('{literal}');",
        ]
        handler = self.handler_source(command.ops_file, command.handler)
        if handler is not None:
            relative, source = handler
            lines.append("")
            lines.append(f"// ---- {relative} ----")
            lines.append(source)
        return "\n".join(lines)


class JsxRunner:
    """单个宿主的求值通道。"""

    def __init__(
        self,
        *,
        client: CepClient,
        eval_timeout_s: float = 30.0,
        logger: Any = None,
        source_index: ScriptSourceIndex | None = None,
        host_key: str = "premiere",
        clock: Any = None,
    ) -> None:
        self.client = client
        self.eval_timeout_s = float(eval_timeout_s)
        self.logger = logger
        self.source_index = source_index
        self.host_key = host_key
        self._clock = clock or time.time

        self._lock = asyncio.Lock()
        self._desynced = False
        self._desync_reason: str | None = None
        self._desync_at: float | None = None
        self._idempotent_results: dict[str, BridgeOutcome] = {}
        self._waited_total_ms = 0.0

    # ---- 状态 ----------------------------------------------------------
    @property
    def desynced(self) -> bool:
        return self._desynced

    @property
    def desync_reason(self) -> str | None:
        return self._desync_reason

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def waited_total_ms(self) -> float:
        return round(self._waited_total_ms, 2)

    def mark_desync(self, reason: str) -> None:
        """标记 DESYNC：宿主状态未知，后续命令一律拒绝，直到显式 resync。"""
        self._desynced = True
        self._desync_reason = reason
        self._desync_at = self._clock()
        if self.logger is not None:
            self.logger.warning("bridge_desync", host=self.host_key, reason=reason)

    def clear_desync(self, reason: str = "host_resync") -> None:
        if self._desynced and self.logger is not None:
            self.logger.info("bridge_desync_cleared", host=self.host_key, reason=reason)
        self._desynced = False
        self._desync_reason = None
        self._desync_at = None

    def require_synced(self) -> None:
        if self._desynced:
            raise ToolError(
                "BRIDGE_DESYNC",
                f"桥接处于 DESYNC 状态：{self._desync_reason or '此前有命令求值超时，宿主状态未知'}",
                "调用 host_resync 重新读取宿主真实状态并比对差异后再继续；"
                "在 resync 之前不要执行任何写操作",
                detail={"desync_reason": self._desync_reason, "desync_at": self._desync_at},
            )

    def desync_state(self) -> dict[str, Any]:
        return {
            "desynced": self._desynced,
            "reason": self._desync_reason,
            "since": self._desync_at,
        }

    # ---- 幂等键（L1 侧缓存；L3 侧另有自己的记录，见 §5.3.5） -------------
    def recall(self, key: str | None) -> BridgeOutcome | None:
        if not key:
            return None
        cached = self._idempotent_results.get(key)
        if cached is None:
            return None
        replay = BridgeOutcome(
            ok=cached.ok,
            command=cached.command,
            data=cached.data,
            error=cached.error,
            host=cached.host,
            warnings=list(cached.warnings) + ["本次结果来自 L1 的幂等键缓存，未再次发送到宿主"],
            timing=dict(cached.timing),
            response=cached.response,
            replayed=True,
        )
        return replay

    def remember(self, key: str | None, outcome: BridgeOutcome) -> None:
        if not key or not outcome.ok:
            return
        self._idempotent_results[key] = outcome
        if len(self._idempotent_results) > 200:
            for stale in list(self._idempotent_results)[:50]:
                self._idempotent_results.pop(stale, None)

    # ---- 求值 ----------------------------------------------------------
    async def evaluate(
        self,
        command: CommandSpec,
        args: Mapping[str, Any],
        *,
        trace_id: str | None = None,
        dry_run: bool = False,
        idempotency_key: str | None = None,
        confirmed: bool = False,
        token_prefix: str | None = None,
        timeout: float | None = None,
        caller: str = "mcp-tool",
        override_desync: bool = False,
    ) -> BridgeOutcome:
        """执行一条命令。所有失败都以 :class:`ToolError` 抛出（带 code 与 remedy）。

        ``override_desync`` 只允许 ``host_resync`` 使用：它是唯一被允许在
        DESYNC 状态下继续读取宿主状态的命令（说明书 §5.3 第 3、4 条）。
        """
        trace_id = trace_id or current_trace_id() or new_trace_id()
        if not override_desync:
            self.require_synced()

        # 非幂等命令：同键重放直接返回上次结果，不重复执行。
        if idempotency_key and not command.idempotent:
            cached = self.recall(idempotency_key)
            if cached is not None:
                if self.logger is not None:
                    self.logger.info(
                        "idempotent_replay",
                        command=command.name,
                        idempotency_key=idempotency_key,
                        trace_id=trace_id,
                    )
                return cached

        waited_ms = 0.0
        wait_started = time.perf_counter()
        if self._lock.locked():
            if self.logger is not None:
                self.logger.debug("eval_waiting_for_lock", command=command.name, host=self.host_key)
        async with self._lock:
            waited_ms = (time.perf_counter() - wait_started) * 1000
            self._waited_total_ms += waited_ms
            # 拿到锁之后重新确认没有被别的调用置为 DESYNC。
            if not override_desync:
                self.require_synced()
            return await self._evaluate_locked(
                command,
                args,
                trace_id=trace_id,
                dry_run=dry_run,
                idempotency_key=idempotency_key,
                confirmed=confirmed,
                token_prefix=token_prefix,
                timeout=timeout,
                caller=caller,
                waited_ms=waited_ms,
            )

    async def _evaluate_locked(
        self,
        command: CommandSpec,
        args: Mapping[str, Any],
        *,
        trace_id: str,
        dry_run: bool,
        idempotency_key: str | None,
        confirmed: bool,
        token_prefix: str | None,
        timeout: float | None,
        caller: str,
        waited_ms: float,
    ) -> BridgeOutcome:
        from .protocol import build_request

        request = build_request(
            command.name,
            args,
            trace_id=trace_id,
            dry_run=dry_run,
            idempotency_key=idempotency_key,
        )
        script_text = self.render_script(command, args, request.meta, request.id)
        effective_timeout = float(timeout if timeout is not None else self.eval_timeout_s)

        started = time.perf_counter()
        self._write_wal(
            phase="request",
            command=command,
            args=args,
            trace_id=trace_id,
            request_id=request.id,
            caller=caller,
            dry_run=dry_run,
            confirmed=confirmed,
            token_prefix=token_prefix,
            idempotency_key=idempotency_key,
            script_text=script_text,
            timeout_s=effective_timeout,
            waited_ms=waited_ms,
        )

        try:
            response = await self.client.request(
                command.name,
                dict(args),
                trace_id=trace_id,
                dry_run=dry_run,
                idempotency_key=idempotency_key,
                timeout=effective_timeout,
                request_id=request.id,
            )
        except ToolError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            if exc.code == "BRIDGE_TIMEOUT":
                self.mark_desync(f"命令 {command.name} 求值超时（{effective_timeout:.0f}s）")
            elif exc.code == "BRIDGE_DISCONNECTED":
                self.mark_desync(f"命令 {command.name} 执行期间连接断开")
            self._write_wal(
                phase="error",
                command=command,
                args=args,
                trace_id=trace_id,
                request_id=request.id,
                caller=caller,
                error={"code": exc.code, "message": exc.message, "remedy": exc.remedy},
                rtt_ms=round(elapsed_ms, 1),
            )
            if self.logger is not None:
                self.logger.error(
                    "eval_failed",
                    command=command.name,
                    code=exc.code,
                    rtt_ms=round(elapsed_ms, 1),
                    trace_id=trace_id,
                )
            raise

        return self._finish(
            command=command,
            args=args,
            response=response,
            trace_id=trace_id,
            request_id=request.id,
            caller=caller,
            idempotency_key=idempotency_key,
            rtt_ms=(time.perf_counter() - started) * 1000,
        )

    def _finish(
        self,
        *,
        command: CommandSpec,
        args: Mapping[str, Any],
        response: ResponseFrame,
        trace_id: str,
        request_id: str,
        caller: str,
        idempotency_key: str | None,
        rtt_ms: float,
    ) -> BridgeOutcome:
        timing = dict(response.timing or {})
        timing["rtt_ms"] = round(rtt_ms, 1)
        warnings = list(response.warnings or [])
        error = response.error
        if error and not isinstance(error, dict):
            error = {"code": "HOST_ERROR", "message": str(error)}
        if error:
            # 面板给出的诊断信息缺失时，用本地 JSX 源码补齐上下文。
            if not error.get("script_excerpt") and error.get("file") and error.get("line"):
                excerpt = (
                    self.source_index.excerpt(str(error["file"]), int(error["line"]))
                    if self.source_index
                    else None
                )
                if excerpt:
                    error["script_excerpt"] = excerpt
                    error["script_excerpt_source"] = "local-jsx-copy"
            if error.get("code") in ("UNKNOWN_COMMAND", "HANDLER_MISSING") and self.logger is not None:
                self.logger.error(
                    "contract_mismatch",
                    command=command.name,
                    code=error.get("code"),
                    hint="L1 与面板的命令契约不一致：运行 scripts/gen_jsx_dispatch.py --check 并重新部署面板",
                )

        outcome = BridgeOutcome(
            ok=bool(response.ok),
            command=command.name,
            data=response.data,
            error=error,
            host=response.host,
            warnings=warnings,
            timing=timing,
            response=response,
            replayed=bool(response.replayed),
            host_key=self.host_key,
        )
        if outcome.ok and idempotency_key and not command.idempotent:
            self.remember(idempotency_key, outcome)
        self._write_wal(
            phase="result",
            command=command,
            args=args,
            trace_id=trace_id,
            request_id=request_id,
            caller=caller,
            ok=outcome.ok,
            error=outcome.error,
            host=outcome.host,
            timing=timing,
        )
        if self.logger is not None:
            self.logger.info(
                "eval_done",
                command=command.name,
                ok=outcome.ok,
                rtt_ms=timing["rtt_ms"],
                host_ms=timing.get("host_ms"),
                error_code=(outcome.error or {}).get("code"),
                trace_id=trace_id,
            )
        return outcome

    # ---- 脚本原文与 WAL -------------------------------------------------
    def render_script(
        self,
        command: CommandSpec,
        args: Mapping[str, Any],
        meta: Mapping[str, Any],
        request_id: str,
    ) -> str:
        if self.source_index is None:
            payload = json.dumps(
                {"id": request_id, "cmd": command.name, "args": dict(args), "meta": dict(meta)},
                ensure_ascii=False,
                default=str,
            )
            return "DSH.dispatch('{0}');".format(payload.replace("'", "\\'"))
        return self.source_index.render_invocation(command, args, meta, request_id)

    def _write_wal(
        self, *, phase: str, command: CommandSpec, args: Mapping[str, Any], **fields: Any
    ) -> None:
        logger = self.logger
        if logger is None or not hasattr(logger, "wal"):
            return
        entry: dict[str, Any] = {
            "phase": phase,
            "host": self.host_key,
            "command": command.name,
            "tool": command.tool,
            "risk": command.risk,
            "args": dict(args),
        }
        entry.update(fields)
        try:
            logger.wal(entry)
        except Exception:  # pragma: no cover - WAL 失败不应阻断命令
            pass

    # ---- 诊断 ----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "host": self.host_key,
            "busy": self.busy,
            "desync": self.desync_state(),
            "idempotent_cache": len(self._idempotent_results),
            "queued_wait_total_ms": self.waited_total_ms,
        }
