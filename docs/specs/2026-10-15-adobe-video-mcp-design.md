# DeepSeek Harness × Adobe 视频制作软件 —— MCP 服务器工程设计说明书

| 项 | 内容 |
|---|---|
| 文档编号 | DSH-ADOBE-MCP-SPEC-001 |
| 版本 | v1.0（设计定稿） |
| 日期 | 2026-10-15 |
| 状态 | 待评审 |
| 目标读者 | 实现者、评审者、运维者 |
| 第一阶段范围 | Adobe Premiere Pro（Windows） |

---

## 1. 目标与非目标

### 1.1 项目目标

构建一个 Python 实现的 MCP（Model Context Protocol）服务器，让 DeepSeek Harness（下称 DSH）能够通过自然语言驱动 Adobe 视频制作软件完成实际生产操作。第一阶段聚焦 Adobe Premiere Pro，交付一条端到端可用链路：

> DSH 发出指令 → MCP 工具调用 → 宿主内脚本执行 → 工程被真实修改 → 返回结构化结果

具体可度量的成功标准（验收见 §14）：

1. DSH 能通过 stdio 接入该 MCP 服务器，工具可被正常发现与调用。
2. 能自动发现本机已安装的 Premiere Pro，并在未运行时按需启动。
3. 能完成"新建工程 → 导入素材 → 建序列 → 上时间线剪辑 → 导出成片"的完整链路。
4. 破坏性操作在执行前必须经过确认，且执行前存在可回滚的工程检查点。
5. 宿主崩溃、脚本超时、连接中断均不导致 MCP 服务器不可恢复。

### 1.2 非目标（v1 明确不做）

| 非目标 | 说明 |
|---|---|
| macOS 支持 | v1 仅 Windows；平台差异集中在一个适配层，为 v2 预留 |
| 云端/远程渲染 | 不引入任何网络服务，全部本地进程内通信 |
| 替代人的创意决策 | 工具只执行确定性操作，不做剪辑风格判断 |
| After Effects / Media Encoder 深度集成 | 架构预留 `HostAdapter` 接口；M3 仅接入 AME 的导出入队能力，不做完整的 AME 自动化 |
| 离线修改 `.prproj` | 见 §3.3，这是不可协商的架构约束 |
| 硬件的视频采集/上屏 | 超出扩展接口能力 |

### 1.3 范围外声明（Out of Scope Declaration）

**本项目的设计、文档与实现不包含、也不支持以下任何内容：**

- 获取、下载、分发、安装未获授权的 Adobe 软件；
- 绕过、禁用、伪造或规避 Adobe 的授权、激活与许可校验机制；
- 修改、重打包、替换宿主程序二进制或官方插件文件；
- 检测、读取或干预宿主的授权状态；
- 任何以"使用未授权副本"为前提的功能设计。

**架构层面的说明：** 本项目与宿主的授权状态在技术上完全解耦。MCP 服务器只能通过 Adobe 公开文档化的扩展接口（CEP / ExtendScript / 后续的 UXP）与宿主通信，这些接口不提供、本项目也不尝试获取任何授权信息。因此服务器既不判断、也不干预宿主的许可状态——这是接口能力的边界，而非本项目的功能特性。

**唯一的例外说明：** 开发期需要设置 `HKEY_CURRENT_USER\Software\Adobe\CSXS.<n>\PlayerDebugMode = "1"`，以便加载**未签名**的本地开发版 CEP 扩展。这是 Adobe 官方为扩展开发者提供的标准设置，仅影响扩展的签名校验，与宿主软件的授权无关，且不影响任何已签名扩展。

---

## 2. 术语表

| 术语 | 含义 |
|---|---|
| **宿主 / Host** | 被自动化控制的 Adobe 应用进程，如 Premiere Pro、Media Encoder |
| **CEP** | Common Extensibility Platform，Adobe 的 HTML/JS 扩展平台 |
| **面板 / Panel** | 运行在宿主内、由 CEP 加载的扩展 UI 与环境 |
| **ExtendScript** | Adobe 的脚本语言，基于 ECMAScript 3 扩展，操作宿主 DOM |
| **桥接 / Bridge** | Python 侧与宿主内面板之间的连接管理与请求路由组件 |
| **信封 / Envelope** | 桥接协议中的一次请求/响应的统一数据单元 |
| **工具 / Tool** | 暴露给 MCP 客户端的一个可调用函数 |
| **风险等级 / Tier** | 操作破坏性的分级：T0 只读 / T1 增量可逆 / T2 破坏性 |
| **检查点 / Checkpoint** | 破坏性操作前保存的工程副本 |
| **QE DOM** | Premiere Pro 内部未公开的脚本 API（`app.enableQE()`） |

---

## 3. 系统架构

### 3.1 三层结构

```
┌─────────────────────────────────────────────────────────┐
│  DSH (MCP Client)                                       │
└────────────────────────┬────────────────────────────────┘
                         │ stdio (JSON-RPC / MCP)
┌────────────────────────▼────────────────────────────────┐
│  L1  MCP 层  (Python 进程)                              │
│  · 工具 schema 与注册      · 风险分级与确认门           │
│  · 任务编排与状态          · 审计日志与 WAL             │
└────────────────────────┬────────────────────────────────┘
                         │ 内部函数调用
┌────────────────────────▼────────────────────────────────┐
│  L2  桥接层  (Python 进程内)                            │
│  · HostRegistry  宿主发现（Windows 注册表）             │
│  · CepDeployer   面板部署到用户 CEP 目录                │
│  · CepClient     WebSocket 长连接 / 请求配对 / 心跳      │
│  · JsxRunner     脚本求值、超时、重试、串行化            │
└────────────────────────┬────────────────────────────────┘
                         │ ws://127.0.0.1:<动态端口>
┌────────────────────────▼────────────────────────────────┐
│  L3  宿主内代理层  (CEP 面板)                           │
│  · Node 侧：WS Server、命令分发、结果序列化             │
│  · ExtendScript 侧：ops/*.jsx 操作宿主 DOM              │
└────────────────────────┬────────────────────────────────┘
                         │ ExtendScript DOM
┌────────────────────────▼────────────────────────────────┐
│  Premiere Pro 进程                                      │
└─────────────────────────────────────────────────────────┘
```

### 3.2 各层职责与边界

**L1（MCP 层）** 是唯一对外可见的层。它持有全部工具的输入/输出 schema，负责参数校验、风险分级、确认令牌签发、任务生命周期与审计。它**不**知道宿主的具体 API，只发命令信封。

**L2（桥接层）** 是唯一接触宿主的层。它持有连接状态，负责宿主发现、面板部署、连接建立、请求配对、超时与重连。它对上层暴露的是"执行一条命令并返回结果"这一单一原语。

**L3（宿主内代理层）** 是唯一接触宿主 DOM 的层。它是一段部署在宿主内的 CEP 扩展，把命令名映射到具体 DOM 操作。它不做业务判断、不做策略、不持有风险分级——**安全策略绝不能下放到这一层**，否则脚本错误即可绕过策略。

### 3.3 架构约束（硬性）

| 编号 | 约束 | 理由 |
|---|---|---|
| C-1 | 不得离线读写 `.prproj` 文件内容 | 该格式为 gzip 压缩的未公开 XML，版本间不稳定；离线改写极易产生不可恢复的工程损坏。所有写入必须经由运行中的宿主。 |
| C-2 | 每个宿主同时只允许一个求值在途 | ExtendScript 求值是同步的且阻塞宿主 UI 线程；并发求值会导致宿主无响应或状态错乱。 |
| C-3 | 风险分级只在 L1 判定 | L3 只执行已被授权的命令，不自行判断操作危险性。 |
| C-4 | 请求必须幂等可重放或明确标记为非幂等 | 超时后无法确定命令是否已执行，非幂等命令必须带幂等键。 |
| C-5 | 全部网络通信限定在 `127.0.0.1` | 不暴露任何局域网或公网监听端口。 |

### 3.4 数据流

一次典型调用的完整路径：

1. DSH 调用工具（如"把 A 素材插入到 B 序列的第 10 秒"）。
2. L1 校验参数 → 查风险表（T1）→ 写 WAL 记录 → 生成命令信封。
3. L2 把信封序列化，检查宿主连接状态，占用该宿主的求值锁。
4. 信封经 WebSocket 发送至面板，按 `id` 登记等待响应。
5. 面板 Node 侧收到后调用 `CSInterface.evalScript()`，把命令交给 ExtendScript。
6. ExtendScript 的 `dispatch.jsx` 路由到 `ops/timeline.jsx` 的对应处理器，操作宿主 DOM。
7. 结果（含错误堆栈）被序列化为 JSON 字符串，沿原路回传。
8. L1 把结果包装进统一返回信封，附上宿主状态与撤销提示，返回给 DSH。

---

## 4. 宿主发现与面板部署

### 4.1 宿主发现

通过 Windows 注册表枚举已安装的宿主，不使用文件系统猜测（避免误匹配非标准安装）：

| 宿主 | 注册表路径 | 取值 |
|---|---|---|
| Premiere Pro | `HKLM\SOFTWARE\Adobe\Premiere Pro\<版本号>` | `InstallPath` |
| Premiere Pro（32 位视图） | `HKLM\SOFTWARE\WOW6432Node\Adobe\Premiere Pro\<版本号>` | `InstallPath` |
| Media Encoder | `HKLM\SOFTWARE\Adobe\Adobe Media Encoder\<版本号>` | `InstallPath` |

**多版本处理：** 允许同时存在多个版本（如 2023 / 2024 / 2025）。配置项 `hosts.premiere.version` 选择目标；未指定时选取版本号最高者。**禁止**在不告知用户的情况下接管当前正在运行的任意版本——启动前必须确认目标版本，或在 `host_status` 中报告歧义。

**发现产物：** `HostDescriptor`，字段为：宿主类型、版本号、安装路径、可执行文件路径、CEP 宿主标识（如 `PPRO`）、当前是否运行、当前是否已连接。

### 4.2 CEP 面板部署

面板源在仓库 `cep-extension/` 目录，部署目标为**用户级** CEP 扩展目录：

```
%APPDATA%\Adobe\CEP\extensions\<extension-id>\
```

选择用户级而非系统级（`C:\Program Files (x86)\Common Files\Adobe\CEP\extensions\`）的原因：系统级需要管理员权限写入，用户级无需提权，且不会与其他用户或系统范围配置产生干扰。

**部署策略：**

1. 比较源目录与目标目录的清单哈希（文件名 + 大小 + 修改时间），一致则跳过——避免每次启动都做无谓写入。
2. 不一致时整体替换：先复制到临时目录，校验完整性，再原子替换目标目录。
3. 部署前校验 `manifest.xml` 的必需元素齐全（见 §4.3），不合规则拒绝部署并报错。
4. 保留上一版本用于回滚；部署失败时自动还原。
5. `dev_install_cep.ps1` 提供同样的逻辑供手动执行。

**关于 DSH 文件沙箱（待验证项）：** MCP 服务器是 DSH 启动的独立进程，其自身文件访问是否受 DSH 工作区沙箱约束，取决于 DSH 的 MCP 启动机制。**这一项在实现阶段必须实测确认**，不得假定。若受限，退路是：由用户手动运行一次 `scripts/dev_install_cep.ps1` 完成部署，MCP 服务器只做存在性检测。

### 4.3 manifest 必需元素

`cep-extension/CSXS/manifest.xml` 必须声明以下内容（描述而非代码，实现时按 CEP 规范书写）：

| 元素 | 必需值/说明 |
|---|---|
| `ExtensionManifest` 的 `ExtensionBundleId` | 唯一包标识，如 `com.dsh.adobe.bridge` |
| `ExtensionBundleVersion` | 语义化版本号 |
| `HostList` | 至少 `PPRO`，版本范围覆盖目标版本；AME 宿主在 M5 阶段加入 |
| `RequiredRuntime` | `CSXS` 版本需与目标宿主匹配 |
| `MainPath` | 面板入口 HTML |
| `CEFCommandLine` | 必须包含 `--enable-nodejs` 与 `--mixed-context`；后者是 CEP 9+ 中让面板 window 作用域可用 `require` 的前提 |
| `UI/Type` | `Panel`（可停靠面板） |
| `Geometry` | 面板初始尺寸 |

**注意：** `--mixed-context` 缺失是 CEP 开发中最常见的"`require` is not defined"根因，必须在部署前的 manifest 校验中作为硬性检查项。

---

## 5. 桥接协议

### 5.1 连接建立（握手）

固定端口会在多版本宿主并存或上次异常退出时冲突，因此采用**动态端口 + 握手文件**：

1. 面板启动时绑定 `127.0.0.1:0`，由操作系统分配空闲端口。
2. 面板生成一个随机令牌（每次启动刷新）。
3. 面板把连接信息写入握手文件：
   `%TEMP%\dsh-adobe-mcp\handshake-<宿主>-<进程号>.json`
   字段：端口、令牌、宿主进程号、宿主版本、面板版本、启动时间戳。
4. Python 侧监视该目录（文件系统事件 + 轮询兜底），读取握手文件后连接。
5. WebSocket 连接建立后的第一帧必须是认证帧，携带该令牌；校验失败立即断开。

**这样设计的安全收益：** 结合"仅绑定回环地址"与"每次启动的随机令牌"，同机其他进程无法冒充或劫持桥接连接。日志中**不得**记录令牌明文。

**过期清理：** 宿主异常退出会留下陈旧握手文件。Python 侧在读取时校验对应进程号是否存活，并清理陈旧文件。

### 5.2 信封格式

请求与响应统一使用一个扁平的 JSON 信封。字段定义如下：

| 字段 | 方向 | 类型 | 说明 |
|---|---|---|---|
| `id` | 双向 | 字符串 | 请求唯一标识（UUID），响应必须原样回填 |
| `cmd` | 请求 | 字符串 | 命令名，命名空间点分，如 `timeline.insert_clip` |
| `args` | 请求 | 对象 | 命令参数，由 L1 校验后透传 |
| `meta` | 请求 | 对象 | 幂等键、追踪号、dry-run 标志、调用者标识 |
| `ok` | 响应 | 布尔 | 整体成败 |
| `data` | 响应 | 任意 | 成功时的负载 |
| `error` | 响应 | 对象 | 失败时的 `code` / `message` / `line` / `script_excerpt` |
| `host` | 响应 | 对象 | 宿主版本、工程路径、是否有未保存改动 |
| `timing` | 响应 | 对象 | 宿主内耗时、往返耗时 |

### 5.3 求值串行化与超时

这是本设计中最容易出错的部分，规则必须严格执行：

1. **串行化（C-2）：** 每个宿主维护一把求值锁，同一时刻最多一个命令在途。L2 在发送前获取锁。
2. **超时语义：** 超时**不等于**命令失败。ExtendScript 没有取消机制，超时后命令可能仍在宿主内执行。
3. **超时后的处置：** 该桥接连接被标记为 `DESYNC`。所有后续命令立刻失败并提示先调用 `host_resync`。
4. **`host_resync`：** 重新读取宿主当前状态（工程路径、活动序列、播放头位置、工程脏标志），与 L1 记录的状态比对，产出差异报告，然后清除 `DESYNC`。
5. **非幂等命令（C-4）：** 带幂等键的命令在宿主侧记录已执行的键；重放同键命令时返回上次结果而不重复执行。

### 5.4 心跳与重连

- 面板每隔 `bridge.heartbeat_s`（默认 5 秒）发送心跳；L2 超过 3 个周期未收到则判定连接失效。
- 连接失效时：标记该宿主的全部在途命令为不确定状态，触发一次 `host_resync` 流程，并尝试重连（退避上限 30 秒）。
- 宿主进程退出时，握手文件被清理，连接自然失效。

---

## 6. ExtendScript 操作库

### 6.1 方言约束

ExtendScript 基于 ECMAScript 3，这是实现阶段最大的陷阱来源。编码约束：

| 不可用 | 替代方案 |
|---|---|
| `let` / `const` | 一律使用 `var` |
| 箭头函数、模板字符串、默认参数、解构 | 使用 ES3 语法书写 |
| 原生 `JSON` | 内置 `json2.jsx` 兼容层，统一走它做序列化/反序列化 |
| `Array.prototype.map` / `filter` / `indexOf` | 手写循环，或由兼容层补齐 |
| `String.prototype.trim` | 兼容层补齐 |
| `Promise` / `async` | 全部同步；异步由 L1 的任务模型承担 |
| `try/catch` 的 `error.stack` | 使用 `error.message`、`error.line`、`$.fileName` |

**校验手段：** 构建流程中必须有一个静态检查步骤，扫描 `jsx/**` 中的 ES5+ 语法（可用轻量 AST 检查或正则黑名单），发现即失败。

### 6.2 API 分层策略

宿主 DOM 分为两层，调用优先级严格如下：

**第一层：公开 DOM（默认启用）。** 有官方文档保证，稳定性可预期。覆盖：工程与素材箱操作、导入、序列创建与查询、轨道与片段读写、标记、效果参数读写、编码器接口。

**第二层：QE DOM（默认禁用，配置开启）。** `app.enableQE()` 暴露的内部接口，可完成公开 API 空缺的操作：在指定位置切分（razor）、插入/删除轨道、部分选择与移动操作。

启用条件与护栏：

- 配置项 `extendscript.enable_qe` 默认 `false`。
- 启用时 `host_status` 与每次调用返回中都带 `qe_enabled: true` 警告。
- QE 调用一律包在最外层 try/catch 中，且失败不得影响已完成的公开 DOM 操作。
- QE 相关工具在文档中单独标注"未公开接口，可能导致宿主崩溃"。

**禁止事项：** 不得为进一步的能力而加载非官方来源的插件二进制或注入宿主进程内存。能力边界止于公开 DOM 与 QE。

### 6.3 命令注册与契约

ExtendScript 侧的 `dispatch.jsx` 是命令路由表。为避免 L1 与 L3 两侧漂移：

- 仓库中维护一份机器可读清单 `cep-extension/jsx/commands.json`，列出全部命令名、参数签名、返回结构、风险等级建议值。
- L1 的工具定义与 `commands.json` 做**双向**契约测试（见 §13）。
- `dispatch.jsx` 的路由表必须与 `commands.json` 完全一致，缺失或多余都导致测试失败。

### 6.4 错误包装

ExtendScript 的错误信息极为贫瘠（常只有一个行号）。因此：

- 每个命令处理器用统一的包装器执行，捕获异常后返回结构化错误对象：错误消息、行号、文件、命令名、参数快照。
- 返回的 `script_excerpt` 携带失败处前后若干行脚本源码，便于定位。
- L1 收到响应后再附加 Python 侧的堆栈与上下文，最终写入审计日志与 WAL。

---

## 7. MCP 工具接口目录

### 7.1 统一返回信封

所有工具返回同一结构（字段为逻辑结构，非代码）：

| 字段 | 说明 |
|---|---|
| `ok` | 布尔，操作是否成功 |
| `data` | 成功时的业务负载 |
| `error` | 失败时的 `code` / `message` / `remedy`（可执行的修复建议） |
| `warnings` | 警告列表，如 QE 已启用、宿主版本未经验证 |
| `host_state` | 宿主版本、工程路径、脏标志、连接状态 |
| `undo_hint` | 如何撤销本次操作的具体指引 |

**`remedy` 与 `undo_hint` 是本设计的关键可用性设计**：AI 客户端在失败时最需要的是"下一步怎么办"，而不是错误码。

### 7.2 工具清单（56 个，按命名空间分组）

| 命名空间 | 工具 | 风险等级 |
|---|---|---|
| `host_` | `list`（枚举已安装宿主）、`status`（运行/连接状态）、`launch`、`wait_ready`、`resync`、`reload_panel`（开发用） | T0 / T1（`launch`） |
| `ppro_app_` | `info`（版本、语言、工程路径、脏标志）、`get_state`（活动序列、播放头、入出点、选中项）、`set_playhead` | T0（`set_playhead` 为 T1） |
| `ppro_project_` | `new`、`open`、`save`、`save_as`、`close`、`info`、`import`、`bin_create`、`bin_list`、`bin_move`、`item_find` | `new`/`open`/`save_as`/`close` 为 T2；`save`、`import`、`bin_*` 为 T1；`info`、`bin_list`、`item_find` 为 T0 |
| `ppro_sequence_` | `create`、`create_from_clips`、`list`、`info`、`set_active`、`set_in_out`、`set_work_area` | T1 |
| `ppro_timeline_` | `list_clips`、`clip_info`、`insert_clip`、`overwrite_clip`、`move_clip`、`trim_clip`、`remove_clip`、`razor`、`add_track`、`remove_track`、`add_marker`、`list_markers` | 读为 T0；`insert`/`overwrite`/`add_marker` 为 T1；`remove_clip`/`remove_track`/`razor` 为 T2 |
| `ppro_effects_` | `list_available`、`apply`、`get_params`、`set_param`、`apply_transition` | 读为 T0，写为 T1 |
| `ppro_media_` | `probe`、`relink`、`generate_proxies` | `probe` 为 T0，其余 T1 |
| `ppro_export_` | `list_presets`、`encode_sequence`、`encode_file` | `list_presets` 为 T0；`encode_*` 为 T2（批量导出） |
| `job_` | `list`、`status`、`cancel` | T0 |
| `safety_` | `confirm_operation`、`restore_checkpoint`、`list_checkpoints` | T0 |

**注：** `razor`（切分）被归为 T2 而非 T1，原因是它依赖 QE 接口且会改变片段结构；归为 T2 使其默认需要确认，符合"宁可多确认"的取向。

### 7.3 渐进式工具暴露

56 个工具一次性暴露会显著占用客户端上下文。因此设计**工具组分段启用**：

- **核心组**（始终启用）：`host_*`（6 个）、`ppro_app_info`、`ppro_project_info`、`job_*`（3 个）、`safety_*`（3 个），共 **14 个**。足够完成宿主管理、状态查询与任务管理。
- **可选组**（由配置开启）：`project_edit`、`timeline_edit`、`effects`、`export`、`media`。

配置项 `tools.enabled_groups` 控制。默认启用核心组 + `project_edit` + `timeline_edit`（覆盖主要生产链路）。

---

## 8. 安全模型

用户明确要求破坏性操作需要显式确认。本节是该需求的设计落地。

### 8.1 三级风险模型

风险等级**写死在 L1 的策略表中**，不在运行时由模型判断，也不下放到 L3。

| 等级 | 定义 | 处置 |
|---|---|---|
| **T0 只读** | 不改变宿主任何状态 | 直接执行，写审计日志 |
| **T1 增量/可逆** | 只增加内容，或改动可被宿主撤销栈覆盖 | 直接执行，写审计日志 + 返回 `undo_hint` |
| **T2 破坏性** | 删除内容、覆盖文件、改变工程路径、批量导出 | **不执行**。返回确认令牌 + 影响说明 |

### 8.2 T2 确认流程

1. 客户端调用 T2 工具。
2. L1 不执行任何宿主操作，而是：
   - 计算并返回**影响说明**：将影响的对象、数量、不可逆部分、目标路径；
   - 生成一次性确认令牌（带过期时间，默认 5 分钟，与操作参数哈希绑定）；
   - 返回 `dry_run` 形式的预期结果结构。
3. 客户端必须再次调用 `safety_confirm_operation`，携带令牌。
4. L1 校验令牌（存在、未过期、参数哈希匹配、未被使用）→ 执行检查点 → 执行操作 → 消费令牌。
5. 令牌一次性消费，重放无效。

**参数哈希绑定**是关键：防止确认了"A 操作"却执行了"B 操作"。

### 8.3 检查点机制

T2 操作执行前自动创建检查点：

- **主机制：** 对磁盘上的 `.prproj` 做文件级复制到检查点目录。相比 `saveAs`，文件复制不改变当前工程的路径、不触发宿主对话框、不污染宿主内部状态。
- **前置条件：** 若工程有未保存改动，先触发一次 `save`（这本身是 T1 并记录），再复制。若工程从未保存过（无磁盘文件），则退化为 `save_as` 到检查点路径并在差异报告中明确告知。
- **存储：** `%LOCALAPPDATA%\dsh-adobe-mcp\checkpoints\`，按工程名与时间戳命名。
- **保留策略：** 最多保留 `safety.max_checkpoints`（默认 20）份，超出按时间清理最旧的，清理动作写入日志。
- **恢复：** `safety_restore_checkpoint` 是 T2 操作，且恢复前会为**当前**状态再建一个检查点，避免"恢复"本身成为不可逆操作。

### 8.4 Dry-run

所有写操作支持 `dry_run` 标志。dry-run 时：

- L1 不签发命令给宿主，或只签发标记为 dry-run 的命令；
- L3 的处理器在 dry-run 模式下只做**前置条件校验**（目标是否存在、路径是否可写、参数是否越界），返回将要执行的动作清单，不触碰 DOM；
- 返回值结构与真实执行一致，便于客户端复用解析逻辑。

### 8.5 预写日志（WAL）

每条发给宿主的命令，在发送**之前**先落盘：

| 字段 | 说明 |
|---|---|
| 时间戳 / 追踪号 / 调用者 | 溯源 |
| 命令名 / 参数快照 | 复现输入 |
| 风险等级 / 是否经过确认 / 令牌号 | 合规审计 |
| 完整的 ExtendScript 源码文本 | **这是关键**——脚本原文比命令名有价值得多 |
| 结果 / 错误 / 宿主耗时 | 事后分析 |

WAL 采用追加写入的 JSONL 格式，按天分文件。WAL 是排查"AI 到底对工程做了什么"的唯一可靠依据。

### 8.6 路径与参数校验

- 所有文件路径参数在 L1 做规范化（`os.path.realpath`），拒绝包含 `..` 越界的路径。
- 导入/导出路径限制在配置允许的根目录白名单内（默认：用户目录与工程目录）。
- 禁止通过工具参数直接执行任意 ExtendScript —— **命令名必须在注册表中，参数必须通过 schema 校验**。这是防止"参数注入即可执行任意代码"的核心防线。

---

## 9. 任务与长操作

### 9.1 为什么需要任务模型

导出、合板、代理生成等操作耗时从数十秒到数小时。MCP 工具调用不应阻塞，且宿主侧 ExtendScript 无法提供进度回调。

### 9.2 设计

- **提交：** `ppro_export_encode_sequence` 等长操作返回 `job_id` 而非阻塞等待。
- **状态：** `job_status` 返回状态机取值 `pending` / `running` / `succeeded` / `failed` / `cancelled` / `unknown`，以及进度信息（若可得）、产物路径、日志片段。
- **进度来源：** 按可靠性排序：宿主编码器接口查询 > 监视输出文件增长 > 监视输出文件存在性。无法确定进度时明确返回 `unknown` 而**不猜测**。
- **取消：** `job_cancel` 尽力而为。若无法真正取消（编码器不支持），返回明确说明而非假装成功。
- **持久化：** 任务状态既存内存也追加写入磁盘日志，使 MCP 服务器重启后仍能报告历史任务。
- **并发上限：** 每宿主同时最多一个长任务；超出时入队而非并发，避免宿主资源争抢。

### 9.3 与求值锁的关系

长任务的"在途求值"只持续到提交那一刻；提交后锁即释放。任务的实际执行发生在宿主/编码器内部，由状态轮询跟踪。这是长任务不违反约束 C-2 的关键。

---

## 10. 配置

配置来源优先级：命令行参数 > 环境变量 > 配置文件 > 内置默认值。
配置文件位置：`%APPDATA%\dsh-adobe-mcp\config.toml`，项目根目录的 `config.toml` 作为开发期覆盖。

| 配置节 | 键 | 默认值 | 说明 |
|---|---|---|---|
| `server` | `log_level` | `info` | 日志级别 |
| | `log_dir` | `%LOCALAPPDATA%\dsh-adobe-mcp\logs` | 日志与 WAL 目录 |
| `hosts.premiere` | `enabled` | `true` | 是否启用该宿主 |
| | `version` | 空（自动选最高） | 目标宿主版本 |
| | `launch_on_demand` | `true` | 未运行时是否自动启动 |
| | `launch_timeout_s` | `120` | 启动并等待面板就绪的超时 |
| | `eval_timeout_s` | `30` | 单条命令求值超时 |
| `bridge` | `handshake_dir` | `%TEMP%\dsh-adobe-mcp` | 握手文件目录 |
| | `heartbeat_s` | `5` | 心跳间隔 |
| | `reconnect_backoff_max_s` | `30` | 重连退避上限 |
| `extendscript` | `enable_qe` | `false` | 是否启用未公开的 QE 接口 |
| | `es3_static_check` | `true` | 构建期 ES3 静态检查 |
| `safety` | `checkpoint_before_t2` | `true` | T2 前建检查点 |
| | `checkpoint_dir` | `%LOCALAPPDATA%\dsh-adobe-mcp\checkpoints` | 检查点目录 |
| | `max_checkpoints` | `20` | 检查点保留上限 |
| | `confirm_token_ttl_s` | `300` | 确认令牌有效期 |
| | `allowed_path_roots` | 用户目录、工程目录 | 路径白名单 |
| `tools` | `enabled_groups` | 核心 + `project_edit` + `timeline_edit` | 工具组分段启用 |
| `jobs` | `poll_interval_s` | `5` | 长任务状态轮询间隔 |
| | `max_concurrent_per_host` | `1` | 每宿主并发长任务上限 |

---

## 11. 日志与可观测性

| 输出 | 位置 | 内容 |
|---|---|---|
| 服务器日志 | `logs/server-YYYY-MM-DD.jsonl` | L1/L2 结构化事件，含追踪号 |
| WAL | `logs/wal-YYYY-MM-DD.jsonl` | 每条命令的完整脚本原文与结果（§8.5） |
| 面板日志 | `logs/panel-<宿主>-<进程号>.log` | 面板 Node 侧与 ExtendScript 侧的错误，宿主异常时是唯一线索 |
| 宿主控制台输出 | 面板内可选显示区 | 开发期调试用 |

**追踪号贯穿：** DSH 调用 → L1 信封 → L2 请求 → 面板 → ExtendScript → 返回，同一条链路同一个追踪号。任一环节出问题时，可用一个追踪号拉出完整链路。

**日志脱敏：** 握手令牌、用户目录路径中的用户名等敏感信息在写入前脱敏。

---

## 12. 错误处理与失败模式

| 失败模式 | 检测方式 | 处置 |
|---|---|---|
| 宿主未安装 | 注册表无对应项 | 工具返回 `HOST_NOT_INSTALLED` + 已发现的宿主列表 |
| 宿主未运行 | 进程检测 | `launch_on_demand` 时自动启动；否则返回 `HOST_NOT_RUNNING` + 启动指引 |
| 面板未加载 | 超时内无握手文件 | 返回 `PANEL_NOT_LOADED`；提示检查 CEP 扩展目录与调试模式设置 |
| 连接断开 | 心跳超时 | 标记在途命令不确定 → 自动重连 → 触发 `host_resync` |
| 求值超时 | `eval_timeout_s` 到期 | 标记 `DESYNC`；**不重试**（C-2/C-4）；要求显式 `host_resync` |
| 脚本异常 | 响应的 `error` 字段 | 返回结构化错误 + `remedy`；WAL 已记录脚本原文 |
| 宿主崩溃 | WebSocket 关闭 + 进程消失 | 标记该宿主全部任务失败；报告最后一次成功的命令；保留检查点供恢复 |
| 宿主弹模态对话框 | 求值超时不返回 | 同上；`remedy` 提示用户检查宿主窗口是否有未响应的对话框 |
| 令牌失效 | 校验失败 | 返回 `CONFIRM_TOKEN_INVALID`，要求重新发起 T2 调用 |
| 路径越界 | 规范化后不在白名单 | 拒绝执行，返回 `PATH_NOT_ALLOWED` |

**核心原则：** 任何失败都不得让 MCP 服务器进程进入不可恢复状态。所有失败路径都必须有明确的下一步动作（`remedy`），且必须可被 `host_resync` 或重启恢复。

---

## 13. 工程结构

```
dsh-adobe-mcp/
├─ pyproject.toml                  # Python ≥3.11；依赖：mcp SDK、websockets、pydantic、tomli
├─ README.md
├─ config.example.toml
├─ docs/
│  └─ specs/
│     └─ 2026-10-15-adobe-video-mcp-design.md   # 本文档
├─ src/dsh_adobe_mcp/
│  ├─ __init__.py
│  ├─ __main__.py                  # stdio 入口
│  ├─ server.py                    # MCP 应用装配、工具注册
│  ├─ config.py                    # 配置加载与校验
│  ├─ result.py                    # 统一返回信封
│  ├─ errors.py                    # 错误码与 remedy 映射
│  ├─ logging_setup.py             # 结构化日志 + WAL + 追踪号
│  ├─ hosts/
│  │  ├─ base.py                   # HostAdapter 抽象接口
│  │  ├─ registry.py               # 注册表发现（Windows）
│  │  ├─ premiere.py
│  │  └─ media_encoder.py          # M5
│  ├─ bridge/
│  │  ├─ deployer.py               # CEP 面板部署与校验
│  │  ├─ manifest_check.py         # manifest 必需元素校验
│  │  ├─ cep_client.py             # WebSocket 客户端、握手、心跳
│  │  ├─ protocol.py               # 信封定义、配对、认证帧
│  │  └─ jsx_runner.py             # 求值锁、超时、DESYNC、幂等键
│  ├─ tools/
│  │  ├─ host.py  app.py  project.py  sequence.py
│  │  ├─ timeline.py  effects.py  media.py  export.py
│  │  └─ jobs.py  safety.py
│  ├─ safety/
│  │  ├─ policy.py                 # 风险分级表（唯一来源）
│  │  ├─ confirm.py                # 确认令牌签发/校验/消费
│  │  ├─ checkpoint.py             # 检查点创建/恢复/清理
│  │  └─ paths.py                  # 路径规范化与白名单
│  └─ jobs/
│     ├─ manager.py
│     └─ models.py
├─ cep-extension/                  # 部署进宿主的 CEP 扩展
│  ├─ CSXS/manifest.xml
│  ├─ index.html
│  ├─ .debug
│  ├─ js/
│  │  ├─ main.js                   # 面板引导
│  │  ├─ ws-server.js              # Node WebSocket 服务
│  │  ├─ auth.js                   # 令牌校验
│  │  ├─ dispatch.js               # 命令 → evalScript 转发
│  │  └─ vendor/ws/                # 随扩展打包含 ws 库（CEP 内不可假定有网络）
│  └─ jsx/
│     ├─ commands.json             # 命令契约清单（唯一来源）
│     ├─ json2.jsx                 # ES3 JSON 兼容层
│     ├─ es3_compat.jsx            # trim/map/indexOf 等补齐
│     ├─ dispatch.jsx              # 路由表，必须与 commands.json 一致
│     ├─ wrap.jsx                  # 异常包装与结构化错误
│     └─ ops/
│        ├─ app.jsx  project.jsx  sequence.jsx
│        ├─ timeline.jsx  effects.jsx  media.jsx
│        ├─ export.jsx  qe.jsx     # qe.jsx 仅在 enable_qe 时载入
│        └─ util.jsx
├─ tests/
│  ├─ unit/                        # 策略、令牌、配置、信封、路径校验
│  ├─ contract/                    # 工具 ↔ commands.json ↔ dispatch.jsx 三方一致性
│  ├─ integration/                 # 标记 opt-in，需真机
│  ├─ chaos/                       # 强杀宿主、断连、超时场景
│  └─ fixtures/                    # 测试素材（ffmpeg 生成的彩条短片等）
└─ scripts/
   ├─ dev_install_cep.ps1          # 手动部署面板（沙箱受限时的退路）
   ├─ check_es3_syntax.py          # ES3 方言静态检查
   ├─ gen_commands_doc.py          # 由 commands.json 生成工具文档
   └─ smoke.py                     # 端到端冒烟
```

---

## 14. 测试策略

### 14.1 单元测试（无需宿主）

覆盖：风险分级表的完备性（每个工具都有等级，无遗漏）、确认令牌的生命周期（签发/过期/参数不匹配/重放）、配置加载与优先级、信封序列化与配对、路径规范化与白名单拒绝、错误码到 remedy 的映射。

### 14.2 契约测试（无需宿主，最高价值）

三方一致性是防止 L1/L3 漂移的核心手段：

1. `tools/*.py` 中注册的工具集合与 `cep-extension/jsx/commands.json` 中的命令集合**双向相等**。
2. `dispatch.jsx` 的路由表与 `commands.json` **完全一致**（利用 `commands.json` 生成路由表，从结构上消除漂移）。
3. 每个工具的风险等级与 `policy.py` 的策略表一致。
4. 工具输入 schema 的参数名校验规则与 `commands.json` 中的签名一致。
5. 工具 schema 快照测试——schema 发生变化时必须显式更新快照，防止无意破坏客户端兼容性。

### 14.3 集成测试（opt-in，需真机）

标记为 `integration`，默认跳过，由环境变量开启。冒烟链路：

1. 启动宿主并等待面板就绪；
2. 新建工程；
3. 导入 fixture 素材（ffmpeg 生成的短视频）；
4. 创建序列并把素材插入时间线；
5. 读取时间线片段列表，断言与预期一致；
6. 导出为短片段，断言输出文件存在且大小非零；
7. 关闭工程，不保存。

**前置检查：** 测试开始前必须确认宿主未持有用户的真实工程，避免测试污染用户工作。

### 14.4 混沌测试

- 求值在途时强杀宿主 → 断言服务器返回确定错误、进入可恢复状态、检查点可用。
- 拔掉连接（模拟面板崩溃）→ 断言心跳检测生效、自动重连、`host_resync` 可清除 `DESYNC`。
- 触发求值超时（注入 sleep）→ 断言**不重试**、标记 `DESYNC`、后续命令被正确拒绝。
- 磁盘写满/检查点目录不可写 → 断言 T2 操作被拒绝而非无检查点执行。

### 14.5 构建期检查

ES3 方言静态检查作为 CI 的强制门禁；`commands.json` 变更时文档自动重新生成并校验是否已提交。

---

## 15. 里程碑与验收标准

| 里程碑 | 交付内容 | 验收标准 |
|---|---|---|
| **M0 骨架** | stdio 服务器、配置、日志、`host_*` 工具、面板部署、握手与认证、心跳 | DSH 能接入并列出工具；`host_list` 正确报告已安装版本；`host_launch` 能启动 PPro 并等到面板就绪；手工断开面板后能自动重连 |
| **M1 只读自省** | `ppro_app_*`、`ppro_project_info`、`ppro_sequence_list/info`、`ppro_timeline_list_clips`、`ppro_media_probe` | 对用户真实工程能准确读出工程结构、序列列表、片段列表与素材元数据；契约测试全绿 |
| **M2 剪辑写入** | 导入、素材箱、序列创建、插入/覆盖/移动/裁剪、标记 | 冒烟链路 1–5 步全通过；dry-run 返回的动作清单与真实执行一致 |
| **M3 导出与任务** | AME 接入、`ppro_export_*`、任务模型与状态轮询 | 冒烟链路完整通过（含导出产物非空）；长任务不阻塞其他工具调用；宿主重启后任务历史仍可查 |
| **M4 安全层加固** | 风险分级落地、确认令牌、检查点、WAL、路径白名单 | T2 操作无确认令牌时**必须**被拒绝；检查点可成功恢复；WAL 能完整还原任一次调用的脚本原文；混沌测试全通过 |
| **M5 扩展** | 效果与参数、AME 独立宿主、QE 能力（配置开启） | 效果读写正确；启用 QE 时有明确警告；QE 崩溃场景不影响服务器存活 |

**M4 不得晚于任何面向用户的发布** —— 在安全层就位之前，只允许在内网/开发环境使用。

---

## 16. 风险登记册

| 编号 | 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|---|
| R-1 | ExtendScript 的 ES3 方言导致反复踩坑 | 高 | 中 | §6.1 编码约束 + 构建期静态检查门禁 |
| R-2 | QE 接口不稳定导致宿主崩溃 | 中 | 高 | 默认禁用；配置开启；警告透出；独立失败边界 |
| R-3 | 求值超时后状态不确定，后续操作基于错误假设 | 中 | 高 | 串行化 + `DESYNC` 状态 + 强制 `host_resync`；禁止自动重试 |
| R-4 | 多版本宿主并存导致接管错误版本 | 中 | 中 | 显式版本配置；歧义时报告而非猜测 |
| R-5 | CEP 在不同宿主版本中的 Node 能力差异 | 中 | 中 | manifest 校验 `--mixed-context`；面板启动自检并上报能力 |
| R-6 | 工程文件被破坏 | 低 | 极高 | 禁止离线改写（C-1）；T2 前强制检查点；恢复自身也可回滚 |
| R-7 | AME 未安装导致导出链路失效 | 中 | 中 | 双路径：宿主编码器接口为主，宿主内建导出为备 |
| R-8 | DSH 文件沙箱限制面板部署 | 中 | 中 | 列为待验证项；退路为手动部署脚本 |
| R-9 | 未签名扩展被宿主拒绝加载 | 高 | 低 | 开发期设置 `PlayerDebugMode`（官方开发设置，见 §1.3）；发布期考虑签名 |
| R-10 | 56 个工具挤占客户端上下文 | 中 | 中 | 工具组分段启用，默认只开核心 + 两条生产链路 |
| R-11 | AI 误删素材或覆盖工程 | 中 | 高 | T2 确认门 + 检查点 + 路径白名单 + WAL 审计 |
| R-12 | 参数注入导致任意脚本执行 | 低 | 极高 | 命令名白名单 + schema 严格校验；禁止透传任意脚本 |

---

## 17. 附录

### 17.1 关键路径速查

| 用途 | 路径 |
|---|---|
| CEP 用户级扩展目录 | `%APPDATA%\Adobe\CEP\extensions\` |
| CEP 系统级扩展目录 | `C:\Program Files (x86)\Common Files\Adobe\CEP\extensions\` |
| 握手文件目录 | `%TEMP%\dsh-adobe-mcp\` |
| 检查点目录 | `%LOCALAPPDATA%\dsh-adobe-mcp\checkpoints\` |
| 日志与 WAL | `%LOCALAPPDATA%\dsh-adobe-mcp\logs\` |
| 配置文件 | `%APPDATA%\dsh-adobe-mcp\config.toml` |
| 调试模式设置 | `HKCU\Software\Adobe\CSXS.<n>\PlayerDebugMode`（字符串 `"1"`） |
| PPro 安装路径 | `HKLM\SOFTWARE\Adobe\Premiere Pro\<版本>\InstallPath` |

### 17.2 实现阶段必须先验证的技术假设

以下假设在本文档中未经验证，实现前必须实测，结论回填本文档：

1. **A-1** DSH 启动的 MCP 服务器进程是否受 DSH 工作区文件沙箱约束（影响 §4.2 部署策略）。
2. **A-2** 目标 PPro 版本中 CEP 的 Node 运行时版本，以及 `ws` 库可否正常加载。
3. **A-3** 目标版本中 `app.encoder` 的可用性与 `encodeSequence` 的行为细节。
4. **A-4** `.prproj` 在宿主打开状态下做文件级复制是否一致可靠（检查点主机制的前提）。
5. **A-5** 目标版本 QE DOM 的 `razor` 等接口的实际行为与稳定性。
6. **A-6** `CSInterface.evalScript` 在单次返回中的字符串长度上限（大结果需要分块）。
7. **A-7** 宿主弹模态对话框时，求值是挂起还是抛异常（影响 §12 的超时处置）。

### 17.3 参考方向

实现时应以 Adobe 官方文档为准，主要包括：CEP 扩展开发文档（manifest schema、CSInterface API、宿主环境与调试）、Premiere Pro 脚本指南（Application/Project/Sequence/Track/TrackItem/Encoder 对象模型）、ExtendScript 工具与语言参考。QE 相关行为无官方文档，只能通过实测确认，且必须按 §6.2 的护栏使用。

---

## 18. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-10-15 | 初版设计定稿。范围：Premiere Pro、Windows、CEP + ExtendScript、单进程 stdio 多宿主路由。 |
