"""L2 桥接层：宿主发现、面板部署、CEP 客户端、求值器与桥接服务。

模块划分与说明书 §13.2 一致：

* :mod:`~dsh_adobe_mcp.bridge.protocol`      —— 帧定义与请求配对
* :mod:`~dsh_adobe_mcp.bridge.manifest_check`—— manifest 必需元素校验
* :mod:`~dsh_adobe_mcp.bridge.deployer`      —— 面板部署与回滚
* :mod:`~dsh_adobe_mcp.bridge.cep_client`    —— 握手、连接、认证、心跳
* :mod:`~dsh_adobe_mcp.bridge.jsx_runner`    —— 求值锁、超时、DESYNC、幂等键、WAL
* :mod:`~dsh_adobe_mcp.bridge.service`       —— 把上述组件装配成对 L1 的单一入口
"""

from __future__ import annotations

from .cep_client import CepClient, Handshake, HandshakeWatcher, pid_alive
from .deployer import CepDeployer, DeployError, DeployPlan, DeployResult, set_player_debug_mode
from .jsx_runner import BridgeOutcome, JsxRunner, ScriptSourceIndex
from .manifest_check import ManifestCheck, check_manifest
from .protocol import (
    PROTOCOL_VERSION,
    AuthFrame,
    AuthOkFrame,
    ConfigFrame,
    HeartbeatFrame,
    PairingTable,
    ProtocolError,
    RequestFrame,
    ResponseFrame,
    build_request,
    encode_frame,
    parse_frame,
)
from .service import BridgeService, HostSession, ResolutionResult

__all__ = [
    "PROTOCOL_VERSION",
    "AuthFrame",
    "AuthOkFrame",
    "ConfigFrame",
    "HeartbeatFrame",
    "PairingTable",
    "ProtocolError",
    "RequestFrame",
    "ResponseFrame",
    "build_request",
    "encode_frame",
    "parse_frame",
    "CepClient",
    "Handshake",
    "HandshakeWatcher",
    "pid_alive",
    "CepDeployer",
    "DeployError",
    "DeployPlan",
    "DeployResult",
    "set_player_debug_mode",
    "BridgeOutcome",
    "JsxRunner",
    "ScriptSourceIndex",
    "ManifestCheck",
    "check_manifest",
    "BridgeService",
    "HostSession",
    "ResolutionResult",
]
