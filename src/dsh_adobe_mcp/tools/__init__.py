"""工具层：MCP 工具的定义与注册（说明书 §7、§14.2）。

* :mod:`~dsh_adobe_mcp.tools.context` —— 唯一的调用流水线（校验/分级/确认/检查点/WAL）
* ``host``/``app``/``project``/``sequence``/``timeline``/``effects``/``media``/``export``/
  ``jobs``/``safety`` —— 56 个工具的显式定义
* :mod:`~dsh_adobe_mcp.tools.registry` —— 按工具组把启用的工具注册到 FastMCP
"""

from __future__ import annotations

from .context import ToolContext, context, set_context

__all__ = ["ToolContext", "context", "set_context"]
