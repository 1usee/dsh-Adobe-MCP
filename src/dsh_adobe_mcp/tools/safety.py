"""``safety_*`` 工具：确认令牌、检查点（说明书 §8.2、§8.3）。"""

from __future__ import annotations

from typing import Any

from .context import context

__all__ = [
    "safety_confirm_operation",
    "safety_restore_checkpoint",
    "safety_list_checkpoints",
]


async def safety_confirm_operation(
    confirm_token: str,
    host: str | None = None,
) -> dict[str, Any]:
    """用确认令牌执行此前被挂起的 T2 操作（令牌一次性消费，重放无效）。

    令牌与操作参数哈希绑定，因此"确认了 A 却执行 B"在结构上不可能发生。
    """
    return await context().safety_confirm_operation(confirm_token, host)


async def safety_restore_checkpoint(
    checkpoint_id: str,
    host: str | None = None,
    dry_run: bool = False,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【T2 需确认】把工程恢复到某个检查点。

    恢复前会为**当前**状态自动再建一个检查点，避免"恢复"本身成为不可逆操作。
    ``dry_run=True`` 时只列出将要执行的动作，不修改任何东西。
    """
    return await context().restore_checkpoint(
        checkpoint_id, host=host, dry_run=dry_run, confirm_token=confirm_token
    )


async def safety_list_checkpoints() -> dict[str, Any]:
    """列出可用检查点及其统计信息（数量、占用空间、目录是否可写、剩余磁盘空间）。"""
    return await context().safety_list_checkpoints()
