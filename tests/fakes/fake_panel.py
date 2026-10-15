"""测试用的假面板：在进程内实现桥接协议的服务端（说明书 §5）。

有了它，L1/L2 的整条链路（握手发现 → WebSocket 连接 → 认证 → 命令求值 → 超时/DESYNC →
重连 → 任务轮询）都能在没有 Premiere Pro 的机器上端到端验证。这不是"模拟测试"：
被测的是真实的 CepClient、JsxRunner、BridgeService 与真实的 WebSocket 往返。

可脚本化的行为：

* ``handlers``           按命令名给出返回数据
* ``no_response``        某些命令**不回复**（用来制造求值超时）
* ``delay``              某些命令延迟若干秒再回复
* ``error_response``     某些命令返回结构化错误
* ``close_on``           收到某命令后关闭连接（模拟面板崩溃/宿主崩溃）
* ``auth_token``         令牌校验（错误令牌直接拒绝）
* ``heartbeat_s``        心跳周期
* ``drop_heartbeats``    停止心跳（用来验证心跳超时判定）
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

__all__ = ["FakePanel", "default_host_payloads"]

Handler = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], Any]


def default_host_payloads() -> dict[str, Any]:
    """一份"正常宿主"的默认返回数据（键为命令名）。"""
    return {
        "host.ping": {"pong": True, "panel_version": "1.1.0", "uptime_ms": 1234},
        "host.capabilities": {
            "node_version": "24.0.0",
            "cep_version": "11.0",
            "mixed_context": True,
            "qe_enabled": False,
            "eval_max_chars": 100000,
            "host_app_id": "PPRO",
        },
        "panel.reload": {"reloading": True},
        "host.state": {
            "has_project": True,
            "project_path": "D:\\work\\demo.prproj",
            "dirty": False,
            "active_sequence": "S1",
            "playhead_s": 0.0,
            "app_version": "25.0.0",
        },
        "app.info": {
            "app_version": "25.0.0",
            "app_language": "zh_CN",
            "has_project": True,
            "project_name": "demo",
            "project_path": "D:\\work\\demo.prproj",
            "dirty": False,
            "project_id": "abc123",
        },
        "app.get_state": {
            "active_sequence": "S1",
            "playhead_s": 0.0,
            "in_point_s": None,
            "out_point_s": None,
            "selection": [],
            "warnings": [],
        },
        "app.set_playhead": {"playhead_s": 0.0},
        "project.info": {
            "has_project": True,
            "name": "demo",
            "path": "D:\\work\\demo.prproj",
            "dirty": False,
            "sequences": ["S1"],
            "bin_count": 2,
            "item_count": 3,
        },
        "project.new": {"created": True, "path": "D:\\work\\new.prproj"},
        "project.open": {"opened": True, "path": "D:\\work\\demo.prproj", "name": "demo"},
        "project.save": {"saved": True, "path": "D:\\work\\demo.prproj"},
        "project.save_as": {"saved": True, "path": "D:\\work\\other.prproj"},
        "project.close": {"closed": True, "discarded": False},
        "project.import": {
            "imported": [{"id": "1", "path": "D:\\media\\a.mp4", "name": "a.mp4", "type": "clip"}],
            "missing": [],
        },
        "project.bin_create": {"created": True, "path": "\\Root\\Bins", "name": "Bins"},
        "project.bin_list": {
            "bins": [{"path": "\\Root\\Bins", "name": "Bins", "item_count": 1, "child_count": 0}]
        },
        "project.bin_move": {"moved": 1, "failed": []},
        "project.item_find": {
            "items": [
                {
                    "id": "1",
                    "path": "\\Root\\a.mp4",
                    "name": "a.mp4",
                    "type": "clip",
                    "media_path": "D:\\media\\a.mp4",
                }
            ],
            "truncated": False,
        },
        "sequence.create": {
            "id": "seq1",
            "name": "S1",
            "path": "\\Root\\S1",
            "width": 1920,
            "height": 1080,
            "frame_rate": 25.0,
        },
        "sequence.create_from_clips": {
            "id": "seq2",
            "name": "S2",
            "path": "\\Root\\S2",
            "clip_count": 1,
        },
        "sequence.list": {
            "sequences": [
                {
                    "id": "seq1",
                    "name": "S1",
                    "path": "\\Root\\S1",
                    "video_tracks": 2,
                    "audio_tracks": 2,
                    "duration_s": 12.0,
                    "frame_rate": 25.0,
                }
            ]
        },
        "sequence.info": {
            "id": "seq1",
            "name": "S1",
            "path": "\\Root\\S1",
            "video_tracks": 2,
            "audio_tracks": 2,
            "duration_s": 12.0,
            "in_point_s": 0.0,
            "out_point_s": 12.0,
            "playhead_s": 0.0,
            "frame_rate": 25.0,
            "width": 1920,
            "height": 1080,
        },
        "sequence.set_active": {"active_sequence": "S1", "id": "seq1"},
        "sequence.set_in_out": {"in_point_s": 1.0, "out_point_s": 9.0},
        "sequence.set_work_area": {"work_area_start_s": 0.0, "work_area_end_s": 12.0},
        "timeline.list_clips": {
            "clips": [
                {
                    "ref": "V:1:0",
                    "track_type": "video",
                    "track_index": 1,
                    "index": 0,
                    "name": "a.mp4",
                    "start_s": 0.0,
                    "end_s": 5.0,
                    "duration_s": 5.0,
                    "in_s": 0.0,
                    "out_s": 5.0,
                    "media_path": "D:\\media\\a.mp4",
                    "type": "Clip",
                    "enabled": True,
                    "selected": False,
                }
            ]
        },
        "timeline.clip_info": {
            "ref": "V:1:0",
            "name": "a.mp4",
            "type": "Clip",
            "media_path": "D:\\media\\a.mp4",
            "start_s": 0.0,
            "end_s": 5.0,
            "duration_s": 5.0,
            "in_s": 0.0,
            "out_s": 5.0,
            "speed": 1.0,
            "enabled": True,
            "selected": False,
            "component_count": 2,
        },
        "timeline.insert_clip": {"ref": "V:1:1", "start_s": 5.0, "end_s": 10.0},
        "timeline.overwrite_clip": {"ref": "V:1:0", "start_s": 0.0, "end_s": 5.0},
        "timeline.move_clip": {"ref": "V:1:0", "start_s": 2.0, "end_s": 7.0},
        "timeline.trim_clip": {"ref": "V:1:0", "start_s": 1.0, "end_s": 5.0},
        "timeline.remove_clip": {"removed": True, "ref": "V:1:0"},
        "timeline.razor": {"razored_at_s": 2.5, "tracks": ["V1"]},
        "timeline.add_track": {"track_type": "video", "track_count": 3, "track_index": 3},
        "timeline.remove_track": {
            "removed": True,
            "track_type": "video",
            "track_index": 2,
            "track_count": 1,
        },
        "timeline.add_marker": {
            "index": 0,
            "name": "m1",
            "at_s": 1.0,
            "duration_s": 0.0,
            "type": "comment",
        },
        "timeline.list_markers": {
            "markers": [
                {
                    "index": 0,
                    "name": "m1",
                    "comment": "",
                    "at_s": 1.0,
                    "duration_s": 0.0,
                    "type": "comment",
                }
            ]
        },
        "effects.list_available": {
            "effects": [{"name": "Gaussian Blur", "media_type": "video"}],
            "truncated": False,
        },
        "effects.apply": {"ref": "V:1:0", "effect": "Gaussian Blur", "component_index": 2},
        "effects.get_params": {
            "components": [
                {
                    "index": 0,
                    "name": "Opacity",
                    "match_name": "opacity",
                    "properties": [
                        {"name": "Opacity", "match_name": "opacity", "value": 100, "type": "float"}
                    ],
                }
            ]
        },
        "effects.set_param": {
            "ref": "V:1:0",
            "component": "Opacity",
            "param": "opacity",
            "previous_value": 100,
            "value": 50,
        },
        "effects.apply_transition": {"ref": "V:1:0", "transition": "Cross Dissolve", "at": "start"},
        "media.probe": {
            "id": "1",
            "name": "a.mp4",
            "media_path": "D:\\media\\a.mp4",
            "type": "clip",
            "offline": False,
            "duration_s": 5.0,
            "frame_rate": 25.0,
            "width": 1920,
            "height": 1080,
            "has_video": True,
            "has_audio": True,
            "audio_channels": 2,
            "unavailable": [],
        },
        "media.relink": {"id": "1", "previous_path": "D:\\media\\a.mp4", "media_path": "D:\\media\\b.mp4"},
        "media.generate_proxies": {"job_id": "enc-1", "attached": 1, "pending": 0},
        "export.encode_sequence": {
            "job_id": "enc-1",
            "output_path": "D:\\out\\s1.mp4",
            "sequence": "S1",
            "queued": True,
        },
        "export.encode_file": {
            "job_id": "enc-2",
            "output_path": "D:\\out\\a.mp4",
            "item": "\\Root\\a.mp4",
            "queued": True,
        },
        "export.job_status": {"job_id": "enc-1", "state": "unknown", "progress": None, "message": ""},
    }


class FakePanel:
    """实现桥接协议的服务端。"""

    def __init__(
        self,
        handshake_dir: Path,
        *,
        host: str = "PPRO",
        host_version: str = "25.0.0",
        token: str = "0123456789abcdef0123456789abcdef",
        pid: int | None = None,
        panel_version: str = "1.1.0",
        contract_schema_version: int = 1,
        heartbeat_s: float | None = None,
        capabilities: Mapping[str, Any] | None = None,
        payloads: Mapping[str, Any] | None = None,
        write_handshake: bool = True,
    ) -> None:
        self.handshake_dir = Path(handshake_dir)
        self.host = host
        self.host_version = host_version
        self.token = token
        self.pid = pid if pid is not None else os.getpid()
        self.panel_version = panel_version
        self.contract_schema_version = contract_schema_version
        self.heartbeat_s = heartbeat_s
        self.capabilities = dict(
            capabilities
            or {
                "node_version": "24.0.0",
                "cep_version": "11.0",
                "mixed_context": True,
                "qe_enabled": False,
                "eval_max_chars": 100000,
            }
        )
        self.payloads: dict[str, Any] = {**default_host_payloads(), **dict(payloads or {})}
        self.write_handshake_enabled = write_handshake

        # 可脚本化行为
        self.handlers: dict[str, Handler] = {}
        self.no_response: set[str] = set()
        self.delay: dict[str, float] = {}
        self.error_response: dict[str, dict[str, Any]] = {}
        self.close_on: set[str] = set()
        self.reject_auth = False
        self.drop_heartbeats = False

        # 观测
        self.requests: list[dict[str, Any]] = []
        self.connections: list[Any] = []
        self.auth_attempts: list[str] = []
        self.closed_count = 0

        self.port: int | None = None
        self.handshake_path: Path | None = None
        self._server: Any = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._stopping = False
        self.config_received: list[dict[str, Any]] = []

    # ---- 生命周期 ------------------------------------------------------
    async def start(self) -> "FakePanel":
        from websockets.asyncio.server import serve

        self._server = await serve(self._handle, "127.0.0.1", 0, max_size=8 * 1024 * 1024)
        sockets = list(self._server.sockets or [])
        self.port = sockets[0].getsockname()[1]
        if self.write_handshake_enabled:
            self.write_handshake()
        self._stopping = False
        if self.heartbeat_s:
            self._tasks.append(asyncio.create_task(self._heartbeat_loop(), name="fake-heartbeat"))
        return self

    def write_handshake(self) -> Path:
        self.handshake_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "protocol": 1,
            "port": self.port,
            "token": self.token,
            "host": self.host,
            "pid": self.pid,
            "host_version": self.host_version,
            "panel_version": self.panel_version,
            "contract_schema_version": self.contract_schema_version,
            "started_at": time.time(),
        }
        path = self.handshake_dir / f"handshake-{self.host}-{self.pid}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.handshake_path = path
        return path

    def remove_handshake(self) -> None:
        if self.handshake_path and self.handshake_path.exists():
            self.handshake_path.unlink()

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        for task in list(self._tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        for connection in list(self.connections):
            try:
                await connection.close()
            except Exception:
                pass
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        self.remove_handshake()

    async def crash(self) -> None:
        """模拟面板/宿主崩溃：直接掐断连接与心跳（不写 close 帧）。"""
        for connection in list(self.connections):
            try:
                await connection.close(code=1006 if False else 1011, reason="simulated crash")
            except Exception:
                pass
        self.connections.clear()

    # ---- 心跳 ----------------------------------------------------------
    async def _heartbeat_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.heartbeat_s or 1.0)
            if self.drop_heartbeats:
                continue
            for connection in list(self.connections):
                try:
                    await connection.send(
                        json.dumps(
                            {
                                "type": "heartbeat",
                                "ts": time.time(),
                                "uptime_ms": 1000,
                                "inflight": 0,
                            }
                        )
                    )
                except Exception:
                    pass

    # ---- 连接处理 ------------------------------------------------------
    async def _handle(self, websocket: Any) -> None:
        self.connections.append(websocket)
        authenticated = False
        try:
            async for raw in websocket:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({"type": "error", "reason": "bad_json"}))
                    continue

                kind = frame.get("type")
                if not authenticated:
                    if kind != "auth":
                        await websocket.send(
                            json.dumps({"type": "auth_failed", "reason": "expected_auth_frame"})
                        )
                        await websocket.close(code=1008, reason="expected auth frame")
                        return
                    self.auth_attempts.append(str(frame.get("token")))
                    if self.reject_auth or frame.get("token") != self.token:
                        await websocket.send(
                            json.dumps({"type": "auth_failed", "reason": "bad_token"})
                        )
                        await websocket.close(code=1008, reason="bad token")
                        return
                    authenticated = True
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "auth_ok",
                                "protocol": 1,
                                "panel_version": self.panel_version,
                                "contract_schema_version": self.contract_schema_version,
                                "capabilities": self.capabilities,
                                "warnings": [],
                            }
                        )
                    )
                    continue

                if kind == "config":
                    self.config_received.append(dict(frame.get("config") or {}))
                    continue
                if kind == "req":
                    await self._handle_request(websocket, frame)
                    continue
                await websocket.send(
                    json.dumps({"type": "error", "reason": "unknown_frame_type"})
                )
        except Exception:
            pass
        finally:
            self.closed_count += 1
            if websocket in self.connections:
                self.connections.remove(websocket)

    async def _handle_request(self, websocket: Any, frame: Mapping[str, Any]) -> None:
        cmd = str(frame.get("cmd"))
        args = dict(frame.get("args") or {})
        meta = dict(frame.get("meta") or {})
        request_id = frame.get("id")
        self.requests.append({"cmd": cmd, "args": args, "meta": meta, "id": request_id})

        if cmd in self.close_on:
            await websocket.close(code=1011, reason="simulated crash")
            return
        if cmd in self.no_response:
            return
        delay = self.delay.get(cmd)
        if delay:
            await asyncio.sleep(delay)

        if cmd in self.error_response:
            payload = {"ok": False, "data": None, "error": dict(self.error_response[cmd])}
        elif cmd in self.handlers:
            result = self.handlers[cmd](args, meta, dict(frame))
            if asyncio.iscoroutine(result):
                result = await result
            payload = result if isinstance(result, dict) and "ok" in result else {"ok": True, "data": result}
        elif meta.get("dryRun"):
            # 按契约（§6.2 of JSX_RUNTIME_CONTRACT）：dry-run 只做前置条件校验，
            # 返回结构与真实执行一致（同样的键，值为 null/估算值）。
            template = self.payloads.get(cmd)
            if callable(template):
                template = template(args, meta)
            keys = list(template.keys()) if isinstance(template, dict) else []
            payload = {
                "ok": True,
                "data": {
                    "dry_run": True,
                    "actions": [f"将执行 {cmd}：{args}"],
                    "checks": [{"name": "preconditions", "ok": True, "detail": "假面板"}],
                    **{key: None for key in keys},
                },
            }
        elif cmd in self.payloads:
            data = self.payloads[cmd]
            if callable(data):
                data = data(args, meta)
            payload = {"ok": True, "data": data}
        else:
            payload = {
                "ok": False,
                "data": None,
                "error": {
                    "code": "UNKNOWN_COMMAND",
                    "message": f"假面板未实现命令 {cmd}",
                    "remedy": "在 FakePanel.payloads 中补充该命令的返回数据",
                },
            }

        response = {
            "type": "res",
            "id": request_id,
            "ok": bool(payload.get("ok")),
            "data": payload.get("data"),
            "error": payload.get("error"),
            "host": payload.get("host")
            or {
                "app_version": self.host_version,
                "project_path": "D:\\work\\demo.prproj",
                "dirty": False,
                "has_project": True,
                "active_sequence": "S1",
            },
            "warnings": list(payload.get("warnings") or []),
            "timing": {"host_ms": 5},
        }
        try:
            await websocket.send(json.dumps(response, ensure_ascii=False))
        except Exception:
            pass

    # ---- 观测辅助 ------------------------------------------------------
    def commands_seen(self) -> list[str]:
        return [item["cmd"] for item in self.requests]

    def count(self, cmd: str) -> int:
        return sum(1 for item in self.requests if item["cmd"] == cmd)

    def count_executed(self, cmd: str) -> int:
        """只统计**真正执行**的调用（``meta.dryRun`` 为假的那些）。

        dry-run 按契约只做前置条件校验、不触碰宿主 DOM，因此"T2 未确认前绝不执行"
        这类断言必须用这个口径，而不是笼统地数请求条数——T2 确认流程本身会先发一次
        dry-run 预览（说明书 §8.2 第 2 步要求返回 dry-run 形式的预期结果）。
        """
        return sum(
            1
            for item in self.requests
            if item["cmd"] == cmd and not (item["meta"] or {}).get("dryRun")
        )

    def executed_commands(self) -> list[str]:
        return [item["cmd"] for item in self.requests if not (item["meta"] or {}).get("dryRun")]

    def last_args(self, cmd: str) -> dict[str, Any] | None:
        for item in reversed(self.requests):
            if item["cmd"] == cmd:
                return dict(item["args"])
        return None

    def last_meta(self, cmd: str) -> dict[str, Any] | None:
        for item in reversed(self.requests):
            if item["cmd"] == cmd:
                return dict(item["meta"])
        return None
