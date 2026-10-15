# cep-extension/jsx —— L3 宿主内 ExtendScript 层

契约：`../JSX_RUNTIME_CONTRACT.md`；命令真源：`commands.json`；路由：`dispatch.jsx`
（**生成物，勿手改**：`python scripts/gen_jsx_dispatch.py`）。本层只执行命令、不做风险判断（约束 C-3）。

## 文件职责

| 文件 | 职责 |
|---|---|
| `json2.jsx` | ES3 JSON 兼容层（`DSH.json.parse/stringify`），不依赖原生 `JSON`。**输出纯 ASCII**（非可打印字符与所有非 ASCII 转义为 `\uXXXX`），保证返回值能穿过 `evalScript` 字符串通道。 |
| `es3_compat.jsx` | 缺失能力补齐（存在则跳过）：`Array.prototype.map/filter/indexOf/forEach/trim`、`String.prototype.trim`、`Object.keys`、`Date.now`。契约里的 `Array.prototype.trim` 无 ES3 语义，定义为"去掉首尾空槽"，业务代码不应依赖。 |
| `wrap.jsx` | `fail/wrap/wrapError/wrapParseFailure/hostSnapshot` + dry-run 检查记录器（`beginChecks/note/dryRunResult`）。错误含 `code/message/remedy/line/file/command/script_excerpt/args_snapshot/detail`；`script_excerpt` 用 `File` 读 `line±5` 行，读不到即 `null`（报告错误的过程不能再抛异常）。 |
| `util.jsx` | 配置、时间换算、引用解析、查找、参数校验、特性检测（`api/requireApi`）、"写后读回"原语（`applyWithVariants/setPropertyWithVariants`）。 |
| `ops/app.jsx` | `host_state`、`app_info`、`app_get_state`、`app_set_playhead`（4） |
| `ops/project.jsx` | 工程与素材箱（11） |
| `ops/sequence.jsx` | 序列（7） |
| `ops/timeline.jsx` | 时间线片段/轨道/标记（12） |
| `ops/effects.jsx` | 效果与参数（5） |
| `ops/media.jsx` | 媒体探测/重链接/代理（3） |
| `ops/export.jsx` | AME 入队与任务状态（3） |
| `ops/qe.jsx` | QE 护栏层（`DSH.qe.*`）：唯一允许出现 QE 调用的地方。 |

45 个 jsx 处理器都是**全局函数**（`dispatch.jsx` 用 `$.global[name]` 解析），
名字与归属文件由 `commands.json` 的 `handler`/`ops_file` 决定。

## QE 护栏（说明书 §6.2）

* `capability=qe`（`timeline.razor`、`effects.apply`、`effects.apply_transition`）：进入处理器前
  由 `dispatch.jsx` 先按 `DSH.qeEnabled()` 拦截一次（`QE_DISABLED`）。
* `public_or_qe`：**先走公开 DOM**；公开路径不可用且 QE 已启用才走 QE；两者都不行时区分
  `QE_DISABLED`（QE 没开）与 `UNSUPPORTED_API`（QE 开了但方法不存在）。
* 所有 QE 调用集中在本目录 `ops/qe.jsx` 并包 `try/catch`，不破坏已完成的公开 DOM 操作；
  唯一例外是 `timeline.move_clip` 的"摘除+重写"复合路径，失败信息会说明片段可能已被摘除。
* ⚠ **QE 是未公开接口，可能导致宿主崩溃或工程损坏**：默认关闭；启用时每次响应都带
  `qe_enabled` 与 `warnings`。

## 三条实现纪律

1. **特性检测，不猜**：不确定存在的宿主成员一律 `DSH.api/requireApi` 探测，缺失即
   `throw DSH.fail("UNSUPPORTED_API", ..., remedy)`，绝不假装成功。
2. **写后读回**：宿主参数形式（Time/ tick 字符串/ 秒数）随版本变化，写入一律"多形式尝试 →
   读回真实值 → 只有一致才算成功"（容差一帧）；无读回手段时返回 `verified:false` 并说明。
3. **dry-run 与真实执行共用同一套前置检查**：`ctx.dryRun===true` 时只读不写，返回
   `{dry_run, actions, checks, ...真实执行的同名字段}`。

## 自测

```powershell
$env:ELECTRON_RUN_AS_NODE="1"
& "F:\DeepseekHarness\DeepSeek Harness.exe" tests\l3\jsx\run.js
```
harness 把 `#include` 递归展开成一段源码（ExtendScript 的 include 就是文本拼接），在带伪宿主
对象的 `vm` 作用域里求值，再直接断言 `DSH.dispatch(...)`。全部通过 45/45。

## 待真机验证（**均未在真实宿主上跑过**；处理器已按"拿不到就报错/标注未校验"处理）

| # | 项 | 现状 |
|---|---|---|
| 1 | `app.project.dirty` | 官方 Project 属性表**无**该成员；只做探测，拿不到返回 `null`。故 `project.close` 在脏标志未知时按"有改动"处理并要求 `confirm_discard=true`（宁可多确认，也不触发模态框）。 |
| 2 | `Sequence.setWorkAreaInPoint/OutPoint`、`setPlayerPosition` 参数 | 文档未展开签名；按 Time/tick/秒三种形式尝试并用 getter 读回校验。 |
| 3 | `Sequence.getInPoint()` 返回"秒字符串" | 按文档用 `secondsFromValue` 解析（自测中曾误当 tick，把 1 秒读成 1 tick，已修正）。 |
| 4 | `Track.insertClip(item, ticks, vTrackIndex, aTrackIndex)` | 文档签名如此；是否必须在目标轨道对象上调用、索引是否被忽略，需实测。 |
| 5 | `TrackItem.move(Time)` | 文档含糊（新时间点 vs 位移量）；按位移量尝试，读回不符则自动改走"摘除+overwriteClip+恢复 in/out"。 |
| 6 | `TrackItem.start/end/inPoint/outPoint` 可写性 | 文档标 read/write；写失败会退到 QE（启用时）。 |
| 7 | `ProjectItem.moveBin()` 是否接受非素材箱条目 | 契约要求移动"工程条目"；处理器逐条目校验并如实报 `failed[]`。 |
| 8 | `app.encoder.encodeSequence/encodeProjectItem` | 参数顺序与 workArea(0/1/2) 按文档；返回值假定 job id 字符串（0=失败）。Encoder 无文档化状态接口，`export.job_status` 固定返回 `state="unknown"`。 |
| 9 | 效果枚举/施加/转场 | 公开 DOM 两者皆无，只能走 QE，且 QE 方法名无文档（`addVideoEffect`/`addTransition`/`getVideoEffectList` 属社区惯例）；按候选名尝试 + 组件/转场数读回校验，真机不同则如实报 `UNSUPPORTED_API`。 |
| 10 | 代理生成 | 公开 DOM 与已知 QE 都无此接口：只在"条目已有代理"时成功，否则 `UNSUPPORTED_API` 并给替代路径（不伪造 `job_id`）。 |
| 11 | `marker.end` 赋值 | 文档注明"传秒数"；先传秒数，失败再传 Time。 |
| 12 | `app_language` | 先探测 `app.properties.getProperty("BE.Prefs.Language")`，再退回 `$.locale`（可能是 locale 而非 UI 语言）。 |
| 13 | `TrackItem.disabled` | 文档列成"带参方法"；读不到布尔值时 `enabled` 返回 `null`（不猜）。 |
| 14 | `script_excerpt` 行号来源 | `DSH.fail` 用调用点的 `$.line`，宿主原生错误用 `error.line/lineNumber`；需用真机错误样本抽查。 |
