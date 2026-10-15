"""工具上下文：L1 的完整调用流水线（说明书 §3.4、§8.2）。

所有 MCP 工具的实现在 ``tools/*.py``，它们只做两件事：

1. 声明**显式**的输入参数（FastMCP 由签名生成 schema，客户端可见）；
2. 把参数交给 :meth:`ToolContext.invoke`。

流水线本身（参数校验 → 风险分级 → 确认门 → 检查点 → WAL → 桥接调用 → 统一信封）
只在这里实现一次，因此不存在"某个工具忘了做安全检查"这种问题。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..bridge.service import BridgeService
from ..config import Config
from ..contract import CommandSpec, Contract, load_contract
from ..errors import ToolError, spec
from ..jobs.manager import JobManager, JobNotFound, JobQueueFull
from ..logging_setup import (
    StructuredLogger,
    current_trace_id,
    new_trace_id,
    set_trace_id,
)
from ..result import failure, host_state as build_host_state, success
from ..safety.checkpoint import CheckpointError, CheckpointManager, project_dir_of
from ..safety.confirm import ConfirmError, ConfirmTokenStore
from ..safety.paths import PathNotAllowed, PathPolicy, looks_like_path_param
from ..safety.policy import DEFAULT_ENABLED_GROUPS, POLICY, ToolPolicy, policy_for

__all__ = ["ToolContext", "context", "set_context", "IMPACT_NOTES", "queue_full_error"]


#: 当前进程的工具上下文。
#:
#: 刻意用模块级全局而不是 ``contextvars``：MCP 服务器为每个请求创建独立任务，
#: 而 lifespan 里设置的 ContextVar 不会自动传播到请求任务（各任务复制的是创建时的
#: 上下文快照）。stdio 服务器是单会话进程，全局引用行为确定、可预测。
_CTX: ToolContext | None = None

#: T2 工具的影响说明模板（说明书 §8.2 第 2 步要求"将影响的对象、数量、不可逆部分、目标路径"写清）
IMPACT_NOTES: dict[str, dict[str, Sequence[str]]] = {
    "ppro_project_new": {
        "irreversible": ["当前工程会话被替换为全新工程"],
        "reversible": ["原工程文件本身未被修改，可重新打开"],
    },
    "ppro_project_open": {
        "irreversible": ["当前打开的工程被关闭并替换"],
        "reversible": ["原工程文件未被修改；未保存改动已被 L1 提前拦下"],
    },
    "ppro_project_save_as": {
        "irreversible": ["当前工程的路径改变为新路径"],
        "reversible": ["旧路径的工程文件保持原样"],
    },
    "ppro_project_close": {
        "irreversible": ["工程被关闭；confirm_discard=true 时未保存改动会丢失"],
        "reversible": ["磁盘上的工程文件未被修改"],
    },
    "ppro_timeline_remove_clip": {
        "irreversible": ["被删除的片段从时间线消失"],
        "reversible": ["可尝试宿主撤销（Ctrl+Z）；工程素材本身不受影响"],
    },
    "ppro_timeline_razor": {
        "irreversible": ["片段被切成两段，结构改变"],
        "reversible": ["撤销后重新拼接需要手工操作；依赖未公开的 QE 接口"],
    },
    "ppro_timeline_remove_track": {
        "irreversible": ["整条轨道及其上的全部片段被删除"],
        "reversible": ["宿主撤销栈通常不覆盖轨道删除，需要用检查点恢复"],
    },
    "ppro_export_encode_sequence": {
        "irreversible": ["目标路径上已有的同名文件会被编码器覆盖"],
        "reversible": ["工程本身不被修改；删除输出文件即可回退"],
    },
    "ppro_export_encode_file": {
        "irreversible": ["目标路径上已有的同名文件会被编码器覆盖"],
        "reversible": ["工程本身不被修改；删除输出文件即可回退"],
    },
    "safety_restore_checkpoint": {
        "irreversible": ["当前工程内容被检查点内容整体替换"],
        "reversible": ["恢复前会为当前状态自动再建一个检查点，可用它把恢复本身回退"],
    },
}


def context() -> ToolContext:
    """取当前会话的工具上下文。"""
    if _CTX is None:
        raise ToolError(
            "INTERNAL_ERROR",
            "工具上下文未初始化",
            "这是实现错误：MCP 服务器启动时会自动设置上下文，请提交 issue",
        )
    return _CTX


def set_context(ctx: ToolContext | None) -> None:
    global _CTX
    _CTX = ctx


class ToolContext:
    """持有 L1 的全部状态与流水线。"""

    def __init__(
        self,
        config: Config,
        *,
        logger: StructuredLogger | None = None,
        bridge: BridgeService | None = None,
        contract: Contract | None = None,
        confirms: ConfirmTokenStore | None = None,
        checkpoints: CheckpointManager | None = None,
        jobs: JobManager | None = None,
        paths: PathPolicy | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.logger = logger
        self.contract = contract or load_contract()
        self.bridge = bridge or BridgeService(config, logger=logger, contract=self.contract)
        self.confirms = confirms or ConfirmTokenStore(config.safety.confirm_token_ttl_s)
        self.checkpoints = checkpoints or CheckpointManager(
            config.safety.checkpoint_dir,
            max_checkpoints=config.safety.max_checkpoints,
            logger=logger,
        )
        self.jobs = jobs or JobManager(
            state_dir=config.jobs.state_dir,
            poll_interval_s=config.jobs.poll_interval_s,
            max_concurrent_per_host=config.jobs.max_concurrent_per_host,
            max_queued_per_host=config.jobs.max_queued_per_host,
            logger=logger,
            probe=self._job_probe,
        )
        self.paths = paths or PathPolicy(config.safety.allowed_path_roots)
        self._clock = clock or time.time
        self.enabled_groups: list[str] = list(config.tools.enabled_groups)

    # ================= 长任务提交 =================
    async def invoke_job(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        host: str | None = None,
        dry_run: bool = False,
        confirm_token: str | None = None,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        """长任务型工具：确认门 → 检查点 → 提交任务（不阻塞）。"""
        set_trace_id(new_trace_id())
        policy = policy_for(tool)
        try:
            self._require_enabled(policy)
            clean_args = self._clean_args(args)
            self._validate_paths(clean_args)
            command = self.command_for(tool)
            self._require_capability(command)

            if dry_run:
                return await self.invoke(tool, clean_args, host=host, dry_run=True)

            token_operation = None
            if policy.destructive:
                token_operation, pending = await self.t2_gate(
                    tool, clean_args, host=host, confirm_token=confirm_token
                )
                if pending is not None:
                    return pending
                if self.config.safety.checkpoint_before_t2:
                    await self._create_checkpoint(host, reason=f"{tool} 执行前的自动检查点")

            session = await self.bridge.ensure_session(host)
            target_host = session.key

            async def _submit() -> Mapping[str, Any]:
                outcome = await self.bridge.call(
                    command,
                    clean_args,
                    host_selector=host,
                    trace_id=current_trace_id(),
                    idempotency_key=None if command.idempotent else uuid.uuid4().hex[:16],
                    confirmed=token_operation is not None,
                )
                if not outcome.ok:
                    error = outcome.error or {}
                    raise ToolError(
                        str(error.get("code") or "SCRIPT_ERROR"),
                        error.get("message"),
                        error.get("remedy"),
                        detail=error,
                    )
                return dict(outcome.data or {})

            try:
                job = self.jobs.submit(
                    tool=tool,
                    host=target_host,
                    command=command.name,
                    parameters=clean_args,
                    submit_callable=_submit,
                    output_path=str(clean_args.get("output_path")) if clean_args.get("output_path") else output_path,
                    trace_id=current_trace_id(),
                )
            except JobQueueFull as exc:
                return queue_full_error(exc)

            return success(
                {
                    "job_id": job.id,
                    "state": job.state.value,
                    "command": command.name,
                    "output_path": job.output_path,
                    "how_to_poll": "调用 job_status(job_id=...) 查询进度；"
                    "宿主不提供进度时返回 unknown，并由输出文件增长推断",
                },
                state=self.current_state(host_selector=host),
                undo_hint=policy.undo_hint,
            )
        except ToolError as exc:
            return self._error_result(exc, tool=tool, host=host)
        except PathNotAllowed as exc:
            return failure(
                "PATH_NOT_ALLOWED", detail=exc.detail(), state=self.current_state(host_selector=host)
            )
        except (ConfirmError, CheckpointError) as exc:
            return failure(
                getattr(exc, "code", "INTERNAL_ERROR"),
                message=getattr(exc, "message", str(exc)),
                remedy=getattr(exc, "remedy", None),
                detail=getattr(exc, "detail", None),
                state=self.current_state(host_selector=host),
            )
        except Exception as exc:  # noqa: BLE001
            if self.logger is not None:
                self.logger.error(
                    "tool_exception", tool=tool, error=f"{type(exc).__name__}: {exc}"
                )
            return failure(
                "INTERNAL_ERROR",
                message=f"{type(exc).__name__}: {exc}",
                detail={"tool": tool},
                state=self.current_state(host_selector=host),
            )
        finally:
            set_trace_id(None)

    # ================= 基础查询 =================
    def is_enabled(self, tool: str) -> bool:
        policy = policy_for(tool)
        return policy.group in self.enabled_groups

    def enabled_tools(self) -> list[str]:
        return [
            name for name, policy in POLICY.items() if policy.group in self.enabled_groups
        ]

    def command_for(self, tool: str) -> CommandSpec:
        policy = policy_for(tool)
        if not policy.bridged:
            raise ToolError(
                "INTERNAL_ERROR",
                f"工具 {tool} 没有对应的桥接命令",
                "这是实现错误，请提交 issue",
            )
        return self.contract.by_name[policy.command]  # type: ignore[index]

    def current_state(self, *, host_selector: str | None = None) -> dict[str, Any]:
        session = self.bridge.session_for(host_selector)
        state = build_host_state(
            host=session.descriptor.host_type.value if session else None,
            version=session.descriptor.version if session else None,
            project_path=(session.last_state or {}).get("project_path") if session else None,
            dirty=(session.last_state or {}).get("dirty") if session else None,
            connected=bool(session and session.client.connected),
            qe_enabled=bool(self.config.extendscript.enable_qe),
        )
        return state

    # ================= 主流水线 =================
    async def invoke(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        host: str | None = None,
        dry_run: bool = False,
        confirm_token: str | None = None,
    ) -> dict[str, Any]:
        """执行一个桥接工具。返回统一信封（永不抛异常给 MCP 框架）。"""
        trace_token = set_trace_id(new_trace_id())
        started = time.perf_counter()
        policy = policy_for(tool)
        try:
            self._require_enabled(policy)
            clean_args = self._clean_args(args)
            self._validate_paths(clean_args)
            command = self.command_for(tool)
            self._require_capability(command)
            if dry_run and not command.dry_run:
                raise ToolError(
                    "INVALID_ARGUMENT",
                    f"命令 {command.name} 不支持 dry-run",
                    "去掉 dry_run 参数重试（该命令本身是只读的）",
                )
            return await self._invoke_bridged(
                tool=tool,
                policy=policy,
                command=command,
                args=clean_args,
                host=host,
                dry_run=dry_run,
                confirm_token=confirm_token,
            )
        except ToolError as exc:
            return self._error_result(exc, tool=tool, host=host)
        except PathNotAllowed as exc:
            return failure(
                "PATH_NOT_ALLOWED",
                detail=exc.detail(),
                state=self.current_state(host_selector=host),
            )
        except (ConfirmError, CheckpointError) as exc:
            return failure(
                getattr(exc, "code", "INTERNAL_ERROR"),
                message=getattr(exc, "message", str(exc)),
                remedy=getattr(exc, "remedy", None),
                detail=getattr(exc, "detail", None),
                state=self.current_state(host_selector=host),
            )
        except Exception as exc:  # noqa: BLE001 - 任何未预期异常都要变成结构化错误
            if self.logger is not None:
                self.logger.error(
                    "tool_exception",
                    tool=tool,
                    error=f"{type(exc).__name__}: {exc}",
                    trace_id=current_trace_id(),
                )
            return failure(
                "INTERNAL_ERROR",
                message=f"{type(exc).__name__}: {exc}",
                detail={"tool": tool},
                state=self.current_state(host_selector=host),
            )
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            if self.logger is not None:
                self.logger.info(
                    "tool_call",
                    tool=tool,
                    host=host,
                    dry_run=dry_run,
                    risk=policy.risk,
                    elapsed_ms=round(elapsed_ms, 1),
                    trace_id=current_trace_id(),
                )
            set_trace_id(None)

    async def _invoke_bridged(
        self,
        *,
        tool: str,
        policy: ToolPolicy,
        command: CommandSpec,
        args: dict[str, Any],
        host: str | None,
        dry_run: bool,
        confirm_token: str | None,
    ) -> dict[str, Any]:
        token_operation = None
        if policy.destructive and not dry_run:
            token_operation, pending = await self.t2_gate(tool, args, host=host, confirm_token=confirm_token)
            if pending is not None:
                return pending

        if token_operation is not None and self.config.safety.checkpoint_before_t2:
            checkpoint = await self._create_checkpoint(host, reason=f"{tool} 执行前的自动检查点")
            if checkpoint is not None and self.logger is not None:
                self.logger.info(
                    "checkpoint_ready",
                    tool=tool,
                    checkpoint_id=checkpoint.get("id"),
                    path=checkpoint.get("path"),
                )

        idempotency_key = None if command.idempotent else uuid.uuid4().hex[:16]
        trace_id = current_trace_id()
        try:
            outcome = await self.bridge.call(
                command,
                args,
                host_selector=host,
                trace_id=trace_id,
                dry_run=dry_run,
                idempotency_key=idempotency_key,
                confirmed=token_operation is not None,
                token_prefix=token_operation.token[:6] if token_operation else None,
            )
        except ToolError as exc:
            if token_operation is not None:
                self.confirms.record_result(token_operation.token, exc.payload())
            raise

        self._learn_project_dir(outcome.host)

        if token_operation is not None:
            self.confirms.record_result(token_operation.token, outcome.to_public())

        state = self._state_from_outcome(outcome)
        warnings = list(outcome.warnings)
        warnings.extend(self._capability_warnings(command))
        warnings = list(dict.fromkeys(warnings))

        if outcome.ok:
            data = outcome.data
            result = success(
                data=data,
                warnings=warnings,
                state=state,
                undo_hint=policy.undo_hint if policy.risk != "T0" else None,
            )
            if idempotency_key:
                result["idempotency_key"] = idempotency_key
            if dry_run:
                result["dry_run"] = True
            return result

        error = outcome.error or {}
        code = str(error.get("code") or "SCRIPT_ERROR")
        if code not in ("UNKNOWN_COMMAND", "HANDLER_MISSING") and not _is_known_code(code):
            code = "SCRIPT_ERROR"
        return failure(
            code,
            message=error.get("message"),
            remedy=error.get("remedy"),
            detail=error,
            warnings=warnings,
            state=state,
            undo_hint=policy.undo_hint if policy.risk != "T0" else None,
        )

    # ================= 确认门 =================
    async def t2_gate(
        self,
        tool: str,
        args: Mapping[str, Any],
        *,
        host: str | None = None,
        confirm_token: str | None = None,
    ) -> tuple[Any | None, dict[str, Any] | None]:
        """T2 确认门。

        :return: ``(已消费的令牌操作, None)`` 表示可以执行；
                 ``(None, 失败信封)`` 表示必须先确认（信封里带令牌与影响说明）。
        """
        policy = policy_for(tool)
        if confirm_token:
            operation = self.confirms.validate(
                confirm_token, tool=tool, args=dict(args)
            )
            consumed = self.confirms.consume(confirm_token)
            if self.logger is not None:
                self.logger.warning(
                    "t2_confirmed",
                    tool=tool,
                    token_prefix=consumed.token[:6],
                    risk=policy.risk,
                    args=dict(args),
                )
            return consumed, None

        impact = await self.build_impact(tool, args, host=host)
        operation = self.confirms.issue(
            tool,
            args,
            impact,
            host_value={"host": host},
            trace_id=current_trace_id(),
        )
        dry_run_preview = await self._dry_run_preview(tool, args, host=host)
        if self.logger is not None:
            self.logger.warning(
                "t2_confirmation_required",
                tool=tool,
                token_prefix=operation.token[:6],
                args=dict(args),
                trace_id=current_trace_id(),
            )
        return None, failure(
            "CONFIRM_REQUIRED",
            message=(
                f"{tool} 是 T2 破坏性操作，尚未执行。"
                f"确认后携带令牌再次调用即可执行（令牌与本次参数哈希绑定）"
            ),
            data={
                "confirm_token": operation.token,
                "impact": impact,
                "expires_at": operation.expires_at,
                "expires_in_s": max(0, int(operation.expires_at - self._clock())),
                "dry_run_result": dry_run_preview,
                "how_to_confirm": (
                    "调用 safety_confirm_operation(confirm_token=...) 执行，"
                    "或用完全相同的参数再次调用本工具并带上 confirm_token"
                ),
            },
            warnings=self._capability_warnings(
                self.command_for(tool) if policy.bridged else None
            ),
            state=self.current_state(host_selector=host),
        )

    async def build_impact(
        self, tool: str, args: Mapping[str, Any], *, host: str | None = None
    ) -> dict[str, Any]:
        """计算影响说明：将影响的对象、数量、不可逆部分、目标路径（说明书 §8.2）。"""
        policy = policy_for(tool)
        notes = IMPACT_NOTES.get(tool, {"irreversible": [], "reversible": []})
        impact: dict[str, Any] = {
            "tool": tool,
            "risk": policy.risk,
            "summary": policy.summary,
            "parameters": dict(args),
            "irreversible": list(notes.get("irreversible", [])),
            "reversible": list(notes.get("reversible", [])),
            "undo_hint": policy.undo_hint,
            "targets": {},
            "observed_at": self._clock(),
        }
        targets: dict[str, Any] = {}
        try:
            targets = await self._observe_targets(tool, args, host=host)
        except ToolError as exc:
            targets = {"observe_error": {"code": exc.code, "message": exc.message}}
        except Exception as exc:  # noqa: BLE001 - 观测失败不能阻止确认流程
            targets = {"observe_error": {"code": "INTERNAL_ERROR", "message": str(exc)}}
        impact["targets"] = targets
        if not targets or "observe_error" in targets:
            impact.setdefault("notes", []).append(
                "无法读取目标对象的当前状态（宿主未连接或对象不存在）；"
                "确认前请用对应的查询工具人工核对"
            )
        return impact

    async def _observe_targets(
        self, tool: str, args: Mapping[str, Any], *, host: str | None
    ) -> dict[str, Any]:
        """尽力读取将被影响对象的当前状态（读不到就如实报告）。"""
        targets: dict[str, Any] = {}
        if tool == "ppro_timeline_remove_clip" and args.get("ref"):
            outcome = await self.bridge.call(
                self.contract.by_name["timeline.clip_info"],
                {"ref": args["ref"], "sequence": args.get("sequence")},
                host_selector=host,
            )
            if outcome.ok:
                data = outcome.data or {}
                targets["clip"] = {
                    "ref": data.get("ref"),
                    "name": data.get("name"),
                    "start_s": data.get("start_s"),
                    "end_s": data.get("end_s"),
                    "duration_s": data.get("duration_s"),
                }
        elif tool == "ppro_project_close":
            data = await self._project_info(host)
            targets["project"] = {
                "path": data.get("path"),
                "name": data.get("name"),
                "dirty": data.get("dirty"),
                "confirm_discard": args.get("confirm_discard", False),
                "unsaved_changes_will_be_lost": bool(
                    data.get("dirty") and args.get("confirm_discard")
                ),
            }
        elif tool in ("ppro_project_new", "ppro_project_open", "ppro_project_save_as"):
            path = args.get("path")
            if path:
                candidate = self.paths.normalize(path)
                targets["file"] = {
                    "path": str(candidate),
                    "exists": candidate.exists(),
                    "will_overwrite": candidate.exists(),
                    "size": candidate.stat().st_size if candidate.is_file() else None,
                }
        elif tool in ("ppro_export_encode_sequence", "ppro_export_encode_file"):
            output = args.get("output_path")
            if output:
                candidate = self.paths.normalize(output)
                targets["output"] = {
                    "path": str(candidate),
                    "exists": candidate.exists(),
                    "size": candidate.stat().st_size if candidate.is_file() else None,
                    "preset_path": args.get("preset_path"),
                    "preset_exists": Path(str(args.get("preset_path"))).is_file()
                    if args.get("preset_path")
                    else None,
                }
            if tool == "ppro_export_encode_sequence":
                sequence = args.get("sequence")
                targets["sequence"] = {"requested": sequence or "(活动序列)"}
        elif tool == "ppro_timeline_remove_track":
            sequence = args.get("sequence")
            track_type = args.get("track_type", "video")
            index = args.get("track_index")
            clips = await self.bridge.call(
                self.contract.by_name["timeline.list_clips"],
                {"sequence": sequence, "track_type": track_type, "track_index": index},
                host_selector=host,
            )
            if clips.ok:
                items = (clips.data or {}).get("clips", [])
                targets["track"] = {
                    "track_type": track_type,
                    "track_index": index,
                    "clip_count": len(items),
                    "clips": [item.get("name") for item in items],
                }
        elif tool == "safety_restore_checkpoint":
            checkpoint_id = args.get("checkpoint_id")
            if checkpoint_id:
                checkpoint = self.checkpoints.get(str(checkpoint_id))
                targets["checkpoint"] = checkpoint.to_public()
            targets["current"] = await self._project_info(host)
        return targets

    async def _dry_run_preview(
        self, tool: str, args: Mapping[str, Any], *, host: str | None
    ) -> dict[str, Any] | None:
        """T2 确认前给出 dry-run 形式的预期结果（说明书 §8.2 第 2 步）。

        宿主不可用时如实返回 ``None`` 与原因，不假装有预览。
        """
        policy = policy_for(tool)
        if not policy.bridged:
            return None
        command = self.contract.by_name[policy.command]  # type: ignore[index]
        if not command.dry_run:
            return None
        if self.bridge.session_for(host) is None:
            return {
                "available": False,
                "reason": "宿主未连接，无法生成 dry-run 预览；确认令牌依然有效",
            }
        try:
            outcome = await self.bridge.call(
                command,
                dict(args),
                host_selector=host,
                dry_run=True,
                timeout=min(self.config.eval_timeout_s, 15),
            )
        except ToolError as exc:
            return {"available": False, "reason": f"{exc.code}: {exc.message}"}
        return {
            "available": outcome.ok,
            "expected": outcome.data,
            "error": outcome.error,
        }

    # ================= 检查点 =================
    async def _project_info(self, host: str | None) -> dict[str, Any]:
        try:
            outcome = await self.bridge.call(
                self.contract.by_name["project.info"], {}, host_selector=host, timeout=15
            )
        except ToolError:
            return {}
        return dict(outcome.data or {}) if outcome.ok else {}

    async def _create_checkpoint(
        self, host: str | None, *, reason: str
    ) -> dict[str, Any] | None:
        """T2 执行前的检查点（说明书 §8.3）。

        * 有未保存改动 → 先 ``save``（T1，并记录），再文件级复制；
        * 工程从未保存过 → 退化为 ``save_as`` 到检查点目录，并在返回里明确告知路径改变；
        * 没有打开的工程 → 无需检查点，返回 None 并记 warning。
        """
        info = await self._project_info(host)
        if not info or not info.get("has_project"):
            if self.logger is not None:
                self.logger.info("checkpoint_skipped", reason="no_open_project", tool_reason=reason)
            return None

        project_path = info.get("path")
        dirty = bool(info.get("dirty"))
        summary: dict[str, Any] = {"reason": reason, "project_path": project_path, "saved_first": False}

        if dirty and project_path:
            await self.bridge.call(self.contract.by_name["project.save"], {}, host_selector=host)
            summary["saved_first"] = True
            summary["note"] = "工程有未保存改动，已先执行 save（T1）再复制"

        if project_path:
            try:
                checkpoint = self.checkpoints.create(
                    project_path, label=reason, project_name=info.get("name")
                )
            except CheckpointError as exc:
                raise ToolError(exc.code, exc.message, exc.remedy, detail=exc.detail) from exc
            summary.update(
                {
                    "id": checkpoint.id,
                    "path": checkpoint.path,
                    "size": checkpoint.size,
                    "kind": checkpoint.kind,
                }
            )
            return summary

        # 工程从未保存过：退化为 save_as 到检查点目录。
        self.checkpoints.ensure_directory()
        target = self.checkpoints.directory / f"{_safe_name(info.get('name') or 'untitled')}__{_stamp()}.prproj"
        outcome = await self.bridge.call(
            self.contract.by_name["project.save_as"],
            {"path": str(target)},
            host_selector=host,
        )
        if not outcome.ok:
            error = outcome.error or {}
            raise ToolError(
                error.get("code", "CHECKPOINT_FAILED"),
                "工程从未保存过，且退化的 save_as 检查点也失败了，因此拒绝执行破坏性操作",
                "先用 ppro_project_save_as 把工程保存到一个具体路径，再重试",
                detail=error,
            )
        checkpoint = self.checkpoints.adopt(
            target,
            project_path=str(target),
            label=reason,
            project_name=info.get("name"),
            kind="save_as",
        )
        summary.update(
            {
                "id": checkpoint.id,
                "path": checkpoint.path,
                "size": checkpoint.size,
                "kind": "save_as",
                "project_path_changed": True,
                "warning": "工程此前从未保存过，已另存到检查点路径；当前工程路径已改变",
            }
        )
        if self.logger is not None:
            self.logger.warning(
                "checkpoint_via_save_as", path=str(target), reason="project_never_saved"
            )
        return summary

    # ================= 路径与参数 =================
    def _clean_args(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """丢掉值为 None 的参数，让 L3 侧的默认值生效。"""
        return {key: value for key, value in args.items() if value is not None}

    def _validate_paths(self, args: dict[str, Any]) -> None:
        for key, value in list(args.items()):
            if not looks_like_path_param(key):
                continue
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                args[key] = [str(self.paths.check(item, allow_new=True)) for item in value]
            else:
                args[key] = str(self.paths.check(value, allow_new=True))

    def _learn_project_dir(self, host_value: Mapping[str, Any] | None) -> None:
        """把当前工程目录加入路径白名单（说明书 §10：默认允许用户目录与工程目录）。"""
        if not host_value:
            return
        directory = project_dir_of(host_value.get("project_path"))
        if directory is not None:
            self.paths.add_root(directory)

    def _require_enabled(self, policy: ToolPolicy) -> None:
        if policy.group not in self.enabled_groups:
            raise ToolError(
                "TOOL_NOT_ENABLED",
                f"工具 {policy.name} 属于未启用的工具组 {policy.group}",
                "在配置 tools.enabled_groups 中加入该工具组后重启 MCP 服务器"
                f"（可选组：{', '.join(self._selectable_groups())}）",
                detail={"tool": policy.name, "group": policy.group},
            )

    def _capability_warnings(self, command: CommandSpec | None) -> list[str]:
        """启用 QE 时，**每次调用**都要带风险警告（说明书 §6.2），包括确认门返回。"""
        if command is None or not self.config.extendscript.enable_qe:
            return []
        if command.capability not in ("qe", "public_or_qe"):
            return []
        return ["QE 接口已启用：这是 Adobe 未公开的接口，异常时可能导致宿主崩溃（说明书 §6.2）"]

    def _require_capability(self, command: CommandSpec) -> None:
        """QE 门禁提前到确认门之前。

        否则会出现这种糟糕体验：先让用户确认一个 T2 破坏性操作，确认完才告诉他
        "这个宿主接口没启用"。能在确认前判定的拒绝，就应当在确认前给出。
        （``BridgeService.call`` 里保留同一道门禁，作为纵深防御。）
        """
        if command.capability == "qe" and not self.config.extendscript.enable_qe:
            raise ToolError(
                "QE_DISABLED",
                f"命令 {command.name} 依赖未公开的 QE 接口，当前配置未启用",
                "若确实需要：把配置项 extendscript.enable_qe 设为 true 并重启宿主面板。"
                "注意 QE 是 Adobe 未公开的接口，可能导致宿主崩溃（说明书 §6.2）",
                detail={"command": command.name, "capability": command.capability},
            )

    def _selectable_groups(self) -> list[str]:
        return [group for group in ("project_edit", "timeline_edit", "effects", "export", "media")]

    def _state_from_outcome(self, outcome: Any) -> dict[str, Any]:
        host_value = outcome.host or {}
        session = self.bridge.sessions.get(getattr(outcome, "host_key", None) or "")
        return build_host_state(
            host=session.descriptor.host_type.value if session else None,
            version=host_value.get("app_version")
            or (session.descriptor.version if session else None),
            project_path=host_value.get("project_path"),
            dirty=host_value.get("dirty"),
            connected=bool(session and session.client.connected),
            qe_enabled=bool(self.config.extendscript.enable_qe),
            extra={"active_sequence": host_value.get("active_sequence")}
            if host_value.get("active_sequence") is not None
            else None,
        )

    def _error_result(self, exc: ToolError, *, tool: str, host: str | None) -> dict[str, Any]:
        policy = POLICY.get(tool)
        return failure(
            exc.code,
            message=exc.message,
            remedy=exc.remedy,
            detail=exc.detail,
            undo_hint=policy.undo_hint if policy and policy.risk != "T0" else None,
            state=self.current_state(host_selector=host),
        )

    # ================= L1 专属工具的实现 =================
    async def host_list(self) -> dict[str, Any]:
        payload = self.bridge.list_hosts()
        warnings = list(payload.pop("notes", []))
        if not payload["hosts"]:
            warnings.append(
                "没有发现任何已安装的宿主：若确认已安装，请检查注册表 InstallPath"
            )
        return success(payload, warnings=warnings, state=self.current_state())

    async def host_status(self, host: str | None) -> dict[str, Any]:
        payload = await self.bridge.status(host)
        warnings = list(payload.pop("warnings", []))
        selected = (payload.get("host") or {}).get("selected") or {}
        state = build_host_state(
            host=(selected.get("host") if selected else None),
            version=selected.get("version") if selected else None,
            project_path=None,
            dirty=None,
            connected=bool(payload.get("sessions")),
            qe_enabled=bool(self.config.extendscript.enable_qe),
        )
        payload["enabled_tools"] = self.enabled_tools()
        payload["tool_groups"] = {
            "enabled": self.enabled_groups,
            "default": list(DEFAULT_ENABLED_GROUPS),
        }
        return success(payload, warnings=warnings, state=state)

    async def host_launch(
        self, host: str | None, *, wait: bool = True, project_path: str | None
    ) -> dict[str, Any]:
        if project_path:
            project_path = str(self.paths.check(project_path, must_be_file=True))
        payload = await self.bridge.launch(host, wait=wait, project_path=project_path)
        return success(
            payload,
            warnings=[],
            state=self.current_state(host_selector=host),
            undo_hint=policy_for("host_launch").undo_hint,
        )

    async def host_wait_ready(self, host: str | None, timeout_s: float | None) -> dict[str, Any]:
        payload = await self.bridge.wait_ready(host, timeout_s=timeout_s)
        return success(payload, warnings=list(payload.get("warnings", [])), state=self.current_state(host_selector=host))

    async def host_reload_panel(self, host: str | None) -> dict[str, Any]:
        payload = await self.bridge.reload_panel(host)
        return success(payload, state=self.current_state(host_selector=host))

    async def job_list(self, limit: int, host: str | None) -> dict[str, Any]:
        items = self.jobs.list(limit=limit, host=host)
        return success(
            {"jobs": items, "summary": self.jobs.summary()}, state=self.current_state(host_selector=host)
        )

    async def job_status(self, job_id: str, host: str | None) -> dict[str, Any]:
        try:
            payload = self.jobs.status(job_id)
        except JobNotFound as exc:
            return failure(
                "JOB_NOT_FOUND",
                message=f"找不到任务 {job_id}",
                detail={"job_id": job_id},
                state=self.current_state(host_selector=host),
            )
        return success(payload, state=self.current_state(host_selector=host))

    async def job_cancel(self, job_id: str, host: str | None) -> dict[str, Any]:
        try:
            job = self.jobs.get(job_id)
        except JobNotFound:
            return failure(
                "JOB_NOT_FOUND",
                message=f"找不到任务 {job_id}",
                detail={"job_id": job_id},
                state=self.current_state(host_selector=host),
            )

        async def _cancel(target: Any) -> bool:
            if not target.encoder_job_id:
                return False
            # 宿主/编码器侧的取消接口并非所有版本都有：没有实现就如实返回"不支持"。
            outcome = await self.bridge.call(
                self.contract.by_name["export.job_status"],
                {"job_id": target.encoder_job_id, "action": "cancel"},
                host_selector=host,
            )
            return bool(outcome.ok and (outcome.data or {}).get("cancelled"))

        result = await self.jobs.cancel(job_id, _cancel)
        if result["cancelled"]:
            return success(result, state=self.current_state(host_selector=host))
        return failure(
            "JOB_NOT_CANCELLABLE",
            message=result.get("message") or "该任务无法取消",
            data=result,
            state=self.current_state(host_selector=host),
        )

    async def safety_list_checkpoints(self) -> dict[str, Any]:
        checkpoints = [item.to_public() for item in self.checkpoints.list()]
        stats = self.checkpoints.stats()
        warnings: list[str] = []
        if not stats["writable"]:
            warnings.append(
                "检查点目录不可写：T2 破坏性操作会被拒绝执行（这是刻意的安全设计）"
            )
        return success(
            {"checkpoints": checkpoints, "stats": stats}, warnings=warnings, state=self.current_state()
        )

    async def safety_confirm_operation(
        self, confirm_token: str, host: str | None
    ) -> dict[str, Any]:
        """用确认令牌执行此前被挂起的 T2 操作。"""
        try:
            operation = self.confirms.peek(confirm_token)
        except ConfirmError as exc:
            return failure(
                exc.code,
                message=exc.message,
                remedy=exc.remedy,
                state=self.current_state(host_selector=host),
            )
        tool = operation.tool
        args = dict(operation.args)
        try:
            if not policy_for(tool).bridged:
                # L1 专属的 T2 工具（例如恢复检查点）走各自的实现。
                return await self._execute_local_t2(
                    tool, args, confirm_token=confirm_token, host=host
                )
            # 长任务型工具必须回到长任务通道：确认之后应当提交 job 并返回 job_id，
            # 而不是同步阻塞地把命令执行掉（说明书 §9.2）。
            if self.command_for(tool).job:
                return await self.invoke_job(tool, args, host=host, confirm_token=confirm_token)
            return await self.invoke(tool, args, host=host, confirm_token=confirm_token)
        except ToolError as exc:
            # 确认阶段本身的失败（例如检查点建不出来）也必须是带 remedy 的信封，
            # 而不是把异常抛给调用者。
            if self.logger is not None:
                self.logger.error(
                    "confirm_execution_failed",
                    tool=tool,
                    code=exc.code,
                    message=exc.message,
                )
            return self._error_result(exc, tool=tool, host=host)

    async def _execute_local_t2(
        self, tool: str, args: dict[str, Any], *, confirm_token: str, host: str | None
    ) -> dict[str, Any]:
        if tool == "safety_restore_checkpoint":
            return await self.restore_checkpoint(
                str(args.get("checkpoint_id", "")), host=host, confirm_token=confirm_token
            )
        return failure(
            "INTERNAL_ERROR",
            message=f"工具 {tool} 没有本地 T2 实现",
            state=self.current_state(host_selector=host),
        )

    async def restore_checkpoint(
        self,
        checkpoint_id: str,
        *,
        host: str | None = None,
        dry_run: bool = False,
        confirm_token: str | None = None,
    ) -> dict[str, Any]:
        """恢复检查点（T2，且恢复前为当前状态再建一个检查点）。"""
        policy = policy_for("safety_restore_checkpoint")
        if not dry_run:
            _operation, pending = await self.t2_gate(
                "safety_restore_checkpoint",
                {"checkpoint_id": checkpoint_id},
                host=host,
                confirm_token=confirm_token,
            )
            if pending is not None:
                return pending

        try:
            checkpoint = self.checkpoints.get(checkpoint_id)
        except CheckpointError as exc:
            return failure(exc.code, message=exc.message, remedy=exc.remedy, detail=exc.detail)

        if dry_run:
            return success(
                {
                    "dry_run": True,
                    "checkpoint": checkpoint.to_public(),
                    "actions": [
                        f"为当前工程状态再建一个检查点（可回退本次恢复）",
                        f"用 {checkpoint.path} 打开工程，替换当前工程内容",
                    ],
                    "current_project": await self._project_info(host),
                },
                state=self.current_state(host_selector=host),
                undo_hint=policy.undo_hint,
            )

        pre = await self._create_checkpoint(host, reason=f"恢复 {checkpoint.id} 前的当前状态")
        outcome = await self.bridge.call(
            self.contract.by_name["project.open"],
            {"path": checkpoint.path},
            host_selector=host,
            confirmed=True,
        )
        state = self._state_from_outcome(outcome)
        if not outcome.ok:
            error = outcome.error or {}
            return failure(
                str(error.get("code") or "SCRIPT_ERROR"),
                message=error.get("message"),
                remedy=error.get("remedy"),
                detail=error,
                data={"pre_restore_checkpoint": pre},
                state=state,
                undo_hint=policy.undo_hint,
            )
        if self.logger is not None:
            self.logger.warning(
                "checkpoint_restored",
                checkpoint_id=checkpoint.id,
                pre_restore_checkpoint=(pre or {}).get("id"),
                trace_id=current_trace_id(),
            )
        return success(
            {
                "restored": checkpoint.to_public(),
                "pre_restore_checkpoint": pre,
            },
            warnings=[
                "恢复操作已完成；如需回退本次恢复，用 pre_restore_checkpoint 再执行一次恢复"
            ],
            state=state,
            undo_hint=policy.undo_hint,
        )

    # ================= 导出预设（L1 侧文件系统扫描） =================
    def list_presets(self, kind: str, query: str | None) -> dict[str, Any]:
        presets: list[dict[str, Any]] = []
        searched: list[str] = []
        missing: list[str] = []
        for directory in self.config.export.preset_dirs:
            searched.append(str(directory))
            if not directory.is_dir():
                missing.append(str(directory))
                continue
            for path in sorted(directory.rglob("*.epr")):
                name = path.stem
                if query and query.lower() not in name.lower():
                    continue
                presets.append(
                    {
                        "name": name,
                        "path": str(path),
                        "kind": "file",
                        "encoder": "AME",
                        "size": path.stat().st_size,
                    }
                )
        warnings: list[str] = []
        if not presets and missing:
            warnings.append(
                "没有找到任何 .epr 预设：检查 export.preset_dirs 配置，"
                "或用宿主导出面板手工确认预设文件位置"
            )
        return {
            "presets": presets,
            "searched_dirs": searched,
            "missing_dirs": missing,
            "requested_kind": kind,
            "notes": [
                "预设列表来自文件系统扫描（AME 的 Presets 目录），"
                "宿主公开 API 不提供预设枚举",
            ],
        }

    # ================= 任务探测 =================
    async def _job_probe(self, job: Any) -> Mapping[str, Any] | None:
        """向宿主查询编码任务状态；宿主不提供进度时返回 None（由文件增长兜底）。"""
        if not job.encoder_job_id:
            return None
        try:
            outcome = await self.bridge.call(
                self.contract.by_name["export.job_status"],
                {"job_id": job.encoder_job_id},
                host_selector=job.host,
                timeout=min(self.config.eval_timeout_s, 10),
            )
        except ToolError:
            return None
        if not outcome.ok:
            return None
        data = dict(outcome.data or {})
        return {
            "state": data.get("state"),
            "progress": data.get("progress"),
            "message": data.get("message"),
            "source": "encoder",
        }

    # ================= 生命周期 =================
    async def startup(self) -> dict[str, Any]:
        await self.jobs.start()
        return {
            "enabled_tools": len(self.enabled_tools()),
            "tool_groups": self.enabled_groups,
            "checkpoint_dir": str(self.checkpoints.directory),
            "handshake_dir": str(self.config.bridge.handshake_dir),
        }

    async def shutdown(self) -> None:
        await self.jobs.stop()
        await self.bridge.shutdown()
        if self.logger is not None:
            self.logger.close()


def _is_known_code(code: str) -> bool:
    try:
        spec(code)
        return True
    except Exception:  # pragma: no cover
        return False


def _safe_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-\u4e00-\u9fff" else "_" for ch in name)
    return (cleaned or "untitled")[:48]


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def queue_full_error(exc: JobQueueFull) -> dict[str, Any]:
    """把"排队已满"映射成带 remedy 的统一信封（长任务提交路径共用）。"""
    return failure(
        "JOB_QUEUE_FULL",
        message=f"宿主 {exc.host} 上排队的长任务已达上限 {exc.limit}",
        detail={"host": exc.host, "limit": exc.limit},
    )
