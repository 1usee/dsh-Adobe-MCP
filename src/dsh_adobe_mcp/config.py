"""配置加载与校验（说明书 §10）。

优先级：**命令行参数 > 环境变量 > 配置文件 > 内置默认值**。

* 配置文件位置：``%APPDATA%\\dsh-adobe-mcp\\config.toml``（用户级），
  仓库根目录的 ``config.toml`` 作为开发期覆盖，优先级高于用户级。
* 格式为 **TOML**：Python ≥3.11 的标准库 ``tomllib`` 可直接读取，零依赖；
  且与 ``pyproject.toml`` 全项目统一一种格式（说明书 §10 的选择理由）。
* 环境变量形式：``DSH_ADOBE_MCP__<路径>``，路径用 ``__`` 分隔，
  例如 ``DSH_ADOBE_MCP__HOSTS__PREMIERE__EVAL_TIMEOUT_S=60``。
* 列表值在环境变量里用逗号分隔。

``config.example.toml`` 由 :func:`example_toml` 生成，``tests/unit/test_config.py``
断言它与配置模型保持同步——模型加了键却忘了更新示例文件会直接判失败。
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import SERVER_NAME
from .contract import repo_root

__all__ = [
    "Config",
    "load_config",
    "example_toml",
    "EXPECTED_ENV_PREFIX",
    "default_log_dir",
    "default_checkpoint_dir",
    "default_handshake_dir",
]

ENV_PREFIX = "DSH_ADOBE_MCP__"
EXPECTED_ENV_PREFIX = ENV_PREFIX

_VAR_PATTERN = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: 这些字段在环境变量里用逗号分隔的列表表示。
LIST_FIELDS = frozenset({"enabled_groups", "allowed_path_roots", "preset_dirs", "log_levels"})


# --------------------------------------------------------------------------
# 路径工具
# --------------------------------------------------------------------------
def expand_path(value: str | Path, *, base: Path | None = None) -> Path:
    """展开 ``%VAR%`` / ``${VAR}`` / ``~``，返回绝对路径。

    未定义的变量保持原样（不静默丢弃），后续由使用方报错，避免"路径悄悄变了"。
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return os.environ.get(name, match.group(0))

    text = str(value)
    text = _VAR_PATTERN.sub(_replace, text)
    text = os.path.expanduser(text)
    path = Path(text)
    if not path.is_absolute() and base is not None:
        path = base / path
    return path


def _localappdata() -> Path:
    raw = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def default_log_dir() -> Path:
    return _localappdata() / SERVER_NAME / "logs"


def default_checkpoint_dir() -> Path:
    return _localappdata() / SERVER_NAME / "checkpoints"


def default_jobs_dir() -> Path:
    return _localappdata() / SERVER_NAME / "jobs"


def default_handshake_dir() -> Path:
    raw = os.environ.get("TEMP") or os.environ.get("TMPDIR")
    base = Path(raw) if raw else Path("/tmp")
    return base / SERVER_NAME


def default_cep_extensions_dir() -> Path:
    raw = os.environ.get("APPDATA")
    base = Path(raw) if raw else Path.home() / "AppData" / "Roaming"
    return base / "Adobe" / "CEP" / "extensions"


def default_preset_dirs() -> list[Path]:
    """AME 预设目录（同一台机器可能装了多个版本）。

    说明书没有为预设列出注册表位置，这里按 Adobe 的安装约定做目录扫描；
    扫不到时返回空列表，``ppro_export_list_presets`` 会明确报告"未发现预设目录"
    而不是假装没有预设。
    """
    bases: list[Path] = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        raw = os.environ.get(env_name)
        if raw:
            bases.append(Path(raw) / "Adobe")
    found: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        for child in sorted(base.glob("Adobe Media Encoder*")):
            presets = child / "Presets"
            if presets.is_dir() and presets not in found:
                found.append(presets)
    return found


# --------------------------------------------------------------------------
# 配置模型
# --------------------------------------------------------------------------
class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_level: Literal["debug", "info", "warning", "error"] = "info"
    log_dir: Path = Field(default_factory=default_log_dir)
    console_log_level: Literal["none", "error", "warning", "info", "debug"] = Field(
        default="none",
        description="标准输出只允许协议使用（stdio 传输），因此默认不向控制台打日志；"
        "调试时可设为 info，日志走 stderr。",
    )

    @field_validator("log_dir", mode="before")
    @classmethod
    def _expand(cls, value: Any) -> Any:
        return expand_path(value) if isinstance(value, (str, Path)) else value


class PremiereHostConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    version: str | None = Field(default=None, description="空表示自动选最高版本")
    launch_on_demand: bool = True
    launch_timeout_s: float = 120
    eval_timeout_s: float = 30

    @field_validator("version", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        # TOML 没有 null，示例配置里用空串表示"未指定版本"
        if isinstance(value, str) and not value.strip():
            return None
        return value


class MediaEncoderHostConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    version: str | None = None
    launch_on_demand: bool = False
    launch_timeout_s: float = 120
    eval_timeout_s: float = 30

    @field_validator("version", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class HostsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    premiere: PremiereHostConfig = Field(default_factory=PremiereHostConfig)
    media_encoder: MediaEncoderHostConfig = Field(default_factory=MediaEncoderHostConfig)


class BridgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handshake_dir: Path = Field(default_factory=default_handshake_dir)
    heartbeat_s: float = 5
    reconnect_backoff_max_s: float = 30
    # 说明书 §5.1 明确"以固定间隔轮询该目录（默认 0.5 秒）"，因此把轮询间隔显式配置化。
    handshake_poll_s: float = 0.5
    connect_timeout_s: float = 5
    auth_timeout_s: float = 3
    max_response_chars: int = 100_000

    @field_validator("handshake_dir", mode="before")
    @classmethod
    def _expand(cls, value: Any) -> Any:
        return expand_path(value) if isinstance(value, (str, Path)) else value


class ExtendScriptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enable_qe: bool = Field(
        default=False, description="启用未公开的 QE 接口；默认禁用（说明书 §6.2）"
    )
    es3_static_check: bool = True


class SafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_before_t2: bool = True
    checkpoint_dir: Path = Field(default_factory=default_checkpoint_dir)
    max_checkpoints: int = 20
    confirm_token_ttl_s: float = 300
    allowed_path_roots: list[Path] = Field(
        default_factory=lambda: [Path.home()],
        description="导入/导出等路径参数的允许根目录；工程目录会在打开工程时自动加入。",
    )

    @field_validator("checkpoint_dir", mode="before")
    @classmethod
    def _expand(cls, value: Any) -> Any:
        return expand_path(value) if isinstance(value, (str, Path)) else value

    @field_validator("allowed_path_roots", mode="before")
    @classmethod
    def _expand_roots(cls, value: Any) -> Any:
        if isinstance(value, (str, Path)):
            return [expand_path(value)]
        if isinstance(value, list):
            return [expand_path(item) if isinstance(item, (str, Path)) else item for item in value]
        return value


class ToolsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled_groups: list[str] = Field(
        default_factory=lambda: ["core", "project_edit", "timeline_edit"]
    )


class JobsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_interval_s: float = 5
    max_concurrent_per_host: int = 1
    max_queued_per_host: int = 8
    state_dir: Path = Field(default_factory=default_jobs_dir)

    @field_validator("state_dir", mode="before")
    @classmethod
    def _expand(cls, value: Any) -> Any:
        return expand_path(value) if isinstance(value, (str, Path)) else value


class CepConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extension_id: str = "com.dsh.adobe.bridge"
    source_dir: Path = Field(default_factory=lambda: repo_root() / "cep-extension")
    extensions_dir: Path = Field(default_factory=default_cep_extensions_dir)
    backup_dir: Path = Field(default_factory=lambda: _localappdata() / SERVER_NAME / "cep-backup")
    set_player_debug_mode: bool = Field(
        default=False,
        description="是否在部署时设置 HKCU\\Software\\Adobe\\CSXS.<n>\\PlayerDebugMode。"
        "这是 Adobe 官方的扩展开发设置，只影响未签名扩展的加载，与宿主授权无关（说明书 §1.3）；"
        "默认 false，需要时由 scripts/dev_install_cep.ps1 显式设置。",
    )
    script_timeout_s: float = 30

    @field_validator("source_dir", "extensions_dir", "backup_dir", mode="before")
    @classmethod
    def _expand(cls, value: Any) -> Any:
        return expand_path(value) if isinstance(value, (str, Path)) else value


class ExportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_dirs: list[Path] = Field(default_factory=default_preset_dirs)

    @field_validator("preset_dirs", mode="before")
    @classmethod
    def _expand(cls, value: Any) -> Any:
        if isinstance(value, (str, Path)):
            return [expand_path(value)]
        if isinstance(value, list):
            return [expand_path(item) if isinstance(item, (str, Path)) else item for item in value]
        return value


class Config(BaseModel):
    """根配置。字段与说明书 §10 的表格一一对应。"""

    model_config = ConfigDict(extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    hosts: HostsConfig = Field(default_factory=HostsConfig)
    bridge: BridgeConfig = Field(default_factory=BridgeConfig)
    extendscript: ExtendScriptConfig = Field(default_factory=ExtendScriptConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    cep: CepConfig = Field(default_factory=CepConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)

    #: 诊断信息：本次配置实际读取了哪些文件（不属于配置项，不出现在示例 TOML 中）。
    loaded_files: list[str] = Field(default_factory=list)

    # ---- 便捷访问 -------------------------------------------------------
    @property
    def eval_timeout_s(self) -> float:
        return self.hosts.premiere.eval_timeout_s

    def user_config_path(self) -> Path:
        raw = os.environ.get("APPDATA")
        base = Path(raw) if raw else Path.home() / "AppData" / "Roaming"
        return base / SERVER_NAME / "config.toml"

    def to_toml_dict(self) -> dict[str, Any]:
        """导出为可写回 TOML 的普通字典。"""
        data = self.model_dump(mode="json", exclude={"loaded_files"})
        return data


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------
def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value  # type: ignore[assignment]
    return base


def _set_path(data: dict[str, Any], path: list[str], value: Any) -> None:
    cursor = data
    for part in path[:-1]:
        node = cursor.get(part)
        if not isinstance(node, dict):
            node = {}
            cursor[part] = node
        cursor = node
    raw = value
    if path and path[-1] in LIST_FIELDS and isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    cursor[path[-1]] = raw


def env_overrides(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """把 ``DSH_ADOBE_MCP__A__B=...`` 形式的环境变量解析成嵌套字典。"""
    env = env if env is not None else os.environ
    data: dict[str, Any] = {}
    for key, value in env.items():
        if not key.startswith(ENV_PREFIX):
            continue
        parts = [p.lower() for p in key[len(ENV_PREFIX) :].split("__") if p]
        if not parts:
            continue
        _set_path(data, parts, value)
    return data


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_config(
    *,
    cli_overrides: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    root: Path | None = None,
    user_config: Path | None = None,
    extra_config_files: list[Path] | None = None,
) -> Config:
    """按优先级合并各来源并校验。

    :raises pydantic.ValidationError: 配置内容非法（例如未知的键）。
    """
    root = root or repo_root()
    data: dict[str, Any] = {}
    loaded: list[str] = []

    candidates: list[Path] = [user_config or Config().user_config_path()]
    project_config = root / "config.toml"
    candidates.append(project_config)
    if extra_config_files:
        candidates.extend(extra_config_files)

    for candidate in candidates:
        if candidate and candidate.is_file():
            _deep_merge(data, _read_toml(candidate))
            loaded.append(str(candidate))

    _deep_merge(data, env_overrides(env))
    if cli_overrides:
        _deep_merge(data, cli_overrides)

    config = Config.model_validate(data)
    config.loaded_files = loaded
    return config


# --------------------------------------------------------------------------
# 示例配置文件
# --------------------------------------------------------------------------
_EXAMPLE_HEADER = f"""\
# {SERVER_NAME} 配置示例
#
# 使用方法：把本文件复制到 %APPDATA%\\{SERVER_NAME}\\config.toml
#           （仓库根目录的 config.toml 会覆盖用户级配置，仅用于开发期）
#
# 优先级：命令行参数 > 环境变量 > 配置文件 > 内置默认值
# 环境变量形式：DSH_ADOBE_MCP__<大写路径，用 __ 分隔>
#           例如 DSH_ADOBE_MCP__HOSTS__PREMIERE__EVAL_TIMEOUT_S=60
#           列表值用逗号分隔，例如 DSH_ADOBE_MCP__TOOLS__ENABLED_GROUPS=core,export
#
# 本文件由 python scripts/render_config_example.py 生成，与配置模型保持同步；
# 手改本文件会导致 tests/unit/test_config.py 失败。
"""


def _format_value(value: Any) -> str:
    if value is None:
        # TOML 没有 null：可选字符串字段用空串表示"未设置"（例如 hosts.*.version 表示自动选最高）
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        inner = ", ".join(_format_value(item) for item in value)
        return f"[{inner}]"
    return json.dumps(str(value), ensure_ascii=False)


_SECTION_COMMENTS: dict[tuple[str, ...], str] = {
    ("server",): "服务器日志（stdio 传输下标准输出归协议使用，因此日志只写文件）",
    ("hosts", "premiere"): "Premiere Pro 宿主",
    ("hosts", "media_encoder"): "Media Encoder 宿主（M5 阶段使用）",
    ("bridge",): "桥接：握手目录、心跳与重连",
    ("extendscript",): "ExtendScript 方言与 QE 开关",
    ("safety",): "安全：检查点、确认令牌、路径白名单",
    ("tools",): "工具组分段启用（说明书 §7.3）",
    ("jobs",): "长任务模型",
    ("cep",): "CEP 面板部署",
    ("export",): "导出预设搜索路径",
}


def _section_comment(path: tuple[str, ...]) -> str | None:
    return _SECTION_COMMENTS.get(path)


def example_toml() -> str:
    """生成与模型同步的示例配置文本。"""
    defaults = Config()
    lines = [_EXAMPLE_HEADER]

    def emit(model: BaseModel, path: tuple[str, ...]) -> None:
        lines.append(f"[{'.'.join(path)}]" if path else "")
        comment = _SECTION_COMMENTS.get(path)
        if comment:
            lines.append(f"# {comment}")
        for name, field in model.__class__.model_fields.items():
            annotation = field.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                continue
            value = getattr(model, name)
            if field.description:
                lines.append(f"# {field.description}")
            lines.append(f"{name} = {_format_value(value)}")
        lines.append("")

    emit(defaults.server, ("server",))
    emit(defaults.hosts.premiere, ("hosts", "premiere"))
    emit(defaults.hosts.media_encoder, ("hosts", "media_encoder"))
    emit(defaults.bridge, ("bridge",))
    emit(defaults.extendscript, ("extendscript",))
    emit(defaults.safety, ("safety",))
    emit(defaults.tools, ("tools",))
    emit(defaults.jobs, ("jobs",))
    emit(defaults.cep, ("cep",))
    emit(defaults.export, ("export",))

    return "\n".join(lines).strip("\n") + "\n"


def validate_config_text(text: str) -> Config:
    """校验一段 TOML 文本（供测试与 ``--check-config`` 使用）。"""
    return Config.model_validate(tomllib.loads(text))


__all__ += ["expand_path", "env_overrides", "validate_config_text", "default_preset_dirs"]
