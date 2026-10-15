# L3 命令契约（cep-extension/jsx/commands.json）

> 本文件由 `python scripts/gen_commands_doc.py` 自动生成，请勿手工编辑。
> 这是 L1 与面板之间的唯一真源；`dispatch.jsx`（L3 路由表）由它生成，处理器实现位于 `cep-extension/jsx/ops/*.jsx`。
> 一致性由 `tests/contract/test_tool_docs.py` 强制校验。

## 约定

- **handler_signature**：function <handler>(args, ctx) —— args 为命令参数对象，ctx 由 dispatch.jsx 注入。
- **ctx_fields**：
  - `ctx.traceId (string)`
  - `ctx.dryRun (boolean)`
  - `ctx.qeEnabled (boolean)`
  - `ctx.command (string)`
  - `ctx.idempotencyKey (string|null)`
- **dry_run_contract**：dry_run=true 时处理器只做前置条件校验，不触碰宿主 DOM，返回 {"dry_run": true, "actions": [<string>...], "checks": [{"name":..., "ok":bool, "detail":...}]}；结构必须与真实执行一致（同样的键），便于客户端复用解析逻辑。
- **time_units**：所有以 _s 结尾的参数与返回值均为秒（number）。L3 内部按需转换为宿主 tick（254016000000 ticks/秒）。
- **clip_ref_format**：clip ref 形如 "V:1:0" / "A:2:3"，即 <V|A>:<轨道序号，1 基>:<轨道内片段序号，0 基>。由 timeline.list_clips 返回，供其他 timeline/effects 命令引用。
- **item_ref_format**：project item 引用为树路径（如 "\\Root\\Bin\\clip.mp4"）或 nodeId 字符串；L3 先按路径匹配，再按 nodeId 匹配。
- **error_shape**：失败时处理器抛出异常，由 wrap.jsx 统一捕获为 {code, message, line, file, command, script_excerpt, args_snapshot}。
- **ticks_per_second**：254016000000

契约 schema_version：`1`；命令总数：**48**

## 命令汇总

| 命令 | 工具 | 组 | 执行器 | 风险 | 能力 | 幂等 | dry-run | 长任务 |
|---|---|---|---|---|---|---|---|---|
| `host.ping` | —（内部） | core | node | T0 | public | 是 | 否 | 否 |
| `host.capabilities` | —（内部） | core | node | T0 | public | 是 | 否 | 否 |
| `panel.reload` | `host_reload_panel` | core | node | T0 | public | 否 | 否 | 否 |
| `host.state` | `host_resync` | core | jsx | T0 | public | 是 | 否 | 否 |
| `app.info` | `ppro_app_info` | core | jsx | T0 | public | 是 | 否 | 否 |
| `app.get_state` | `ppro_app_get_state` | timeline_edit | jsx | T0 | public_or_qe | 是 | 否 | 否 |
| `app.set_playhead` | `ppro_app_set_playhead` | timeline_edit | jsx | T1 | public | 是 | 是 | 否 |
| `project.info` | `ppro_project_info` | core | jsx | T0 | public | 是 | 否 | 否 |
| `project.new` | `ppro_project_new` | project_edit | jsx | T2 | public | 否 | 是 | 否 |
| `project.open` | `ppro_project_open` | project_edit | jsx | T2 | public | 否 | 是 | 否 |
| `project.save` | `ppro_project_save` | project_edit | jsx | T1 | public | 是 | 是 | 否 |
| `project.save_as` | `ppro_project_save_as` | project_edit | jsx | T2 | public | 否 | 是 | 否 |
| `project.close` | `ppro_project_close` | project_edit | jsx | T2 | public | 否 | 是 | 否 |
| `project.import` | `ppro_project_import` | project_edit | jsx | T1 | public | 否 | 是 | 否 |
| `project.bin_create` | `ppro_project_bin_create` | project_edit | jsx | T1 | public | 否 | 是 | 否 |
| `project.bin_list` | `ppro_project_bin_list` | project_edit | jsx | T0 | public | 是 | 否 | 否 |
| `project.bin_move` | `ppro_project_bin_move` | project_edit | jsx | T1 | public | 否 | 是 | 否 |
| `project.item_find` | `ppro_project_item_find` | project_edit | jsx | T0 | public | 是 | 否 | 否 |
| `sequence.create` | `ppro_sequence_create` | timeline_edit | jsx | T1 | public | 否 | 是 | 否 |
| `sequence.create_from_clips` | `ppro_sequence_create_from_clips` | timeline_edit | jsx | T1 | public | 否 | 是 | 否 |
| `sequence.list` | `ppro_sequence_list` | timeline_edit | jsx | T1 | public | 是 | 否 | 否 |
| `sequence.info` | `ppro_sequence_info` | timeline_edit | jsx | T1 | public | 是 | 否 | 否 |
| `sequence.set_active` | `ppro_sequence_set_active` | timeline_edit | jsx | T1 | public | 是 | 是 | 否 |
| `sequence.set_in_out` | `ppro_sequence_set_in_out` | timeline_edit | jsx | T1 | public | 是 | 是 | 否 |
| `sequence.set_work_area` | `ppro_sequence_set_work_area` | timeline_edit | jsx | T1 | public_or_qe | 是 | 是 | 否 |
| `timeline.list_clips` | `ppro_timeline_list_clips` | timeline_edit | jsx | T0 | public | 是 | 否 | 否 |
| `timeline.clip_info` | `ppro_timeline_clip_info` | timeline_edit | jsx | T0 | public | 是 | 否 | 否 |
| `timeline.insert_clip` | `ppro_timeline_insert_clip` | timeline_edit | jsx | T1 | public | 否 | 是 | 否 |
| `timeline.overwrite_clip` | `ppro_timeline_overwrite_clip` | timeline_edit | jsx | T1 | public | 否 | 是 | 否 |
| `timeline.move_clip` | `ppro_timeline_move_clip` | timeline_edit | jsx | T1 | public_or_qe | 否 | 是 | 否 |
| `timeline.trim_clip` | `ppro_timeline_trim_clip` | timeline_edit | jsx | T1 | public_or_qe | 否 | 是 | 否 |
| `timeline.remove_clip` | `ppro_timeline_remove_clip` | timeline_edit | jsx | T2 | public | 否 | 是 | 否 |
| `timeline.razor` | `ppro_timeline_razor` | timeline_edit | jsx | T2 | qe | 否 | 是 | 否 |
| `timeline.add_track` | `ppro_timeline_add_track` | timeline_edit | jsx | T1 | public_or_qe | 否 | 是 | 否 |
| `timeline.remove_track` | `ppro_timeline_remove_track` | timeline_edit | jsx | T2 | public_or_qe | 否 | 是 | 否 |
| `timeline.add_marker` | `ppro_timeline_add_marker` | timeline_edit | jsx | T1 | public | 否 | 是 | 否 |
| `timeline.list_markers` | `ppro_timeline_list_markers` | timeline_edit | jsx | T0 | public | 是 | 否 | 否 |
| `effects.list_available` | `ppro_effects_list_available` | effects | jsx | T0 | public | 是 | 否 | 否 |
| `effects.apply` | `ppro_effects_apply` | effects | jsx | T1 | qe | 否 | 是 | 否 |
| `effects.get_params` | `ppro_effects_get_params` | effects | jsx | T0 | public | 是 | 否 | 否 |
| `effects.set_param` | `ppro_effects_set_param` | effects | jsx | T1 | public | 是 | 是 | 否 |
| `effects.apply_transition` | `ppro_effects_apply_transition` | effects | jsx | T1 | qe | 否 | 是 | 否 |
| `media.probe` | `ppro_media_probe` | media | jsx | T0 | public | 是 | 否 | 否 |
| `media.relink` | `ppro_media_relink` | media | jsx | T1 | public | 是 | 是 | 否 |
| `media.generate_proxies` | `ppro_media_generate_proxies` | media | jsx | T1 | public_or_qe | 否 | 是 | 是 |
| `export.encode_sequence` | `ppro_export_encode_sequence` | export | jsx | T2 | public | 否 | 是 | 是 |
| `export.encode_file` | `ppro_export_encode_file` | export | jsx | T2 | public | 否 | 是 | 是 |
| `export.job_status` | —（内部） | core | jsx | T0 | public | 是 | 否 | 否 |

## 命令详情

### `host.ping`

- 工具：—（内部命令，不直接暴露）
- 工具组：`core`；风险等级：T0
- 执行器：`node`
- 实现：面板 Node 侧 `handlePing`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：面板存活探测（Node 侧），返回面板版本与运行时长。
- 返回：object: {pong: true, panel_version, uptime_ms}

_无参数。_

### `host.capabilities`

- 工具：—（内部命令，不直接暴露）
- 工具组：`core`；风险等级：T0
- 执行器：`node`
- 实现：面板 Node 侧 `handleCapabilities`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：上报面板能力（Node 版本、CEP 版本、mixed-context、QE 可用性、evalScript 返回长度上限）。
- 返回：object: {node_version, cep_version, mixed_context, qe_enabled, eval_max_chars, host_app_id}

_无参数。_

### `panel.reload`

- 工具：`host_reload_panel`
- 工具组：`core`；风险等级：T0
- 执行器：`node`
- 实现：面板 Node 侧 `handleReload`
- 能力：`public`
- 幂等：否；支持 dry-run：否；长任务：否
- 说明：重载面板（开发用）。响应发出后面板自行 reload，连接会短暂中断。
- 返回：object: {reloading: true}

_无参数。_

### `host.state`

- 工具：`host_resync`
- 工具组：`core`；风险等级：T0
- 执行器：`jsx`
- 实现：`app.jsx` :: `host_state`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：读取宿主当前状态（工程路径、是否已保存、活动序列、播放头位置），用于 resync 差异报告。
- 返回：object: {has_project, project_path, dirty, active_sequence, playhead_s, app_version}

_无参数。_

### `app.info`

- 工具：`ppro_app_info`
- 工具组：`core`；风险等级：T0
- 执行器：`jsx`
- 实现：`app.jsx` :: `app_info`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：读取宿主应用信息：版本、语言、工程路径与名称、脏标志。
- 返回：object: {app_version, app_language, has_project, project_name, project_path, dirty, project_id}

_无参数。_

### `app.get_state`

- 工具：`ppro_app_get_state`
- 工具组：`timeline_edit`；风险等级：T0
- 执行器：`jsx`
- 实现：`app.jsx` :: `app_get_state`
- 能力：`public_or_qe`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：读取当前编辑状态：活动序列、播放头、入出点、选中项（选中项依赖 QE，未启用时返回空数组并给出 warning）。
- 返回：object: {active_sequence, playhead_s, in_point_s, out_point_s, selection: [], warnings: []}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `include_selection` | bool | 否 | `True` | 是否读取当前选中项（需要 QE）。 |

### `app.set_playhead`

- 工具：`ppro_app_set_playhead`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`app.jsx` :: `app_set_playhead`
- 能力：`public`
- 幂等：是；支持 dry-run：是；长任务：否
- 说明：把播放头移动到指定秒数。
- 返回：object: {playhead_s}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `seconds` | float | 是 | — | 目标时间（秒），相对序列起点。 |

### `project.info`

- 工具：`ppro_project_info`
- 工具组：`core`；风险等级：T0
- 执行器：`jsx`
- 实现：`project.jsx` :: `project_info`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：读取工程结构概览：名称、路径、脏标志、序列名列表、素材箱数量、条目数量。
- 返回：object: {has_project, name, path, dirty, sequences: [], bin_count, item_count}

_无参数。_

### `project.new`

- 工具：`ppro_project_new`
- 工具组：`project_edit`；风险等级：T2
- 执行器：`jsx`
- 实现：`project.jsx` :: `project_new`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：新建工程并保存到指定 .prproj 路径。
- 返回：object: {created, path}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | 是 | — | 新工程的目标 .prproj 绝对路径。 |

### `project.open`

- 工具：`ppro_project_open`
- 工具组：`project_edit`；风险等级：T2
- 执行器：`jsx`
- 实现：`project.jsx` :: `project_open`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：打开磁盘上的 .prproj 工程。当前工程有未保存改动时 L1 会拒绝，要求先 save。
- 返回：object: {opened, path, name}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | 是 | — | 要打开的 .prproj 绝对路径。 |

### `project.save`

- 工具：`ppro_project_save`
- 工具组：`project_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`project.jsx` :: `project_save`
- 能力：`public`
- 幂等：是；支持 dry-run：是；长任务：否
- 说明：保存当前工程到其现有路径。
- 返回：object: {saved, path}

_无参数。_

### `project.save_as`

- 工具：`ppro_project_save_as`
- 工具组：`project_edit`；风险等级：T2
- 执行器：`jsx`
- 实现：`project.jsx` :: `project_save_as`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：另存工程到新路径（会改变当前工程路径）。
- 返回：object: {saved, path}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | 是 | — | 目标 .prproj 绝对路径。 |

### `project.close`

- 工具：`ppro_project_close`
- 工具组：`project_edit`；风险等级：T2
- 执行器：`jsx`
- 实现：`project.jsx` :: `project_close`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：关闭当前工程。未保存改动默认拒绝关闭（避免宿主模态对话框），确认丢弃时才继续。
- 返回：object: {closed, discarded}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `confirm_discard` | bool | 否 | `False` | true 表示丢弃未保存改动直接关闭。 |

### `project.import`

- 工具：`ppro_project_import`
- 工具组：`project_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`project.jsx` :: `project_import`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：导入磁盘素材到工程，可指定目标素材箱。
- 返回：object: {imported: [{id, path, name, type}], missing: []}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `paths` | array<string> | 是 | — | 要导入的媒体文件绝对路径列表。 |
| `bin` | string | 否 | `None` | 目标素材箱路径，缺省导入到工程根。 |
| `suppress_ui` | bool | 否 | `True` | 是否抑制宿主导入进度 UI。 |

### `project.bin_create`

- 工具：`ppro_project_bin_create`
- 工具组：`project_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`project.jsx` :: `project_bin_create`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：新建素材箱。
- 返回：object: {created, path, name}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | 素材箱名称。 |
| `parent` | string | 否 | `None` | 父素材箱路径，缺省为工程根。 |

### `project.bin_list`

- 工具：`ppro_project_bin_list`
- 工具组：`project_edit`；风险等级：T0
- 执行器：`jsx`
- 实现：`project.jsx` :: `project_bin_list`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：列出素材箱及其直接子项数量。
- 返回：object: {bins: [{path, name, item_count, child_count}]}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `parent` | string | 否 | `None` | 只列出该素材箱下的子素材箱；缺省列出全部。 |

### `project.bin_move`

- 工具：`ppro_project_bin_move`
- 工具组：`project_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`project.jsx` :: `project_bin_move`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：把工程条目移动到目标素材箱。
- 返回：object: {moved, failed: []}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `items` | array<string> | 是 | — | 要移动的条目路径或 nodeId 列表。 |
| `target_bin` | string | 是 | — | 目标素材箱路径。 |

### `project.item_find`

- 工具：`ppro_project_item_find`
- 工具组：`project_edit`；风险等级：T0
- 执行器：`jsx`
- 实现：`project.jsx` :: `project_item_find`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：按名称/路径子串查找工程条目。
- 返回：object: {items: [{id, path, name, type, media_path}], truncated}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | — | 匹配子串（不区分大小写）。 |
| `kind` | enum(`any`, `clip`, `bin`, `sequence`, `file`) | 否 | `any` | 条目类型过滤。 |
| `limit` | int | 否 | `50` | 返回上限。 |

### `sequence.create`

- 工具：`ppro_sequence_create`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`sequence.jsx` :: `sequence_create`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：新建序列。可用 .sqpreset 预设；也接受 width/height/frame_rate，但宿主公开 API 无法在无预设时设定这些值，此时返回 UNSUPPORTED_API 并给出补救建议。
- 返回：object: {id, name, path, width, height, frame_rate}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | 序列名称。 |
| `preset_path` | string | 否 | `None` | .sqpreset 绝对路径。 |
| `width` | int | 否 | `None` | 期望帧宽（无 preset_path 时无法保证）。 |
| `height` | int | 否 | `None` | 期望帧高（无 preset_path 时无法保证）。 |
| `frame_rate` | float | 否 | `None` | 期望帧率（无 preset_path 时无法保证）。 |

### `sequence.create_from_clips`

- 工具：`ppro_sequence_create_from_clips`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`sequence.jsx` :: `sequence_create_from_clips`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：由一组素材创建序列（按素材首片段规格自动匹配设置）。
- 返回：object: {id, name, path, clip_count}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | 序列名称。 |
| `items` | array<string> | 是 | — | 参与创建序列的工程条目路径或 nodeId 列表，顺序即时间线顺序。 |

### `sequence.list`

- 工具：`ppro_sequence_list`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`sequence.jsx` :: `sequence_list`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：列出工程内全部序列及其轨道数、时长。
- 返回：object: {sequences: [{id, name, path, video_tracks, audio_tracks, duration_s, frame_rate}]}

_无参数。_

### `sequence.info`

- 工具：`ppro_sequence_info`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`sequence.jsx` :: `sequence_info`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：读取指定（或活动）序列的详细信息。
- 返回：object: {id, name, path, video_tracks, audio_tracks, duration_s, in_point_s, out_point_s, playhead_s, frame_rate, width, height}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` | 序列名或路径；缺省为活动序列。 |

### `sequence.set_active`

- 工具：`ppro_sequence_set_active`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`sequence.jsx` :: `sequence_set_active`
- 能力：`public`
- 幂等：是；支持 dry-run：是；长任务：否
- 说明：把指定序列设为活动序列（在源监视器/时间线中打开）。
- 返回：object: {active_sequence, id}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 是 | — | 序列名或路径。 |

### `sequence.set_in_out`

- 工具：`ppro_sequence_set_in_out`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`sequence.jsx` :: `sequence_set_in_out`
- 能力：`public`
- 幂等：是；支持 dry-run：是；长任务：否
- 说明：设置序列入点/出点。
- 返回：object: {in_point_s, out_point_s}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` | 序列名或路径；缺省为活动序列。 |
| `in_s` | float | 否 | `None` | 入点（秒）。 |
| `out_s` | float | 否 | `None` | 出点（秒）。 |

### `sequence.set_work_area`

- 工具：`ppro_sequence_set_work_area`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`sequence.jsx` :: `sequence_set_work_area`
- 能力：`public_or_qe`
- 幂等：是；支持 dry-run：是；长任务：否
- 说明：设置序列工作区（公开 DOM 无此能力，优先 QE，未启用时返回 UNSUPPORTED_API 并说明如何启用）。
- 返回：object: {work_area_start_s, work_area_end_s}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` | 序列名或路径；缺省为活动序列。 |
| `start_s` | float | 是 | — | 工作区起点（秒）。 |
| `end_s` | float | 是 | — | 工作区终点（秒）。 |

### `timeline.list_clips`

- 工具：`ppro_timeline_list_clips`
- 工具组：`timeline_edit`；风险等级：T0
- 执行器：`jsx`
- 实现：`timeline.jsx` :: `timeline_list_clips`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：列出序列时间线片段（可按轨道过滤）。
- 返回：object: {clips: [{ref, track_type, track_index, index, name, start_s, end_s, duration_s, in_s, out_s, media_path, type, enabled, selected}]}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` | 序列名或路径；缺省为活动序列。 |
| `track_type` | enum(`video`, `audio`, `all`) | 否 | `all` | 轨道类型过滤。 |
| `track_index` | int | 否 | `None` | 轨道序号过滤（1 基）。 |

### `timeline.clip_info`

- 工具：`ppro_timeline_clip_info`
- 工具组：`timeline_edit`；风险等级：T0
- 执行器：`jsx`
- 实现：`timeline.jsx` :: `timeline_clip_info`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：读取单个时间线片段的详细信息（含效果组件数量）。
- 返回：object: {ref, name, type, media_path, start_s, end_s, duration_s, in_s, out_s, speed, enabled, selected, component_count}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` | 序列名或路径；缺省为活动序列。 |
| `ref` | string | 是 | — | 片段引用，形如 V:1:0。 |

### `timeline.insert_clip`

- 工具：`ppro_timeline_insert_clip`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`timeline.jsx` :: `timeline_insert_clip`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：把工程条目插入到指定轨道时间点（插入模式，后续片段右移）。
- 返回：object: {ref, start_s, end_s}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` | 序列名或路径；缺省为活动序列。 |
| `item` | string | 是 | — | 工程条目路径或 nodeId。 |
| `track_type` | enum(`video`, `audio`) | 否 | `video` | 目标轨道类型。 |
| `track_index` | int | 否 | `1` | 目标轨道序号（1 基）。 |
| `at_s` | float | 否 | `0` | 插入时间（秒）。 |

### `timeline.overwrite_clip`

- 工具：`ppro_timeline_overwrite_clip`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`timeline.jsx` :: `timeline_overwrite_clip`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：把工程条目覆盖写入到指定轨道时间点（覆盖模式，替换原有内容）。破坏性局限在覆盖范围内，故为 T1。
- 返回：object: {ref, start_s, end_s}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |
| `item` | string | 是 | — |  |
| `track_type` | enum(`video`, `audio`) | 否 | `video` |  |
| `track_index` | int | 否 | `1` |  |
| `at_s` | float | 否 | `0` |  |

### `timeline.move_clip`

- 工具：`ppro_timeline_move_clip`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`timeline.jsx` :: `timeline_move_clip`
- 能力：`public_or_qe`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：移动片段到新的时间点（可选跨轨道）。优先公开 API，失败时在 enable_qe 下走 QE。
- 返回：object: {ref, start_s, end_s}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |
| `ref` | string | 是 | — |  |
| `to_track_type` | enum(`video`, `audio`) | 否 | `None` |  |
| `to_track_index` | int | 否 | `None` |  |
| `to_s` | float | 是 | — | 目标起点（秒）。 |

### `timeline.trim_clip`

- 工具：`ppro_timeline_trim_clip`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`timeline.jsx` :: `timeline_trim_clip`
- 能力：`public_or_qe`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：调整片段在时间线上的起点/终点（裁切）。至少给出 start_s 或 end_s 之一。
- 返回：object: {ref, start_s, end_s}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |
| `ref` | string | 是 | — |  |
| `start_s` | float | 否 | `None` |  |
| `end_s` | float | 否 | `None` |  |

### `timeline.remove_clip`

- 工具：`ppro_timeline_remove_clip`
- 工具组：`timeline_edit`；风险等级：T2
- 执行器：`jsx`
- 实现：`timeline.jsx` :: `timeline_remove_clip`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：从时间线删除片段（不删除工程素材）。
- 返回：object: {removed, ref}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |
| `ref` | string | 是 | — |  |
| `ripple` | bool | 否 | `False` | 是否连带波纹（后续片段左移）。 |

### `timeline.razor`

- 工具：`ppro_timeline_razor`
- 工具组：`timeline_edit`；风险等级：T2
- 执行器：`jsx`
- 实现：`timeline.jsx` :: `timeline_razor`
- 能力：`qe`；**未公开接口（QE），可能导致宿主崩溃**
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：在指定时间点切分片段。依赖未公开的 QE 接口，默认禁用；启用后可能有宿主崩溃风险。
- 返回：object: {razored_at_s, tracks: []}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |
| `at_s` | float | 是 | — | 切分时间点（秒）。 |
| `track_type` | enum(`video`, `audio`, `all`) | 否 | `all` |  |
| `track_index` | int | 否 | `None` | 限定在某条轨道上切分。 |

### `timeline.add_track`

- 工具：`ppro_timeline_add_track`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`timeline.jsx` :: `timeline_add_track`
- 能力：`public_or_qe`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：新增视频/音频轨道。
- 返回：object: {track_type, track_count, track_index}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |
| `track_type` | enum(`video`, `audio`) | 否 | `video` |  |
| `position` | int | 否 | `None` | 插入位置（1 基）；缺省追加到末尾。 |
| `name` | string | 否 | `None` | 新轨道名称。 |

### `timeline.remove_track`

- 工具：`ppro_timeline_remove_track`
- 工具组：`timeline_edit`；风险等级：T2
- 执行器：`jsx`
- 实现：`timeline.jsx` :: `timeline_remove_track`
- 能力：`public_or_qe`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：删除轨道及其上的全部片段。
- 返回：object: {removed, track_type, track_index, track_count}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |
| `track_type` | enum(`video`, `audio`) | 否 | `video` |  |
| `track_index` | int | 是 | — | 要删除的轨道序号（1 基）。 |

### `timeline.add_marker`

- 工具：`ppro_timeline_add_marker`
- 工具组：`timeline_edit`；风险等级：T1
- 执行器：`jsx`
- 实现：`timeline.jsx` :: `timeline_add_marker`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：在序列上添加标记。
- 返回：object: {index, name, at_s, duration_s, type}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |
| `at_s` | float | 是 | — | 标记时间（秒）。 |
| `name` | string | 否 | `None` | 标记名称。 |
| `comment` | string | 否 | `None` | 标记注释。 |
| `duration_s` | float | 否 | `None` | 标记持续时长（秒），0 为单点标记。 |
| `type` | enum(`comment`, `chapter`, `segmentation`, `web`) | 否 | `comment` | 标记类型。 |

### `timeline.list_markers`

- 工具：`ppro_timeline_list_markers`
- 工具组：`timeline_edit`；风险等级：T0
- 执行器：`jsx`
- 实现：`timeline.jsx` :: `timeline_list_markers`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：列出序列上的全部标记。
- 返回：object: {markers: [{index, name, comment, at_s, duration_s, type}]}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |

### `effects.list_available`

- 工具：`ppro_effects_list_available`
- 工具组：`effects`；风险等级：T0
- 执行器：`jsx`
- 实现：`effects.jsx` :: `effects_list_available`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：列出宿主可用的视频/音频效果（可按名称过滤）。
- 返回：object: {effects: [{name, media_type}], truncated}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `target` | enum(`clip`, `video_track`, `audio_track`, `sequence`, `master`) | 否 | `clip` | 效果可施加的目标类别。 |
| `query` | string | 否 | `None` | 名称子串过滤（不区分大小写）。 |

### `effects.apply`

- 工具：`ppro_effects_apply`
- 工具组：`effects`；风险等级：T1
- 执行器：`jsx`
- 实现：`effects.jsx` :: `effects_apply`
- 能力：`qe`；**未公开接口（QE），可能导致宿主崩溃**
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：给片段施加效果。施加效果依赖未公开的 QE 接口，默认禁用。
- 返回：object: {ref, effect, component_index}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |
| `ref` | string | 是 | — | 片段引用。 |
| `effect` | string | 是 | — | 效果名称（Display Name）。 |

### `effects.get_params`

- 工具：`ppro_effects_get_params`
- 工具组：`effects`；风险等级：T0
- 执行器：`jsx`
- 实现：`effects.jsx` :: `effects_get_params`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：读取片段上已有效果组件及其参数。
- 返回：object: {components: [{index, name, match_name, properties: [{name, match_name, value, type}]}]}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |
| `ref` | string | 是 | — |  |

### `effects.set_param`

- 工具：`ppro_effects_set_param`
- 工具组：`effects`；风险等级：T1
- 执行器：`jsx`
- 实现：`effects.jsx` :: `effects_set_param`
- 能力：`public`
- 幂等：是；支持 dry-run：是；长任务：否
- 说明：设置片段效果参数值。
- 返回：object: {ref, component, param, previous_value, value}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |
| `ref` | string | 是 | — |  |
| `component` | string | 否 | `None` | 组件名称或序号；缺省使用第一个可写组件。 |
| `param` | string | 是 | — | 参数名称或 matchName。 |
| `value` | any | 是 | — | 目标值（数值/布尔/字符串）。 |

### `effects.apply_transition`

- 工具：`ppro_effects_apply_transition`
- 工具组：`effects`；风险等级：T1
- 执行器：`jsx`
- 实现：`effects.jsx` :: `effects_apply_transition`
- 能力：`qe`；**未公开接口（QE），可能导致宿主崩溃**
- 幂等：否；支持 dry-run：是；长任务：否
- 说明：给片段添加转场（片段头/尾）。依赖未公开的 QE 接口，默认禁用。
- 返回：object: {ref, transition, at}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` |  |
| `ref` | string | 是 | — |  |
| `transition` | string | 是 | — | 转场名称。 |
| `at` | enum(`start`, `end`) | 否 | `start` | 施加在片段起点还是终点。 |

### `media.probe`

- 工具：`ppro_media_probe`
- 工具组：`media`；风险等级：T0
- 执行器：`jsx`
- 实现：`media.jsx` :: `media_probe`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：探测工程条目对应的媒体信息。宿主公开 API 覆盖不到的字段以 unavailable 列表显式返回，不猜测。
- 返回：object: {id, name, media_path, type, offline, duration_s, frame_rate, width, height, has_video, has_audio, audio_channels, unavailable: []}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `item` | string | 是 | — | 工程条目路径或 nodeId。 |

### `media.relink`

- 工具：`ppro_media_relink`
- 工具组：`media`；风险等级：T1
- 执行器：`jsx`
- 实现：`media.jsx` :: `media_relink`
- 能力：`public`
- 幂等：是；支持 dry-run：是；长任务：否
- 说明：重新链接离线素材到新路径。
- 返回：object: {id, previous_path, media_path}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `item` | string | 是 | — | 工程条目路径或 nodeId。 |
| `new_path` | string | 是 | — | 新的媒体文件绝对路径。 |
| `override_checks` | bool | 否 | `False` | 是否跳过宿主的媒体一致性校验。 |

### `media.generate_proxies`

- 工具：`ppro_media_generate_proxies`
- 工具组：`media`；风险等级：T1
- 执行器：`jsx`
- 实现：`media.jsx` :: `media_generate_proxies`
- 能力：`public_or_qe`
- 幂等：否；支持 dry-run：是；长任务：是
- 说明：为指定素材生成/附加代理。提交后立即返回，由任务模型跟踪进度。
- 返回：object: {job_id, attached, pending}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `items` | array<string> | 是 | — | 需要生成代理的工程条目路径或 nodeId 列表。 |
| `preset_path` | string | 否 | `None` | 代理编码预设路径。 |
| `ingest` | bool | 否 | `True` | 是否在生成后自动附加为代理。 |

### `export.encode_sequence`

- 工具：`ppro_export_encode_sequence`
- 工具组：`export`；风险等级：T2
- 执行器：`jsx`
- 实现：`export.jsx` :: `export_encode_sequence`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：是
- 说明：把序列送入 AME/宿主编码器导出入队，返回编码任务号。
- 返回：object: {job_id, output_path, sequence, queued}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sequence` | string | 否 | `None` | 序列名或路径；缺省为活动序列。 |
| `output_path` | string | 是 | — | 输出文件绝对路径。 |
| `preset_path` | string | 是 | — | 编码预设（.epr/.prproj 预设）绝对路径。 |
| `work_area` | enum(`entire`, `in_out`) | 否 | `entire` | 导出范围。 |
| `remove_from_queue` | bool | 否 | `True` | 完成后是否从编码队列移除。 |

### `export.encode_file`

- 工具：`ppro_export_encode_file`
- 工具组：`export`；风险等级：T2
- 执行器：`jsx`
- 实现：`export.jsx` :: `export_encode_file`
- 能力：`public`
- 幂等：否；支持 dry-run：是；长任务：是
- 说明：把单个工程条目送入编码器导出，返回编码任务号。
- 返回：object: {job_id, output_path, item, queued}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `item` | string | 是 | — | 工程条目路径或 nodeId。 |
| `output_path` | string | 是 | — | 输出文件绝对路径。 |
| `preset_path` | string | 是 | — | 编码预设绝对路径。 |
| `remove_from_queue` | bool | 否 | `True` |  |

### `export.job_status`

- 工具：—（内部命令，不直接暴露）
- 工具组：`core`；风险等级：T0
- 执行器：`jsx`
- 实现：`export.jsx` :: `export_job_status`
- 能力：`public`
- 幂等：是；支持 dry-run：否；长任务：否
- 说明：查询宿主编码器侧的任务状态；宿主不提供进度时返回 state=unknown，由 L1 结合输出文件增长判定。
- 返回：object: {job_id, state, progress, message}

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `job_id` | string | 是 | — | export.encode_* 返回的编码任务号。 |
