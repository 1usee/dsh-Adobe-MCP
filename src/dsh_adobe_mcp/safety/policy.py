"""风险分级表 —— 说明书 §8.1 的唯一落地位置。

设计要点（硬性约束 C-3）：风险等级**只在这里判定**，既不由模型在运行时决定，
也不下发到 L3（宿主内脚本）。L3 只负责执行被授权的命令。

本表覆盖全部 56 个工具（说明书 §7.2），并提供：

* 每个工具的风险等级、所属工具组、对应的桥接命令名；
* T1/T2 工具的 ``undo_hint``（§7.1 要求"如何撤销本次操作"是可执行的具体指引，而不是空话）。

``tests/unit/test_policy.py`` 断言本表与 §7.2 的工具清单一一对应；
``tests/contract/test_commands_contract.py`` 断言本表与 ``commands.json`` 的风险等级、
命令名、工具组完全一致。
"""

from __future__ import annotations

from typing import Iterator, Literal, Mapping

from pydantic import BaseModel, Field

from ..contract import GROUPS, Contract, Group, Risk, load_contract

__all__ = [
    "ToolPolicy",
    "POLICY",
    "policy_for",
    "risk_of",
    "tools_in_group",
    "enabled_tools",
    "destructive_tools",
    "bridged_tools",
    "check_against_contract",
    "DEFAULT_ENABLED_GROUPS",
]


class ToolPolicy(BaseModel):
    """单个工具的安全与归属元数据。"""

    name: str
    risk: Risk
    group: Group
    command: str | None = Field(
        default=None, description="对应的桥接命令名；None 表示纯 L1 工具"
    )
    summary: str = ""
    undo_hint: str | None = Field(
        default=None, description="T1/T2 工具的撤销指引；T0 为 None"
    )

    @property
    def bridged(self) -> bool:
        return self.command is not None

    @property
    def destructive(self) -> bool:
        """T2：必须经过确认令牌才允许执行。"""
        return self.risk == "T2"

    @property
    def writes(self) -> bool:
        return self.risk in ("T1", "T2")


def _t0(
    name: str, group: Group, command: str | None, summary: str
) -> ToolPolicy:
    return ToolPolicy(name=name, risk="T0", group=group, command=command, summary=summary)


def _t1(
    name: str, group: Group, command: str | None, summary: str, undo: str
) -> ToolPolicy:
    return ToolPolicy(
        name=name, risk="T1", group=group, command=command, summary=summary, undo_hint=undo
    )


def _t2(
    name: str, group: Group, command: str | None, summary: str, undo: str
) -> ToolPolicy:
    return ToolPolicy(
        name=name, risk="T2", group=group, command=command, summary=summary, undo_hint=undo
    )


_TOOLS: tuple[ToolPolicy, ...] = (
    # ---------------- core：始终启用（说明书 §7.3） ----------------
    _t0("host_list", "core", None, "枚举本机已安装的宿主及其版本"),
    _t0("host_status", "core", None, "报告宿主的运行与连接状态"),
    _t1(
        "host_launch",
        "core",
        None,
        "启动目标宿主进程并等待面板就绪",
        "关闭宿主进程即可回到启动前状态；本操作不修改工程。",
    ),
    _t0("host_wait_ready", "core", None, "等待面板握手并建立桥接连接"),
    _t0("host_resync", "core", "host.state", "重新读取宿主真实状态并产出差异报告"),
    _t0("host_reload_panel", "core", "panel.reload", "重载宿主内面板（开发用）"),
    _t0("ppro_app_info", "core", "app.info", "读取宿主应用信息与工程路径"),
    _t0("ppro_project_info", "core", "project.info", "读取当前工程结构概览"),
    _t0("job_list", "core", None, "列出长任务"),
    _t0("job_status", "core", None, "查询长任务状态与进度"),
    _t0("job_cancel", "core", None, "尽力取消长任务"),
    _t0("safety_confirm_operation", "core", None, "用确认令牌执行被挂起的 T2 操作"),
    _t2(
        "safety_restore_checkpoint",
        "core",
        None,
        "把工程恢复到某个检查点",
        "恢复前会为当前状态自动再建一个检查点，可用它把恢复本身回退。",
    ),
    _t0("safety_list_checkpoints", "core", None, "列出可用检查点"),
    # ---------------- project_edit ----------------
    _t2(
        "ppro_project_new",
        "project_edit",
        "project.new",
        "新建工程并保存到指定路径",
        "关闭该工程即可；原工程文件未被触碰，可在宿主中重新打开。",
    ),
    _t2(
        "ppro_project_open",
        "project_edit",
        "project.open",
        "打开磁盘上的工程",
        "重新打开先前的工程即可回到原状态（未保存改动会被 L1 提前拦下）。",
    ),
    _t1(
        "ppro_project_save",
        "project_edit",
        "project.save",
        "保存当前工程",
        "宿主撤销栈不含保存动作；如需回退内容请用 safety_list_checkpoints 选择更早的检查点。",
    ),
    _t2(
        "ppro_project_save_as",
        "project_edit",
        "project.save_as",
        "另存工程到新路径",
        "旧路径的工程文件保持原样，重新打开它即可；新路径文件需手工删除。",
    ),
    _t2(
        "ppro_project_close",
        "project_edit",
        "project.close",
        "关闭当前工程",
        "重新打开该工程文件即可；关闭前是否丢弃改动由 confirm_discard 明确指定。",
    ),
    _t1(
        "ppro_project_import",
        "project_edit",
        "project.import",
        "导入磁盘素材到工程",
        "在素材箱中选中导入的条目并删除（不勾选“从磁盘删除文件”）。",
    ),
    _t1(
        "ppro_project_bin_create",
        "project_edit",
        "project.bin_create",
        "新建素材箱",
        "在素材箱上右键 → 删除。",
    ),
    _t0("ppro_project_bin_list", "project_edit", "project.bin_list", "列出素材箱"),
    _t1(
        "ppro_project_bin_move",
        "project_edit",
        "project.bin_move",
        "移动工程条目到目标素材箱",
        "把条目移回原素材箱（原路径已由本次调用返回）。",
    ),
    _t0("ppro_project_item_find", "project_edit", "project.item_find", "查找工程条目"),
    # ---------------- timeline_edit ----------------
    _t0("ppro_app_get_state", "timeline_edit", "app.get_state", "读取当前编辑状态"),
    _t1(
        "ppro_app_set_playhead",
        "timeline_edit",
        "app.set_playhead",
        "移动播放头",
        "把播放头移回原位置（原位置已由本次调用返回）。",
    ),
    _t1(
        "ppro_sequence_create",
        "timeline_edit",
        "sequence.create",
        "新建序列",
        "在项目面板中删除该序列（不影响素材）。",
    ),
    _t1(
        "ppro_sequence_create_from_clips",
        "timeline_edit",
        "sequence.create_from_clips",
        "由素材创建序列",
        "在项目面板中删除该序列（不影响素材）。",
    ),
    # 说明：§7.2 的表格把 ppro_sequence_* 整体标为 T1；其中 list/info 实际是只读查询，
    # 因此这里保留 T1（与说明书一致，不擅自降低等级），但 undo_hint 如实写成"未改变状态"。
    _t1(
        "ppro_sequence_list",
        "timeline_edit",
        "sequence.list",
        "列出工程内序列",
        "只读查询，未改变宿主任何状态，无需撤销。",
    ),
    _t1(
        "ppro_sequence_info",
        "timeline_edit",
        "sequence.info",
        "读取序列详情",
        "只读查询，未改变宿主任何状态，无需撤销。",
    ),
    _t1(
        "ppro_sequence_set_active",
        "timeline_edit",
        "sequence.set_active",
        "切换活动序列",
        "把先前的活动序列重新设为活动序列。",
    ),
    _t1(
        "ppro_sequence_set_in_out",
        "timeline_edit",
        "sequence.set_in_out",
        "设置序列入出点",
        "用返回的原入出点再次调用本工具恢复。",
    ),
    _t1(
        "ppro_sequence_set_work_area",
        "timeline_edit",
        "sequence.set_work_area",
        "设置序列工作区",
        "用返回的原工作区范围再次调用本工具恢复。",
    ),
    _t0("ppro_timeline_list_clips", "timeline_edit", "timeline.list_clips", "列出时间线片段"),
    _t0("ppro_timeline_clip_info", "timeline_edit", "timeline.clip_info", "读取片段详情"),
    _t1(
        "ppro_timeline_insert_clip",
        "timeline_edit",
        "timeline.insert_clip",
        "插入素材到时间线",
        "宿主撤销（Ctrl+Z）可撤销本次插入；或删除返回的 ref 对应片段。",
    ),
    _t1(
        "ppro_timeline_overwrite_clip",
        "timeline_edit",
        "timeline.overwrite_clip",
        "覆盖写入素材到时间线",
        "宿主撤销（Ctrl+Z）可恢复被覆盖的内容。",
    ),
    _t1(
        "ppro_timeline_move_clip",
        "timeline_edit",
        "timeline.move_clip",
        "移动时间线片段",
        "用返回的原起点再次调用本工具移动回原位。",
    ),
    _t1(
        "ppro_timeline_trim_clip",
        "timeline_edit",
        "timeline.trim_clip",
        "裁切时间线片段",
        "用返回的原起止点再次调用本工具恢复。",
    ),
    _t2(
        "ppro_timeline_remove_clip",
        "timeline_edit",
        "timeline.remove_clip",
        "从时间线删除片段",
        "宿主撤销（Ctrl+Z）可恢复；工程素材本身未被删除。",
    ),
    _t2(
        "ppro_timeline_razor",
        "timeline_edit",
        "timeline.razor",
        "在指定时间点切分片段",
        "撤销切分：选中两个半段 → 右键 → 删除，再从素材箱重新插入；或宿主撤销栈回退。",
    ),
    _t1(
        "ppro_timeline_add_track",
        "timeline_edit",
        "timeline.add_track",
        "新增轨道",
        "用 ppro_timeline_remove_track 删除新增的轨道（轨道序号已由本次调用返回）。",
    ),
    _t2(
        "ppro_timeline_remove_track",
        "timeline_edit",
        "timeline.remove_track",
        "删除轨道及其片段",
        "宿主撤销栈通常不可覆盖轨道删除；请用 safety_list_checkpoints 恢复检查点。",
    ),
    _t1(
        "ppro_timeline_add_marker",
        "timeline_edit",
        "timeline.add_marker",
        "添加序列标记",
        "在标记面板删除该标记（索引已由本次调用返回）。",
    ),
    _t0("ppro_timeline_list_markers", "timeline_edit", "timeline.list_markers", "列出序列标记"),
    # ---------------- effects ----------------
    _t0("ppro_effects_list_available", "effects", "effects.list_available", "列出可用效果"),
    _t1(
        "ppro_effects_apply",
        "effects",
        "effects.apply",
        "给片段施加效果",
        "在效果控件中选中该效果 → 删除；宿主撤销栈亦可回退。",
    ),
    _t0("ppro_effects_get_params", "effects", "effects.get_params", "读取效果参数"),
    _t1(
        "ppro_effects_set_param",
        "effects",
        "effects.set_param",
        "设置效果参数",
        "用返回的原值再次调用本工具恢复。",
    ),
    _t1(
        "ppro_effects_apply_transition",
        "effects",
        "effects.apply_transition",
        "给片段添加转场",
        "在时间线上选中转场 → 删除；宿主撤销栈亦可回退。",
    ),
    # ---------------- media ----------------
    _t0("ppro_media_probe", "media", "media.probe", "探测素材元数据"),
    _t1(
        "ppro_media_relink",
        "media",
        "media.relink",
        "重新链接离线素材",
        "用返回的原路径再次调用本工具即可回退链接。",
    ),
    _t1(
        "ppro_media_generate_proxies",
        "media",
        "media.generate_proxies",
        "生成并附加代理",
        "在素材上右键 → 代理 → 取消链接代理；生成的代文件需手工删除。",
    ),
    # ---------------- export ----------------
    _t0("ppro_export_list_presets", "export", None, "列出可用的编码预设"),
    _t2(
        "ppro_export_encode_sequence",
        "export",
        "export.encode_sequence",
        "导出序列",
        "删除输出文件即可回退；工程本身不被修改。",
    ),
    _t2(
        "ppro_export_encode_file",
        "export",
        "export.encode_file",
        "导出单个素材",
        "删除输出文件即可回退；工程本身不被修改。",
    ),
)

POLICY: Mapping[str, ToolPolicy] = {tool.name: tool for tool in _TOOLS}

if len(POLICY) != len(_TOOLS):  # pragma: no cover - 属于代码错误而非输入错误
    raise RuntimeError("风险表中存在重名工具")

#: 说明书 §7.3：默认启用核心组 + 两条主要生产链路。
DEFAULT_ENABLED_GROUPS: tuple[Group, ...] = ("core", "project_edit", "timeline_edit")


def policy_for(tool: str) -> ToolPolicy:
    try:
        return POLICY[tool]
    except KeyError as exc:  # pragma: no cover - 由测试保证不触发
        raise KeyError(f"工具不在风险表中: {tool}") from exc


def risk_of(tool: str) -> Risk:
    return policy_for(tool).risk


def tools_in_group(group: Group) -> list[str]:
    return [t.name for t in _TOOLS if t.group == group]


def enabled_tools(groups: list[str] | tuple[str, ...]) -> list[str]:
    """按工具组解析出启用的工具名，保持稳定顺序（契约中的声明顺序）。"""
    wanted = set(groups)
    unknown = wanted - set(GROUPS)
    if unknown:
        raise ValueError(f"未知的工具组: {sorted(unknown)}")
    return [t.name for t in _TOOLS if t.group in wanted]


def destructive_tools() -> list[str]:
    return [t.name for t in _TOOLS if t.destructive]


def bridged_tools() -> list[str]:
    return [t.name for t in _TOOLS if t.bridged]


def iter_policies() -> Iterator[ToolPolicy]:
    return iter(_TOOLS)


def check_against_contract(contract: Contract | None = None) -> list[str]:
    """返回 L1 风险表与命令契约之间的**全部**不一致（空列表表示一致）。

    契约测试调用它并把返回值直接作为失败信息，便于定位漂移。
    """
    contract = contract or load_contract()
    problems: list[str] = []
    contract_tools = contract.by_tool

    # 方向一：policy 中声明为桥接的工具，必须在契约中存在且完全对齐。
    for policy in _TOOLS:
        if not policy.bridged:
            continue
        command = contract_tools.get(policy.name)
        if command is None:
            problems.append(
                f"policy 声明 {policy.name} 走桥接命令 {policy.command}，但契约中没有该工具"
            )
            continue
        if command.name != policy.command:
            problems.append(
                f"{policy.name} 的命令名不一致：policy={policy.command} contract={command.name}"
            )
        if command.risk != policy.risk:
            problems.append(
                f"{policy.name} 的风险等级不一致：policy={policy.risk} contract={command.risk}"
            )
        if command.group != policy.group:
            problems.append(
                f"{policy.name} 的工具组不一致：policy={policy.group} contract={command.group}"
            )

    # 方向二：契约中声明了工具的命令，在 policy 中必须存在且同样标记为桥接。
    for command in contract.commands:
        if command.tool is None:
            continue
        policy = POLICY.get(command.tool)
        if policy is None:
            problems.append(f"契约声明工具 {command.tool}（命令 {command.name}），但 policy 中没有它")
        elif not policy.bridged:
            problems.append(f"契约把 {command.tool} 声明为桥接工具，但 policy 中它没有命令")
    return problems
