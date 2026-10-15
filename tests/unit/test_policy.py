"""风险分级表与工具清单（说明书 §7.2、§7.3、§8.1）。"""

from __future__ import annotations

import pytest

from dsh_adobe_mcp.safety.policy import (
    DEFAULT_ENABLED_GROUPS,
    POLICY,
    enabled_tools,
    policy_for,
    tools_in_group,
)

#: 说明书 §7.2 的工具清单 —— 逐字誊写，作为"实现与说明书一致"的独立依据。
SPEC_TOOLS: dict[str, list[str]] = {
    "host_": ["list", "status", "launch", "wait_ready", "resync", "reload_panel"],
    "ppro_app_": ["info", "get_state", "set_playhead"],
    "ppro_project_": [
        "new",
        "open",
        "save",
        "save_as",
        "close",
        "info",
        "import",
        "bin_create",
        "bin_list",
        "bin_move",
        "item_find",
    ],
    "ppro_sequence_": [
        "create",
        "create_from_clips",
        "list",
        "info",
        "set_active",
        "set_in_out",
        "set_work_area",
    ],
    "ppro_timeline_": [
        "list_clips",
        "clip_info",
        "insert_clip",
        "overwrite_clip",
        "move_clip",
        "trim_clip",
        "remove_clip",
        "razor",
        "add_track",
        "remove_track",
        "add_marker",
        "list_markers",
    ],
    "ppro_effects_": ["list_available", "apply", "get_params", "set_param", "apply_transition"],
    "ppro_media_": ["probe", "relink", "generate_proxies"],
    "ppro_export_": ["list_presets", "encode_sequence", "encode_file"],
    "job_": ["list", "status", "cancel"],
    "safety_": ["confirm_operation", "restore_checkpoint", "list_checkpoints"],
}


def spec_tool_names() -> set[str]:
    return {f"{prefix}{name}" for prefix, names in SPEC_TOOLS.items() for name in names}


def test_spec_tool_count_is_56():
    assert len(spec_tool_names()) == 56


def test_policy_covers_spec_exactly():
    assert set(POLICY) == spec_tool_names()


def test_groups_match_spec_sizes():
    assert len(tools_in_group("core")) == 14
    assert len(tools_in_group("project_edit")) == 10
    assert len(tools_in_group("timeline_edit")) == 21
    assert len(tools_in_group("effects")) == 5
    assert len(tools_in_group("media")) == 3
    assert len(tools_in_group("export")) == 3


def test_core_group_is_exactly_the_14_declared_tools():
    """说明书 §7.3 明确列出核心组的 14 个工具。"""
    assert set(tools_in_group("core")) == {
        "host_list",
        "host_status",
        "host_launch",
        "host_wait_ready",
        "host_resync",
        "host_reload_panel",
        "ppro_app_info",
        "ppro_project_info",
        "job_list",
        "job_status",
        "job_cancel",
        "safety_confirm_operation",
        "safety_restore_checkpoint",
        "safety_list_checkpoints",
    }


def test_default_enabled_groups_per_spec():
    assert DEFAULT_ENABLED_GROUPS == ("core", "project_edit", "timeline_edit")
    names = enabled_tools(list(DEFAULT_ENABLED_GROUPS))
    assert len(names) == 45
    # 默认不开的组：effects / media / export
    assert "ppro_effects_apply" not in names
    assert "ppro_export_encode_sequence" not in names
    assert "ppro_media_probe" not in names


def test_every_tool_has_risk_and_summary():
    for name, policy in POLICY.items():
        assert policy.risk in ("T0", "T1", "T2"), name
        assert policy.summary, f"{name} 缺少说明"
        if policy.risk == "T0":
            assert policy.undo_hint is None, f"{name} 是只读工具，不应有撤销指引"
        else:
            assert policy.undo_hint, f"{name} 是写操作，必须给出可执行的撤销指引"
            assert len(policy.undo_hint) > 8, f"{name} 的撤销指引过于笼统"


def test_t2_set_matches_spec_table():
    """§7.2 的破坏性工具集合。"""
    assert {name for name, policy in POLICY.items() if policy.risk == "T2"} == {
        "ppro_project_new",
        "ppro_project_open",
        "ppro_project_save_as",
        "ppro_project_close",
        "ppro_timeline_remove_clip",
        "ppro_timeline_razor",
        "ppro_timeline_remove_track",
        "ppro_export_encode_sequence",
        "ppro_export_encode_file",
        "safety_restore_checkpoint",
    }


def test_read_tools_are_t0():
    for name in (
        "host_list",
        "host_status",
        "host_resync",
        "ppro_app_info",
        "ppro_project_info",
        "ppro_project_bin_list",
        "ppro_project_item_find",
        "ppro_timeline_list_clips",
        "ppro_timeline_clip_info",
        "ppro_timeline_list_markers",
        "ppro_effects_list_available",
        "ppro_effects_get_params",
        "ppro_media_probe",
        "ppro_export_list_presets",
        "job_list",
        "job_status",
        "job_cancel",
        "safety_list_checkpoints",
    ):
        assert policy_for(name).risk == "T0", name


def test_qe_tools_are_destructive_or_flagged():
    """依赖未公开接口的工具必须是 T2 或至少让调用者能分辨（说明书 §7.2 注）。"""
    from dsh_adobe_mcp.contract import load_contract

    contract = load_contract()
    for command in contract.commands:
        if command.capability == "qe":
            assert command.undocumented is True
            assert command.tool is None or POLICY[command.tool].risk in ("T1", "T2")


def test_unknown_group_is_rejected():
    with pytest.raises(ValueError):
        enabled_tools(["core", "not_a_group"])
