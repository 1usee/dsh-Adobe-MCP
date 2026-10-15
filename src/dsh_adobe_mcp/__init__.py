"""dsh-adobe-mcp —— 通过 MCP 驱动 Adobe 视频制作软件（第一阶段：Premiere Pro）。

包结构对应设计说明书 §13.2：
    server.py   MCP 应用装配与工具注册（L1）
    config.py   配置加载与校验
    result.py   统一返回信封
    errors.py   错误码与 remedy 映射
    hosts/      宿主发现与适配
    bridge/     CEP 面板部署、桥接协议与求值
    tools/      面向 MCP 客户端的工具
    safety/     风险分级、确认令牌、检查点、路径白名单
    jobs/       长任务模型
"""

from __future__ import annotations

__all__ = ["__version__", "SERVER_NAME"]

SERVER_NAME = "dsh-adobe-mcp"
__version__ = "1.1.0"
