"""配置加载：默认值、优先级、路径展开与示例文件同步（说明书 §10）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dsh_adobe_mcp.config import (
    Config,
    env_overrides,
    example_toml,
    expand_path,
    load_config,
    validate_config_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_defaults_match_spec_table():
    config = Config()
    assert config.server.log_level == "info"
    assert config.hosts.premiere.enabled is True
    assert config.hosts.premiere.version is None
    assert config.hosts.premiere.launch_on_demand is True
    assert config.hosts.premiere.launch_timeout_s == 120
    assert config.hosts.premiere.eval_timeout_s == 30
    assert config.bridge.heartbeat_s == 5
    assert config.bridge.reconnect_backoff_max_s == 30
    assert config.bridge.handshake_poll_s == 0.5
    assert config.extendscript.enable_qe is False
    assert config.extendscript.es3_static_check is True
    assert config.safety.checkpoint_before_t2 is True
    assert config.safety.max_checkpoints == 20
    assert config.safety.confirm_token_ttl_s == 300
    assert config.tools.enabled_groups == ["core", "project_edit", "timeline_edit"]
    assert config.jobs.poll_interval_s == 5
    assert config.jobs.max_concurrent_per_host == 1
    assert config.cep.extension_id == "com.dsh.adobe.bridge"


def test_default_paths_are_absolute_and_under_expected_roots():
    config = Config()
    assert config.server.log_dir.is_absolute()
    assert config.safety.checkpoint_dir.is_absolute()
    assert config.bridge.handshake_dir.is_absolute()
    assert "dsh-adobe-mcp" in str(config.server.log_dir)


def test_toml_file_is_read_and_merged(tmp_path: Path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[hosts.premiere]\nversion = \"24.0\"\n\n[safety]\nmax_checkpoints = 5\n",
        encoding="utf-8",
    )
    config = load_config(root=tmp_path, user_config=tmp_path / "missing.toml")
    assert config.hosts.premiere.version == "24.0"
    assert config.safety.max_checkpoints == 5


def test_project_config_overrides_user_config(tmp_path: Path):
    user = tmp_path / "user.toml"
    user.write_text("[server]\nlog_level = \"warning\"\n", encoding="utf-8")
    (tmp_path / "config.toml").write_text("[server]\nlog_level = \"debug\"\n", encoding="utf-8")
    config = load_config(root=tmp_path, user_config=user)
    assert config.server.log_level == "debug"
    assert str(user) in config.loaded_files


def test_env_overrides_file_and_cli_overrides_env(tmp_path: Path):
    (tmp_path / "config.toml").write_text(
        "[hosts.premiere]\neval_timeout_s = 10\n", encoding="utf-8"
    )
    env = {"DSH_ADOBE_MCP__HOSTS__PREMIERE__EVAL_TIMEOUT_S": "45"}
    assert load_config(root=tmp_path, user_config=tmp_path / "x.toml", env=env).hosts.premiere.eval_timeout_s == 45

    cli = {"hosts": {"premiere": {"eval_timeout_s": 99}}}
    config = load_config(root=tmp_path, user_config=tmp_path / "x.toml", env=env, cli_overrides=cli)
    assert config.hosts.premiere.eval_timeout_s == 99


def test_env_list_values_are_split_on_commas():
    env = {"DSH_ADOBE_MCP__TOOLS__ENABLED_GROUPS": "core,export"}
    overrides = env_overrides(env)
    assert overrides["tools"]["enabled_groups"] == ["core", "export"]
    config = load_config(root=Path.cwd() / "nonexistent-root", user_config=None, env=env)
    assert config.tools.enabled_groups == ["core", "export"]


def test_env_unknown_key_is_rejected():
    with pytest.raises(ValidationError):
        load_config(env={"DSH_ADOBE_MCP__NOPE": "1"}, user_config=None)


def test_unknown_toml_key_is_rejected(tmp_path: Path):
    (tmp_path / "config.toml").write_text("[server]\nunknown_key = 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(root=tmp_path, user_config=tmp_path / "x.toml")


def test_expand_path_handles_vars_and_user(monkeypatch):
    monkeypatch.setenv("DSH_TEST_DIR", str(REPO_ROOT))
    assert expand_path("%DSH_TEST_DIR%/sub") == REPO_ROOT / "sub"
    assert expand_path("${DSH_TEST_DIR}/sub") == REPO_ROOT / "sub"
    assert expand_path("~").is_absolute()


def test_example_toml_is_in_sync_with_model():
    expected = example_toml()
    on_disk = (REPO_ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert on_disk == expected, "config.example.toml 与配置模型不同步：运行 scripts/render_config_example.py"


def test_example_toml_roundtrips_to_defaults():
    parsed = validate_config_text(example_toml())
    assert parsed.model_dump(mode="json") == Config().model_dump(mode="json")


def test_example_toml_documents_every_leaf_field():
    text = example_toml()
    leaves: list[str] = []
    for section, model in (
        ("server", Config().server),
        ("hosts.premiere", Config().hosts.premiere),
        ("hosts.media_encoder", Config().hosts.media_encoder),
        ("bridge", Config().bridge),
        ("extendscript", Config().extendscript),
        ("safety", Config().safety),
        ("tools", Config().tools),
        ("jobs", Config().jobs),
        ("cep", Config().cep),
        ("export", Config().export),
    ):
        assert f"[{section}]" in text, f"示例配置缺少 [{section}]"
        leaves.extend(model.__class__.model_fields)
    for name in leaves:
        assert f"\n{name} = " in text, f"示例配置缺少配置项 {name}"


def test_cli_overrides_of_command_line_shape():
    from dsh_adobe_mcp.__main__ import build_parser, cli_overrides

    args = build_parser().parse_args(
        ["--log-level", "debug", "--enable-qe", "--groups", "core,export", "--host-version", "25.0"]
    )
    overrides = cli_overrides(args)
    assert overrides["server"]["log_level"] == "debug"
    assert overrides["extendscript"]["enable_qe"] is True
    assert overrides["tools"]["enabled_groups"] == ["core", "export"]
    assert overrides["hosts"]["premiere"]["version"] == "25.0"
