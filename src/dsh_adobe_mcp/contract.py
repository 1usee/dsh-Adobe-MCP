"""命令契约：``cep-extension/jsx/commands.json`` 的加载与校验。

本模块是"三方一致性"的 Python 侧入口（说明书 §6.3、§14.2）：

* L1 的工具定义（``safety/policy.py`` 的风险表 + ``tools/*.py`` 的函数签名）
* L3 的路由表（``cep-extension/jsx/dispatch.jsx``，由 ``scripts/gen_jsx_dispatch.py`` 生成）
* 本文件加载的契约清单

三者必须一致，任何漂移都由 ``tests/contract`` 直接判失败。

契约文件是**唯一真源**：命令名、参数签名、返回结构、风险等级建议值都写在这里，
其他位置只允许引用，不允许各自维护一份。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Risk = Literal["T0", "T1", "T2"]
Group = Literal["core", "project_edit", "timeline_edit", "effects", "media", "export"]
Capability = Literal["public", "public_or_qe", "qe"]
Executor = Literal["jsx", "node"]
ParamType = Literal[
    "string", "int", "float", "bool", "array<string>", "object", "any", "enum"
]

GROUPS: tuple[Group, ...] = (
    "core",
    "project_edit",
    "timeline_edit",
    "effects",
    "media",
    "export",
)

#: L1 侧通用参数，不由契约声明：宿主选择、dry-run、确认令牌。
L1_GENERIC_PARAMS: frozenset[str] = frozenset({"host", "dry_run", "confirm_token"})

CONTRACT_RELATIVE_PATH = Path("cep-extension") / "jsx" / "commands.json"
DISPATCH_RELATIVE_PATH = Path("cep-extension") / "jsx" / "dispatch.jsx"


def repo_root() -> Path:
    """返回仓库根目录。

    优先使用环境变量 ``DSH_ADOBE_MCP_ROOT``（便于测试注入），否则由本文件位置推导。
    """
    override = os.environ.get("DSH_ADOBE_MCP_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def contract_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / CONTRACT_RELATIVE_PATH


class ParamSpec(BaseModel):
    """一个命令参数的签名。"""

    name: str
    type: ParamType
    required: bool = False
    default: Any = None
    enum: list[str] | None = None
    description: str = ""

    @model_validator(mode="after")
    def _check(self) -> ParamSpec:
        if not self.name.isidentifier():
            raise ValueError(f"参数名不是合法标识符: {self.name!r}")
        if self.type == "enum":
            if not self.enum:
                raise ValueError(f"参数 {self.name} 声明为 enum 但未给出取值")
        elif self.enum:
            raise ValueError(f"参数 {self.name} 给出了 enum 取值但类型不是 enum")
        if self.required and self.default is not None:
            raise ValueError(f"必需参数 {self.name} 不应带默认值")
        return self


class CommandSpec(BaseModel):
    """一条桥接命令的契约。"""

    name: str
    tool: str | None = Field(
        default=None, description="对应的 MCP 工具名；null 表示内部命令（不直接暴露）"
    )
    group: Group
    host: str
    executor: Executor
    ops_file: str | None = None
    handler: str
    risk: Risk = Field(description="风险等级建议值；L1 的 policy.py 为准")
    capability: Literal["public", "public_or_qe", "qe"]
    undocumented: bool = False
    idempotent: bool
    dry_run: bool
    job: bool = False
    summary: str
    params: list[ParamSpec] = Field(default_factory=list)
    returns: str = ""

    @model_validator(mode="after")
    def _check(self) -> CommandSpec:
        parts = self.name.split(".")
        if len(parts) != 2 or not all(p.isidentifier() for p in parts):
            raise ValueError(f"命令名必须是 <命名空间>.<名称> 形式: {self.name!r}")
        if not self.handler.isidentifier():
            raise ValueError(f"处理器名不是合法标识符: {self.handler!r} / {self.name}")
        if self.executor == "jsx" and not self.ops_file:
            raise ValueError(f"jsx 执行器的命令必须声明 ops_file: {self.name}")
        if self.executor == "node" and self.ops_file:
            raise ValueError(f"node 执行器的命令不应声明 ops_file: {self.name}")
        # QE 若为必需能力，则一定是未公开接口（说明书 §6.2 的护栏要求）
        if self.capability == "qe" and not self.undocumented:
            raise ValueError(f"capability=qe 的命令必须标记 undocumented: {self.name}")
        if self.capability == "qe" and not self.summary:
            raise ValueError(f"QE 命令必须写清风险说明: {self.name}")
        names = [p.name for p in self.params]
        if len(names) != len(set(names)):
            raise ValueError(f"参数名重复: {self.name}")
        return self


class Contract(BaseModel):
    """整份契约。"""

    schema_version: int
    description: str = ""
    conventions: dict[str, Any] = Field(default_factory=dict)
    commands: list[CommandSpec]

    @model_validator(mode="after")
    def _check(self) -> Contract:
        names = [c.name for c in self.commands]
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"命令名重复: {dupes}")
        tools = [c.tool for c in self.commands if c.tool]
        if len(tools) != len(set(tools)):
            dupes = sorted({t for t in tools if tools.count(t) > 1})
            raise ValueError(f"同一工具被多条命令占用: {dupes}")
        return self

    # ---- 查询辅助 -------------------------------------------------------
    @property
    def by_name(self) -> dict[str, CommandSpec]:
        return {c.name: c for c in self.commands}

    @property
    def by_tool(self) -> dict[str, CommandSpec]:
        return {c.tool: c for c in self.commands if c.tool}

    @property
    def internal(self) -> list[CommandSpec]:
        return [c for c in self.commands if c.tool is None]

    def groups(self) -> dict[str, list[CommandSpec]]:
        out: dict[str, list[CommandSpec]] = {g: [] for g in GROUPS}
        for command in self.commands:
            out[command.group].append(command)
        return out

    def ops_files(self) -> list[str]:
        return sorted({c.ops_file for c in self.commands if c.ops_file})

    def handler_names(self) -> list[str]:
        return sorted({c.handler for c in self.commands})

    def param_names(self, command: str) -> list[str]:
        return [p.name for p in self.by_name[command].params]

    def ticks_per_second(self) -> int:
        return int(self.conventions.get("ticks_per_second", 254016000000))


@lru_cache(maxsize=1)
def _load_cached(path_str: str) -> Contract:
    raw = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return Contract.model_validate(raw)


def load_contract(root: Path | None = None, *, refresh: bool = False) -> Contract:
    """加载并校验契约文件。校验失败会直接抛 ``pydantic.ValidationError``。"""
    path = contract_path(root)
    if refresh:
        _load_cached.cache_clear()
    return _load_cached(str(path))


def clear_cache() -> None:
    _load_cached.cache_clear()
