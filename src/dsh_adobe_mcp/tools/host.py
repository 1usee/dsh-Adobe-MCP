"""``host_*`` 工具：宿主发现、启动、就绪与重同步（说明书 §7.2）。

这些工具都是纯 L1 实现（不需要经由 ExtendScript），只有 ``host_resync`` 与
``host_reload_panel`` 会向面板发命令——前者正是为了清除 DESYNC 而存在。
"""

from __future__ import annotations

from typing import Any

from ..result import success
from .context import context

__all__ = [
    "host_list",
    "host_status",
    "host_launch",
    "host_wait_ready",
    "host_resync",
    "host_reload_panel",
]


async def host_list() -> dict[str, Any]:
    """枚举本机已安装的宿主（注册表发现，不使用文件系统猜测）。

    返回每个宿主的版本、安装路径、可执行文件是否存在、是否正在运行、是否已建立连接，
    以及多版本并存时"默认会选择哪一个"和歧义提示。**不会启动任何进程。**
    """
    return await context().host_list()


async def host_status(host: str | None = None) -> dict[str, Any]:
    """报告宿主与桥接的当前状态：安装/运行/连接、面板部署状态、契约版本、已启用工具组。

    ``host`` 可写 ``"premiere"``、``"ppre:25.0"`` 形式的版本选择或省略（自动选最高版本）。
    当存在多个版本且未指定时，返回里会明确给出歧义提示。
    """
    return await context().host_status(host)


async def host_launch(
    host: str | None = None,
    project_path: str | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    """启动目标宿主进程，并（默认）等待面板握手与桥接连接建立。

    多版本并存且未指定版本时会拒绝启动（``HOST_AMBIGUOUS_VERSION``），
    避免在不告知用户的情况下接管任意一个正在运行的版本。
    """
    return await context().host_launch(host, wait=wait, project_path=project_path)


async def host_wait_ready(host: str | None = None, timeout_s: float | None = None) -> dict[str, Any]:
    """等待面板就绪并建立（或复用）桥接连接。宿主已在运行但面板未加载时很有用。"""
    return await context().host_wait_ready(host, timeout_s)


async def host_resync(host: str | None = None) -> dict[str, Any]:
    """重新读取宿主的真实状态，与 L1 记录比对并产出差异报告，然后清除 DESYNC。

    这是求值超时/连接断开后的**唯一**正确出路：先 resync，再决定是否继续写操作。
    """
    ctx = context()
    payload = await ctx.bridge.resync(host)
    return success(
        payload,
        warnings=[
            "差异非空时请以 current 为准：不要假设先前的写操作没有生效"
        ]
        if payload.get("changed")
        else [],
        state=ctx.current_state(host_selector=host),
    )


async def host_reload_panel(host: str | None = None) -> dict[str, Any]:
    """重载宿主内的 CEP 面板（开发用）。连接会短暂中断，随后用 host_wait_ready 重连。"""
    return await context().host_reload_panel(host)
