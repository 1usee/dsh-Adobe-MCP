# MCP 工具目录

> 本文件由 `python scripts/gen_commands_doc.py` 自动生成，请勿手工编辑。
> 数据来源：`cep-extension/jsx/commands.json`（schema_version 1）与 `src/dsh_adobe_mcp/safety/policy.py`；参数取自工具的真实 JSON schema。
> 一致性由 `tests/contract/test_tool_docs.py` 强制校验。

## 总览

| 工具组 | 工具数 | T0 | T1 | T2 | 默认启用 |
|---|---|---|---|---|---|
| 核心组（始终启用） (`core`) | 14 | 12 | 1 | 1 | 是 |
| 工程编辑组 (`project_edit`) | 10 | 2 | 4 | 4 | 是 |
| 时间线编辑组 (`timeline_edit`) | 21 | 4 | 14 | 3 | 是 |
| 效果组 (`effects`) | 5 | 2 | 3 | 0 | 否 |
| 媒体组 (`media`) | 3 | 1 | 2 | 0 | 否 |
| 导出组 (`export`) | 3 | 1 | 0 | 2 | 否 |
| **合计** | **56** | **22** | **24** | **10** | |

## 风险等级的含义

| 等级 | 定义 | 处置 |
|---|---|---|
| T0 只读 | 不改变宿主任何状态 | 直接执行，写审计日志 |
| T1 增量/可逆 | 只增加内容，或改动可被撤销栈覆盖 | 直接执行，返回 `undo_hint` |
| T2 破坏性 | 删除内容、覆盖文件、改变工程路径、批量导出 | **不执行**，返回确认令牌与影响说明 |

## 工具清单

### 核心组（始终启用）

| 工具 | 风险 | 桥接命令 | 说明 |
|---|---|---|---|
| `host_launch` | T1 | —（L1 本地） | 启动目标宿主进程并等待面板就绪 |
| `host_list` | T0 | —（L1 本地） | 枚举本机已安装的宿主及其版本 |
| `host_reload_panel` | T0 | `panel.reload` | 重载宿主内面板（开发用） |
| `host_resync` | T0 | `host.state` | 重新读取宿主真实状态并产出差异报告 |
| `host_status` | T0 | —（L1 本地） | 报告宿主的运行与连接状态 |
| `host_wait_ready` | T0 | —（L1 本地） | 等待面板握手并建立桥接连接 |
| `job_cancel` | T0 | —（L1 本地） | 尽力取消长任务 |
| `job_list` | T0 | —（L1 本地） | 列出长任务 |
| `job_status` | T0 | —（L1 本地） | 查询长任务状态与进度 |
| `ppro_app_info` | T0 | `app.info` | 读取宿主应用信息与工程路径 |
| `ppro_project_info` | T0 | `project.info` | 读取当前工程结构概览 |
| `safety_confirm_operation` | T0 | —（L1 本地） | 用确认令牌执行被挂起的 T2 操作 |
| `safety_list_checkpoints` | T0 | —（L1 本地） | 列出可用检查点 |
| `safety_restore_checkpoint` | T2 | —（L1 本地） | 把工程恢复到某个检查点 |

### 工程编辑组

| 工具 | 风险 | 桥接命令 | 说明 |
|---|---|---|---|
| `ppro_project_bin_create` | T1 | `project.bin_create` | 新建素材箱 |
| `ppro_project_bin_list` | T0 | `project.bin_list` | 列出素材箱 |
| `ppro_project_bin_move` | T1 | `project.bin_move` | 移动工程条目到目标素材箱 |
| `ppro_project_close` | T2 | `project.close` | 关闭当前工程 |
| `ppro_project_import` | T1 | `project.import` | 导入磁盘素材到工程 |
| `ppro_project_item_find` | T0 | `project.item_find` | 查找工程条目 |
| `ppro_project_new` | T2 | `project.new` | 新建工程并保存到指定路径 |
| `ppro_project_open` | T2 | `project.open` | 打开磁盘上的工程 |
| `ppro_project_save` | T1 | `project.save` | 保存当前工程 |
| `ppro_project_save_as` | T2 | `project.save_as` | 另存工程到新路径 |

### 时间线编辑组

| 工具 | 风险 | 桥接命令 | 说明 |
|---|---|---|---|
| `ppro_app_get_state` | T0 | `app.get_state` | 读取当前编辑状态 |
| `ppro_app_set_playhead` | T1 | `app.set_playhead` | 移动播放头 |
| `ppro_sequence_create` | T1 | `sequence.create` | 新建序列 |
| `ppro_sequence_create_from_clips` | T1 | `sequence.create_from_clips` | 由素材创建序列 |
| `ppro_sequence_info` | T1 | `sequence.info` | 读取序列详情 |
| `ppro_sequence_list` | T1 | `sequence.list` | 列出工程内序列 |
| `ppro_sequence_set_active` | T1 | `sequence.set_active` | 切换活动序列 |
| `ppro_sequence_set_in_out` | T1 | `sequence.set_in_out` | 设置序列入出点 |
| `ppro_sequence_set_work_area` | T1 | `sequence.set_work_area` | 设置序列工作区 |
| `ppro_timeline_add_marker` | T1 | `timeline.add_marker` | 添加序列标记 |
| `ppro_timeline_add_track` | T1 | `timeline.add_track` | 新增轨道 |
| `ppro_timeline_clip_info` | T0 | `timeline.clip_info` | 读取片段详情 |
| `ppro_timeline_insert_clip` | T1 | `timeline.insert_clip` | 插入素材到时间线 |
| `ppro_timeline_list_clips` | T0 | `timeline.list_clips` | 列出时间线片段 |
| `ppro_timeline_list_markers` | T0 | `timeline.list_markers` | 列出序列标记 |
| `ppro_timeline_move_clip` | T1 | `timeline.move_clip` | 移动时间线片段 |
| `ppro_timeline_overwrite_clip` | T1 | `timeline.overwrite_clip` | 覆盖写入素材到时间线 |
| `ppro_timeline_razor` | T2 | `timeline.razor` | 在指定时间点切分片段 |
| `ppro_timeline_remove_clip` | T2 | `timeline.remove_clip` | 从时间线删除片段 |
| `ppro_timeline_remove_track` | T2 | `timeline.remove_track` | 删除轨道及其片段 |
| `ppro_timeline_trim_clip` | T1 | `timeline.trim_clip` | 裁切时间线片段 |

### 效果组

| 工具 | 风险 | 桥接命令 | 说明 |
|---|---|---|---|
| `ppro_effects_apply` | T1 | `effects.apply` | 给片段施加效果 |
| `ppro_effects_apply_transition` | T1 | `effects.apply_transition` | 给片段添加转场 |
| `ppro_effects_get_params` | T0 | `effects.get_params` | 读取效果参数 |
| `ppro_effects_list_available` | T0 | `effects.list_available` | 列出可用效果 |
| `ppro_effects_set_param` | T1 | `effects.set_param` | 设置效果参数 |

### 媒体组

| 工具 | 风险 | 桥接命令 | 说明 |
|---|---|---|---|
| `ppro_media_generate_proxies` | T1 | `media.generate_proxies` | 生成并附加代理 |
| `ppro_media_probe` | T0 | `media.probe` | 探测素材元数据 |
| `ppro_media_relink` | T1 | `media.relink` | 重新链接离线素材 |

### 导出组

| 工具 | 风险 | 桥接命令 | 说明 |
|---|---|---|---|
| `ppro_export_encode_file` | T2 | `export.encode_file` | 导出单个素材 |
| `ppro_export_encode_sequence` | T2 | `export.encode_sequence` | 导出序列 |
| `ppro_export_list_presets` | T0 | —（L1 本地） | 列出可用的编码预设 |

## 工具详情

### `host_launch`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T1 增量/可逆**
- 实现位置：L1（不经过宿主脚本）
- 说明：启动目标宿主进程并等待面板就绪
- 撤销指引：关闭宿主进程即可回到启动前状态；本操作不修改工程。

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `host` | string \| null | 否 | `null` |
| `project_path` | string \| null | 否 | `null` |
| `wait` | boolean | 否 | `true` |

### `host_list`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T0 只读**
- 实现位置：L1（不经过宿主脚本）
- 说明：枚举本机已安装的宿主及其版本

_无参数。_

### `host_reload_panel`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T0 只读**
- 桥接命令：`panel.reload`（node 侧）
- 说明：重载宿主内面板（开发用）
- 返回：object: {reloading: true}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `host` | string \| null | 否 | `null` |

### `host_resync`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T0 只读**
- 桥接命令：`host.state`（jsx 侧）
- 说明：重新读取宿主真实状态并产出差异报告
- 返回：object: {has_project, project_path, dirty, active_sequence, playhead_s, app_version}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `host` | string \| null | 否 | `null` |

### `host_status`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T0 只读**
- 实现位置：L1（不经过宿主脚本）
- 说明：报告宿主的运行与连接状态

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `host` | string \| null | 否 | `null` |

### `host_wait_ready`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T0 只读**
- 实现位置：L1（不经过宿主脚本）
- 说明：等待面板握手并建立桥接连接

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `host` | string \| null | 否 | `null` |
| `timeout_s` | number \| null | 否 | `null` |

### `job_cancel`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T0 只读**
- 实现位置：L1（不经过宿主脚本）
- 说明：尽力取消长任务

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `job_id` | string | 是 | — |
| `host` | string \| null | 否 | `null` |

### `job_list`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T0 只读**
- 实现位置：L1（不经过宿主脚本）
- 说明：列出长任务

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `limit` | integer | 否 | `50` |
| `host` | string \| null | 否 | `null` |

### `job_status`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T0 只读**
- 实现位置：L1（不经过宿主脚本）
- 说明：查询长任务状态与进度

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `job_id` | string | 是 | — |
| `host` | string \| null | 否 | `null` |

### `ppro_app_info`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T0 只读**
- 桥接命令：`app.info`（jsx 侧）
- 说明：读取宿主应用信息与工程路径
- 返回：object: {app_version, app_language, has_project, project_name, project_path, dirty, project_id}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `host` | string \| null | 否 | `null` |

### `ppro_project_info`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T0 只读**
- 桥接命令：`project.info`（jsx 侧）
- 说明：读取当前工程结构概览
- 返回：object: {has_project, name, path, dirty, sequences: [], bin_count, item_count}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `host` | string \| null | 否 | `null` |

### `safety_confirm_operation`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T0 只读**
- 实现位置：L1（不经过宿主脚本）
- 说明：用确认令牌执行被挂起的 T2 操作

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `confirm_token` | string | 是 | — |
| `host` | string \| null | 否 | `null` |

### `safety_list_checkpoints`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T0 只读**
- 实现位置：L1（不经过宿主脚本）
- 说明：列出可用检查点

_无参数。_

### `safety_restore_checkpoint`

- 工具组：`core`（核心组（始终启用））
- 风险等级：**T2 破坏性（需确认令牌）**
- 实现位置：L1（不经过宿主脚本）
- 说明：把工程恢复到某个检查点
- 撤销指引：恢复前会为当前状态自动再建一个检查点，可用它把恢复本身回退。

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `checkpoint_id` | string | 是 | — |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |
| `confirm_token` | string \| null | 否 | `null` |

### `ppro_project_bin_create`

- 工具组：`project_edit`（工程编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`project.bin_create`（jsx 侧）
- 说明：新建素材箱
- 撤销指引：在素材箱上右键 → 删除。
- 返回：object: {created, path, name}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `name` | string | 是 | — |
| `parent` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_project_bin_list`

- 工具组：`project_edit`（工程编辑组）
- 风险等级：**T0 只读**
- 桥接命令：`project.bin_list`（jsx 侧）
- 说明：列出素材箱
- 返回：object: {bins: [{path, name, item_count, child_count}]}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `parent` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |

### `ppro_project_bin_move`

- 工具组：`project_edit`（工程编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`project.bin_move`（jsx 侧）
- 说明：移动工程条目到目标素材箱
- 撤销指引：把条目移回原素材箱（原路径已由本次调用返回）。
- 返回：object: {moved, failed: []}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `items` | array<string> | 是 | — |
| `target_bin` | string | 是 | — |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_project_close`

- 工具组：`project_edit`（工程编辑组）
- 风险等级：**T2 破坏性（需确认令牌）**
- 桥接命令：`project.close`（jsx 侧）
- 说明：关闭当前工程
- 撤销指引：重新打开该工程文件即可；关闭前是否丢弃改动由 confirm_discard 明确指定。
- 返回：object: {closed, discarded}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `confirm_discard` | boolean | 否 | `false` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |
| `confirm_token` | string \| null | 否 | `null` |

### `ppro_project_import`

- 工具组：`project_edit`（工程编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`project.import`（jsx 侧）
- 说明：导入磁盘素材到工程
- 撤销指引：在素材箱中选中导入的条目并删除（不勾选“从磁盘删除文件”）。
- 返回：object: {imported: [{id, path, name, type}], missing: []}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `paths` | array<string> | 是 | — |
| `bin` | string \| null | 否 | `null` |
| `suppress_ui` | boolean | 否 | `true` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_project_item_find`

- 工具组：`project_edit`（工程编辑组）
- 风险等级：**T0 只读**
- 桥接命令：`project.item_find`（jsx 侧）
- 说明：查找工程条目
- 返回：object: {items: [{id, path, name, type, media_path}], truncated}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `query` | string | 是 | — |
| `kind` | string | 否 | `"any"` |
| `limit` | integer | 否 | `50` |
| `host` | string \| null | 否 | `null` |

### `ppro_project_new`

- 工具组：`project_edit`（工程编辑组）
- 风险等级：**T2 破坏性（需确认令牌）**
- 桥接命令：`project.new`（jsx 侧）
- 说明：新建工程并保存到指定路径
- 撤销指引：关闭该工程即可；原工程文件未被触碰，可在宿主中重新打开。
- 返回：object: {created, path}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `path` | string | 是 | — |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |
| `confirm_token` | string \| null | 否 | `null` |

### `ppro_project_open`

- 工具组：`project_edit`（工程编辑组）
- 风险等级：**T2 破坏性（需确认令牌）**
- 桥接命令：`project.open`（jsx 侧）
- 说明：打开磁盘上的工程
- 撤销指引：重新打开先前的工程即可回到原状态（未保存改动会被 L1 提前拦下）。
- 返回：object: {opened, path, name}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `path` | string | 是 | — |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |
| `confirm_token` | string \| null | 否 | `null` |

### `ppro_project_save`

- 工具组：`project_edit`（工程编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`project.save`（jsx 侧）
- 说明：保存当前工程
- 撤销指引：宿主撤销栈不含保存动作；如需回退内容请用 safety_list_checkpoints 选择更早的检查点。
- 返回：object: {saved, path}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_project_save_as`

- 工具组：`project_edit`（工程编辑组）
- 风险等级：**T2 破坏性（需确认令牌）**
- 桥接命令：`project.save_as`（jsx 侧）
- 说明：另存工程到新路径
- 撤销指引：旧路径的工程文件保持原样，重新打开它即可；新路径文件需手工删除。
- 返回：object: {saved, path}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `path` | string | 是 | — |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |
| `confirm_token` | string \| null | 否 | `null` |

### `ppro_app_get_state`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T0 只读**
- 桥接命令：`app.get_state`（jsx 侧）
- 说明：读取当前编辑状态
- 宿主接口：`public_or_qe`
- 返回：object: {active_sequence, playhead_s, in_point_s, out_point_s, selection: [], warnings: []}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `include_selection` | boolean | 否 | `true` |
| `host` | string \| null | 否 | `null` |

### `ppro_app_set_playhead`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`app.set_playhead`（jsx 侧）
- 说明：移动播放头
- 撤销指引：把播放头移回原位置（原位置已由本次调用返回）。
- 返回：object: {playhead_s}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `seconds` | number | 是 | — |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_sequence_create`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`sequence.create`（jsx 侧）
- 说明：新建序列
- 撤销指引：在项目面板中删除该序列（不影响素材）。
- 返回：object: {id, name, path, width, height, frame_rate}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `name` | string | 是 | — |
| `preset_path` | string \| null | 否 | `null` |
| `width` | integer \| null | 否 | `null` |
| `height` | integer \| null | 否 | `null` |
| `frame_rate` | number \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_sequence_create_from_clips`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`sequence.create_from_clips`（jsx 侧）
- 说明：由素材创建序列
- 撤销指引：在项目面板中删除该序列（不影响素材）。
- 返回：object: {id, name, path, clip_count}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `name` | string | 是 | — |
| `items` | array<string> | 是 | — |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_sequence_info`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`sequence.info`（jsx 侧）
- 说明：读取序列详情
- 撤销指引：只读查询，未改变宿主任何状态，无需撤销。
- 返回：object: {id, name, path, video_tracks, audio_tracks, duration_s, in_point_s, out_point_s, playhead_s, frame_rate, width, height}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |

### `ppro_sequence_list`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`sequence.list`（jsx 侧）
- 说明：列出工程内序列
- 撤销指引：只读查询，未改变宿主任何状态，无需撤销。
- 返回：object: {sequences: [{id, name, path, video_tracks, audio_tracks, duration_s, frame_rate}]}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `host` | string \| null | 否 | `null` |

### `ppro_sequence_set_active`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`sequence.set_active`（jsx 侧）
- 说明：切换活动序列
- 撤销指引：把先前的活动序列重新设为活动序列。
- 返回：object: {active_sequence, id}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `sequence` | string | 是 | — |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_sequence_set_in_out`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`sequence.set_in_out`（jsx 侧）
- 说明：设置序列入出点
- 撤销指引：用返回的原入出点再次调用本工具恢复。
- 返回：object: {in_point_s, out_point_s}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `sequence` | string \| null | 否 | `null` |
| `in_s` | number \| null | 否 | `null` |
| `out_s` | number \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_sequence_set_work_area`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`sequence.set_work_area`（jsx 侧）
- 说明：设置序列工作区
- 撤销指引：用返回的原工作区范围再次调用本工具恢复。
- 宿主接口：`public_or_qe`
- 返回：object: {work_area_start_s, work_area_end_s}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `start_s` | number | 是 | — |
| `end_s` | number | 是 | — |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_timeline_add_marker`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`timeline.add_marker`（jsx 侧）
- 说明：添加序列标记
- 撤销指引：在标记面板删除该标记（索引已由本次调用返回）。
- 返回：object: {index, name, at_s, duration_s, type}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `at_s` | number | 是 | — |
| `name` | string \| null | 否 | `null` |
| `comment` | string \| null | 否 | `null` |
| `duration_s` | number \| null | 否 | `null` |
| `type` | string | 否 | `"comment"` |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_timeline_add_track`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`timeline.add_track`（jsx 侧）
- 说明：新增轨道
- 撤销指引：用 ppro_timeline_remove_track 删除新增的轨道（轨道序号已由本次调用返回）。
- 宿主接口：`public_or_qe`
- 返回：object: {track_type, track_count, track_index}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `track_type` | string | 否 | `"video"` |
| `position` | integer \| null | 否 | `null` |
| `name` | string \| null | 否 | `null` |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_timeline_clip_info`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T0 只读**
- 桥接命令：`timeline.clip_info`（jsx 侧）
- 说明：读取片段详情
- 返回：object: {ref, name, type, media_path, start_s, end_s, duration_s, in_s, out_s, speed, enabled, selected, component_count}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `ref` | string | 是 | — |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |

### `ppro_timeline_insert_clip`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`timeline.insert_clip`（jsx 侧）
- 说明：插入素材到时间线
- 撤销指引：宿主撤销（Ctrl+Z）可撤销本次插入；或删除返回的 ref 对应片段。
- 返回：object: {ref, start_s, end_s}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `item` | string | 是 | — |
| `at_s` | number | 否 | `0` |
| `track_type` | string | 否 | `"video"` |
| `track_index` | integer | 否 | `1` |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_timeline_list_clips`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T0 只读**
- 桥接命令：`timeline.list_clips`（jsx 侧）
- 说明：列出时间线片段
- 返回：object: {clips: [{ref, track_type, track_index, index, name, start_s, end_s, duration_s, in_s, out_s, media_path, type, enabled, selected}]}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `sequence` | string \| null | 否 | `null` |
| `track_type` | string | 否 | `"all"` |
| `track_index` | integer \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |

### `ppro_timeline_list_markers`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T0 只读**
- 桥接命令：`timeline.list_markers`（jsx 侧）
- 说明：列出序列标记
- 返回：object: {markers: [{index, name, comment, at_s, duration_s, type}]}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |

### `ppro_timeline_move_clip`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`timeline.move_clip`（jsx 侧）
- 说明：移动时间线片段
- 撤销指引：用返回的原起点再次调用本工具移动回原位。
- 宿主接口：`public_or_qe`
- 返回：object: {ref, start_s, end_s}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `ref` | string | 是 | — |
| `to_s` | number | 是 | — |
| `to_track_type` | string \| null | 否 | `null` |
| `to_track_index` | integer \| null | 否 | `null` |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_timeline_overwrite_clip`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`timeline.overwrite_clip`（jsx 侧）
- 说明：覆盖写入素材到时间线
- 撤销指引：宿主撤销（Ctrl+Z）可恢复被覆盖的内容。
- 返回：object: {ref, start_s, end_s}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `item` | string | 是 | — |
| `at_s` | number | 否 | `0` |
| `track_type` | string | 否 | `"video"` |
| `track_index` | integer | 否 | `1` |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_timeline_razor`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T2 破坏性（需确认令牌）**
- 桥接命令：`timeline.razor`（jsx 侧）
- 说明：在指定时间点切分片段
- 撤销指引：撤销切分：选中两个半段 → 右键 → 删除，再从素材箱重新插入；或宿主撤销栈回退。
- 宿主接口：`qe`（**未公开接口，可能导致宿主崩溃**）
- 返回：object: {razored_at_s, tracks: []}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `at_s` | number | 是 | — |
| `track_type` | string | 否 | `"all"` |
| `track_index` | integer \| null | 否 | `null` |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |
| `confirm_token` | string \| null | 否 | `null` |

### `ppro_timeline_remove_clip`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T2 破坏性（需确认令牌）**
- 桥接命令：`timeline.remove_clip`（jsx 侧）
- 说明：从时间线删除片段
- 撤销指引：宿主撤销（Ctrl+Z）可恢复；工程素材本身未被删除。
- 返回：object: {removed, ref}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `ref` | string | 是 | — |
| `ripple` | boolean | 否 | `false` |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |
| `confirm_token` | string \| null | 否 | `null` |

### `ppro_timeline_remove_track`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T2 破坏性（需确认令牌）**
- 桥接命令：`timeline.remove_track`（jsx 侧）
- 说明：删除轨道及其片段
- 撤销指引：宿主撤销栈通常不可覆盖轨道删除；请用 safety_list_checkpoints 恢复检查点。
- 宿主接口：`public_or_qe`
- 返回：object: {removed, track_type, track_index, track_count}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `track_index` | integer | 是 | — |
| `track_type` | string | 否 | `"video"` |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |
| `confirm_token` | string \| null | 否 | `null` |

### `ppro_timeline_trim_clip`

- 工具组：`timeline_edit`（时间线编辑组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`timeline.trim_clip`（jsx 侧）
- 说明：裁切时间线片段
- 撤销指引：用返回的原起止点再次调用本工具恢复。
- 宿主接口：`public_or_qe`
- 返回：object: {ref, start_s, end_s}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `ref` | string | 是 | — |
| `start_s` | number \| null | 否 | `null` |
| `end_s` | number \| null | 否 | `null` |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_effects_apply`

- 工具组：`effects`（效果组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`effects.apply`（jsx 侧）
- 说明：给片段施加效果
- 撤销指引：在效果控件中选中该效果 → 删除；宿主撤销栈亦可回退。
- 宿主接口：`qe`（**未公开接口，可能导致宿主崩溃**）
- 返回：object: {ref, effect, component_index}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `ref` | string | 是 | — |
| `effect` | string | 是 | — |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_effects_apply_transition`

- 工具组：`effects`（效果组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`effects.apply_transition`（jsx 侧）
- 说明：给片段添加转场
- 撤销指引：在时间线上选中转场 → 删除；宿主撤销栈亦可回退。
- 宿主接口：`qe`（**未公开接口，可能导致宿主崩溃**）
- 返回：object: {ref, transition, at}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `ref` | string | 是 | — |
| `transition` | string | 是 | — |
| `at` | string | 否 | `"start"` |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_effects_get_params`

- 工具组：`effects`（效果组）
- 风险等级：**T0 只读**
- 桥接命令：`effects.get_params`（jsx 侧）
- 说明：读取效果参数
- 返回：object: {components: [{index, name, match_name, properties: [{name, match_name, value, type}]}]}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `ref` | string | 是 | — |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |

### `ppro_effects_list_available`

- 工具组：`effects`（效果组）
- 风险等级：**T0 只读**
- 桥接命令：`effects.list_available`（jsx 侧）
- 说明：列出可用效果
- 返回：object: {effects: [{name, media_type}], truncated}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `target` | string | 否 | `"clip"` |
| `query` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |

### `ppro_effects_set_param`

- 工具组：`effects`（效果组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`effects.set_param`（jsx 侧）
- 说明：设置效果参数
- 撤销指引：用返回的原值再次调用本工具恢复。
- 返回：object: {ref, component, param, previous_value, value}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `ref` | string | 是 | — |
| `param` | string | 是 | — |
| `value` | any | 是 | — |
| `component` | string \| null | 否 | `null` |
| `sequence` | string \| null | 否 | `null` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_media_generate_proxies`

- 工具组：`media`（媒体组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`media.generate_proxies`（jsx 侧）
- 说明：生成并附加代理
- 撤销指引：在素材上右键 → 代理 → 取消链接代理；生成的代文件需手工删除。
- 宿主接口：`public_or_qe`
- 长任务：立即返回 `job_id`，用 `job_status` 跟踪
- 返回：object: {job_id, attached, pending}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `items` | array<string> | 是 | — |
| `preset_path` | string \| null | 否 | `null` |
| `ingest` | boolean | 否 | `true` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_media_probe`

- 工具组：`media`（媒体组）
- 风险等级：**T0 只读**
- 桥接命令：`media.probe`（jsx 侧）
- 说明：探测素材元数据
- 返回：object: {id, name, media_path, type, offline, duration_s, frame_rate, width, height, has_video, has_audio, audio_channels, unavailable: []}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `item` | string | 是 | — |
| `host` | string \| null | 否 | `null` |

### `ppro_media_relink`

- 工具组：`media`（媒体组）
- 风险等级：**T1 增量/可逆**
- 桥接命令：`media.relink`（jsx 侧）
- 说明：重新链接离线素材
- 撤销指引：用返回的原路径再次调用本工具即可回退链接。
- 返回：object: {id, previous_path, media_path}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `item` | string | 是 | — |
| `new_path` | string | 是 | — |
| `override_checks` | boolean | 否 | `false` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |

### `ppro_export_encode_file`

- 工具组：`export`（导出组）
- 风险等级：**T2 破坏性（需确认令牌）**
- 桥接命令：`export.encode_file`（jsx 侧）
- 说明：导出单个素材
- 撤销指引：删除输出文件即可回退；工程本身不被修改。
- 长任务：立即返回 `job_id`，用 `job_status` 跟踪
- 返回：object: {job_id, output_path, item, queued}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `item` | string | 是 | — |
| `output_path` | string | 是 | — |
| `preset_path` | string | 是 | — |
| `remove_from_queue` | boolean | 否 | `true` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |
| `confirm_token` | string \| null | 否 | `null` |

### `ppro_export_encode_sequence`

- 工具组：`export`（导出组）
- 风险等级：**T2 破坏性（需确认令牌）**
- 桥接命令：`export.encode_sequence`（jsx 侧）
- 说明：导出序列
- 撤销指引：删除输出文件即可回退；工程本身不被修改。
- 长任务：立即返回 `job_id`，用 `job_status` 跟踪
- 返回：object: {job_id, output_path, sequence, queued}

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `output_path` | string | 是 | — |
| `preset_path` | string | 是 | — |
| `sequence` | string \| null | 否 | `null` |
| `work_area` | string | 否 | `"entire"` |
| `remove_from_queue` | boolean | 否 | `true` |
| `host` | string \| null | 否 | `null` |
| `dry_run` | boolean | 否 | `false` |
| `confirm_token` | string \| null | 否 | `null` |

### `ppro_export_list_presets`

- 工具组：`export`（导出组）
- 风险等级：**T0 只读**
- 实现位置：L1（不经过宿主脚本）
- 说明：列出可用的编码预设

| 参数 | 类型 | 必填 | 默认 |
|---|---|---|---|
| `kind` | string | 否 | `"sequence"` |
| `query` | string \| null | 否 | `null` |
