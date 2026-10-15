# dsh-adobe-mcp

让 AI 客户端（DeepSeek Harness / 任意 MCP 客户端）通过 **MCP** 驱动 **Adobe Premiere Pro**（Windows）
完成真实的生产操作：新建工程 → 导入素材 → 建序列 → 上时间线剪辑 → 导出成片。

设计说明书： [`docs/specs/2026-10-15-adobe-video-mcp-design.md`](docs/specs/2026-10-15-adobe-video-mcp-design.md)（DSH-ADOBE-MCP-SPEC-001 v1.1）
L3 接口契约：[`cep-extension/JSX_RUNTIME_CONTRACT.md`](cep-extension/JSX_RUNTIME_CONTRACT.md)

```
DSH (MCP Client)
      │ stdio (JSON-RPC / MCP)
┌─────▼──────────────────────────────────────────────┐
│ L1  MCP 层（Python）  工具 schema、风险分级、确认门、任务、WAL │
└─────┬──────────────────────────────────────────────┘
      │ 内部函数调用
┌─────▼──────────────────────────────────────────────┐
│ L2  桥接层（Python）  宿主发现、面板部署、握手认证、求值串行化 │
└─────┬──────────────────────────────────────────────┘
      │ ws://127.0.0.1:<动态端口>（仅回环）
┌─────▼──────────────────────────────────────────────┐
│ L3  宿主内代理（CEP 面板）  Node WS 服务 + ExtendScript ops  │
└─────┬──────────────────────────────────────────────┘
      │ ExtendScript DOM
   Premiere Pro 进程
```

---

## 1. 环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11（v1 仅 Windows，macOS 差异集中在一个适配层） |
| Python | ≥ 3.11（推荐 3.12） |
| 宿主 | Adobe Premiere Pro（已授权、已安装；注册表可见） |
| MCP 客户端 | DeepSeek Harness（或任意支持 stdio 的 MCP 客户端） |

> 本项目**不**获取/安装/破解 Adobe 软件，也**不**读取或干预宿主授权状态。它只使用 Adobe
> 公开文档化的扩展接口（CEP / ExtendScript）：这些接口本身不提供授权信息，本项目也不尝试获取。
> 开发期唯一的系统设置是 `HKCU\Software\Adobe\CSXS.<n>\PlayerDebugMode = "1"`，
> 那是 Adobe 官方给扩展开发者用来加载**未签名**扩展的标准设置。

## 2. 安装

```powershell
# 方式 A：conda
conda env create -f environment.yml
conda activate dsh-adobe-mcp

# 方式 B：pip
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 安装本包（提供 dsh-adobe-mcp 命令）
pip install -e .
```

`esprima` 在 PyPI 上只有源码包（sdist），安装时需要 `setuptools` 在位（conda 环境已自带）。

## 3. 配置

优先级：**命令行参数 > 环境变量 > 配置文件 > 内置默认值**。

```powershell
Copy-Item config.example.toml "$env:APPDATA\dsh-adobe-mcp\config.toml"
```

常用项：

| 配置 | 说明 |
|---|---|
| `hosts.premiere.version` | 目标宿主版本；留空自动选最高（多版本并存时 `host_status` 会报告歧义） |
| `hosts.premiere.launch_on_demand` | 未运行时是否自动启动（默认 true） |
| `extendscript.enable_qe` | 是否启用未公开的 QE 接口（**默认 false**；启用后可能导致宿主崩溃） |
| `tools.enabled_groups` | 工具组分段启用（默认 `core` + `project_edit` + `timeline_edit` = 45 个工具） |
| `safety.max_checkpoints` | 检查点保留数量（默认 20） |
| `safety.allowed_path_roots` | 导入/导出路径白名单根目录（默认用户目录 + 当前工程目录） |

环境变量形式：`DSH_ADOBE_MCP__TOOLS__ENABLED_GROUPS=core,export`（列表用逗号分隔）。

## 4. 接入 DSH

在 MCP 配置中登记（`command` 指向本环境的 Python，`args` 为 `-m dsh_adobe_mcp`）：

```json
{
  "mcpServers": {
    "adobe-video": {
      "command": "D:\\Conda_envs\\dsh-adobe-mcp\\python.exe",
      "args": ["-m", "dsh_adobe_mcp"],
      "env": { "DSH_ADOBE_MCP__SERVER__LOG_LEVEL": "info" }
    }
  }
}
```

如果**没有**执行过 `pip install -e .`（例如受沙箱限制装不上），在 `env` 里补一个
`"PYTHONPATH": "<仓库路径>\\src"` 即可，服务器本身不依赖安装形态。

登记后先跑一次接入验证（对应验收标准"DSH 能接入并列出工具"）：

```powershell
pwsh -File scripts\verify_mcp_stdio.ps1            # 默认 45 个工具
pwsh -File scripts\verify_mcp_stdio.ps1 -Groups core   # 只启用核心组时 14 个
```

新登记/改动配置后需要让 DSH 重新加载该 MCP 服务器（重启 `dsh web` 或重开会话）。

部署 CEP 面板（首次必须做一次；之后 MCP 服务器会自动保持同步）：

```powershell
pwsh -File scripts\dev_install_cep.ps1 -SetPlayerDebugMode
```

面板部署完成后**重启 Premiere Pro**，面板才会被加载。

## 5. 典型链路

```
host_list → host_status → host_launch → host_wait_ready
ppro_project_new(path)            # T2：先返回 impact + confirm_token
safety_confirm_operation(token)    # 确认后执行（自动建检查点）
ppro_project_import(paths=[...])
ppro_sequence_create(name="S1", preset_path="...")
ppro_timeline_insert_clip(item="\\Root\\clip.mp4", at_s=0)
ppro_timeline_list_clips()         # 用返回的 ref 继续操作
ppro_export_encode_sequence(...)   # T2：确认后返回 job_id
job_status(job_id)                 # 进度/产物；无法判定时返回 unknown
```

每个工具都返回统一信封，失败时**先读 `error.remedy`**：

```json
{
  "ok": false,
  "data": null,
  "error": {"code": "HOST_NOT_RUNNING", "message": "宿主进程未运行", "remedy": "调用 host_launch ..."},
  "warnings": [],
  "host_state": {"host": "premiere", "version": "25.0", "connected": false, "qe_enabled": false},
  "undo_hint": null
}
```

## 6. 安全模型（摘要）

| 等级 | 含义 | 处置 |
|---|---|---|
| **T0** | 只读 | 直接执行，写审计日志 |
| **T1** | 增量/可逆 | 直接执行，返回 `undo_hint` |
| **T2** | 删除内容 / 覆盖文件 / 改工程路径 / 批量导出 | **不执行**：返回影响说明 + 一次性确认令牌；`safety_confirm_operation` 确认后才执行，且执行前自动建检查点 |

* 确认令牌与操作**参数哈希绑定**（确认 A 不会执行成 B），一次性消费，默认 5 分钟过期。
* 检查点可直接文件级复制回收（`safety_list_checkpoints` / `safety_restore_checkpoint`），
  恢复前会为当前状态再建一个检查点。
* 每条发往宿主的命令**在发送前**写入 WAL（`logs/wal-YYYY-MM-DD.jsonl`），
  记录里含**本次调用的完整脚本原文**与处理器实现，这是事后核查"AI 到底改了什么"的依据。
* 参数注入无法执行任意脚本：命令名必须在契约中，参数必须通过 schema 校验。
* 桥接只监听 `127.0.0.1`，每次面板启动刷新随机令牌，令牌不写日志。

## 7. 构建期门禁

三道门禁，任一失败即阻断合入：

```powershell
python scripts/gen_jsx_dispatch.py --check   # 1) 契约 → L3 路由表是否同步
python scripts/check_es3_syntax.py           # 2) ExtendScript ES3 方言静态检查（esprima AST）
python scripts/gen_commands_doc.py --check   # 3) 命令契约与工具文档是否同步
python scripts/render_config_example.py --check
pytest -q                                    # 含 tests/test_dependencies.py（依赖三方同步）
```

依赖真源是 `pyproject.toml`；`requirements.txt` 与 `environment.yml` 必须与之同步，
由 `tests/test_dependencies.py` 强制校验。

## 8. 测试

测试分五层，越靠下越"重"，但全都不需要真实宿主（最后一层除外）：

| 目录 | 覆盖内容 | 是否需要宿主 |
|---|---|---|
| `tests/unit/` | 风险表、错误与 remedy、配置、确认令牌、路径白名单、检查点、任务模型、日志脱敏、协议帧、manifest 校验、部署器、宿主发现、ES3 检查器 | 否 |
| `tests/contract/` | 三方一致性（工具 ↔ `commands.json` ↔ `dispatch.jsx`）、schema 快照、文档与门禁脚本、MCP 协议接入 | 否 |
| `tests/e2e/` | L1↔L2 全链路（真实 CepClient + 真实 WebSocket + 假面板）：确认门、检查点、dry-run、QE 门禁、任务生命周期、WAL | 否（用假面板） |
| `tests/chaos/` | 求值超时→DESYNC、断连、宿主被杀、检查点不可用、任务通道失败 | 否（用假面板） |
| `tests/integration/` | 真机 7 步链路（§14.3） | **是** |

```powershell
pytest -q                              # 前四层（默认）
pytest -q -m chaos                     # 只看混沌场景
pytest -q --integration                # 含真机链路（需先看下面的前置条件）

# L3（面板 + ExtendScript）自测：用伪 CEP / 伪宿主跑真实 JSX 与真实面板代码
pwsh -File tests\l3\panel\verify.ps1
$env:ELECTRON_RUN_AS_NODE="1"
& "F:\DeepseekHarness\DeepSeek Harness.exe" tests\l3\jsx\run.js

# 真机冒烟（等价链路，输出更适合人工观察）
python scripts\smoke.py --yes
```

真机集成测试的前置条件（说明书 §14.3）：**宿主里不能有用户的真实工程**，
否则测试会污染用户的工作；测试自身会先检查这一点，不满足直接失败。
素材用 `pwsh -File tests/fixtures/make_fixtures.ps1` 生成（需要 ffmpeg）。

本机没有 `node.exe` 时，可用 DSH 自带的 Electron 充当 Node（上面第二条命令即是）。

### 开发环境注意事项

这些是实测踩到的、会浪费时间的坑，记录下来省下一次排查：

1. **pytest 的临时目录要放在仓库内**。本环境对系统临时目录的长路径形式不做放行，
   `tmp_path` 默认落在 `%TEMP%` 下会直接权限失败。`tests/conftest.py` 因此用
   `SandboxTmpFactory` 覆盖了 `tmp_path_factory`，把基准目录固定为 `.pytest-work/`。
2. **不要用 `os.mkdir(path, 0o700)` 建目录**。沙箱会拒绝对这类目录做枚举
   （`PermissionError: WinError 5`），而 pytest 内置的临时目录恰好用 0o700 创建——
   这正是上面第 1 条要替换它的原因。
3. **Electron 当 Node 用时，不要把子进程 stdout 捕获进 PowerShell 变量**。
   它会在约 100ms 后被提前终止（`$LASTEXITCODE` 为空），长脚本只输出前几行；
   请重定向到文件（`Start-Process -RedirectStandardOutput ...`）。
4. **本环境禁止对子进程使用管道 stdio**：因此 `tests/contract/test_mcp_stdio.py`
   用进程内传输验证 MCP 协议层，CLI 入口本身用不捕获输出的方式单独验证。


## 9. 排障

| 症状 | 处置 |
|---|---|
| `PANEL_NOT_LOADED` | 运行 `host_status` 看部署状态；未部署则 `pwsh -File scripts\dev_install_cep.ps1`，设置 PlayerDebugMode 后**重启宿主** |
| 面板里 `require is not defined` | manifest 缺 `--mixed-context`（部署前校验会拦下，见 `bridge/manifest_check.py`） |
| `BRIDGE_TIMEOUT` | **不代表命令失败**。先 `host_resync` 比对差异，禁止直接重试；若宿主有模态对话框先手工关闭 |
| `BRIDGE_DESYNC` | 调 `host_resync` 清除 |
| `HOST_AMBIGUOUS_VERSION` | 显式给出 `host="premiere:25.0"` 或配置 `hosts.premiere.version` |
| `PATH_NOT_ALLOWED` | 把文件放到用户目录或当前工程目录，或扩充 `safety.allowed_path_roots` |
| `CHECKPOINT_FAILED` | 检查点目录不可写/空间不足；T2 操作在无检查点时**不会执行**（刻意设计） |
| `QE_DISABLED` | 需要 QE 的能力默认关闭；确需时设 `extendscript.enable_qe=true`，注意风险 |

日志与产物位置：

| 用途 | 路径 |
|---|---|
| 服务器日志 | `%LOCALAPPDATA%\dsh-adobe-mcp\logs\server-YYYY-MM-DD.jsonl` |
| WAL（脚本原文） | `%LOCALAPPDATA%\dsh-adobe-mcp\logs\wal-YYYY-MM-DD.jsonl` |
| 面板日志 | `%LOCALAPPDATA%\dsh-adobe-mcp\logs\panel-<宿主>-<进程号>.log` |
| 检查点 | `%LOCALAPPDATA%\dsh-adobe-mcp\checkpoints\` |
| 任务状态 | `%LOCALAPPDATA%\dsh-adobe-mcp\jobs\` |
| 握手文件 | `%TEMP%\dsh-adobe-mcp\` |

## 10. 目录结构

见说明书 §13.2。本实现相对该结构的**新增模块**（都是为了落地说明书自身的要求）：

| 新增 | 原因 |
|---|---|
| `src/dsh_adobe_mcp/contract.py` | 加载并校验 `commands.json`，是"三方一致性"门禁的 Python 侧入口（§6.3、§14.2） |
| `src/dsh_adobe_mcp/bridge/service.py` | 把发现/部署/客户端/求值器装配成 L2 对 L1 的单一入口（§3.2 的"单一原语"） |
| `src/dsh_adobe_mcp/tools/context.py` | 唯一的调用流水线：避免"某个工具忘了做安全检查"（§8） |
| `src/dsh_adobe_mcp/tools/registry.py` | 工具与风险表的一一对应校验 + 工具组分段注册（§7.3） |
| `cep-extension/JSX_RUNTIME_CONTRACT.md` | L1↔L3 的**函数级**接口契约（说明书只写到信封层） |
| `scripts/gen_jsx_dispatch.py` | 由契约生成 L3 路由表，从结构上消除 L1/L3 漂移（§14.2.2） |
| `scripts/render_config_example.py` | 示例配置与配置模型同步的门禁 |
| `tests/l3/**` | 用伪造宿主在 Node 里跑真实 JSX / 面板代码，无真机时也能验证 L3 逻辑 |

## 11. 与说明书的偏差（已记录，均有理由）

| 项 | 说明书 | 本实现 | 理由 |
|---|---|---|---|
| 构建后端 | hatchling | setuptools | 本环境无网络、无法安装 hatchling；两者同为 PEP 621 后端，`pip install -e .` 已实测通过 |
| `qe.jsx` 载入方式 | "仅在 enable_qe 时载入" | 随 dispatch 一并载入，**双重门禁**（L1 配置 + L3 运行时开关）后才可能调用 | `#include` 是静态预处理，无法条件载入；把门禁放在调用点比放在载入点更可靠（载入即报错会掩盖其它问题）。不调用 `enableQE()` 时无任何宿主副作用 |
| `ppro_sequence_list/info` 风险 | 表格标 T1 | 保持 T1，但 `undo_hint` 如实写"未改变状态" | 遵守说明书表格，不擅自降级；同时不误导使用者 |
| `ppro_app_get_state/set_playhead` 分组 | 未指定 | 归入 `timeline_edit` | 二者读写的都是序列时间线状态；核心组仍严格保持 14 个工具（§7.3） |
| 配置键 | §10 的表格 | 追加 `server.console_log_level`、`bridge.handshake_poll_s/connect_timeout_s/auth_timeout_s/max_response_chars`、`jobs.state_dir/max_queued_per_host`、`cep.*`、`export.preset_dirs` | 说明书 §5.1 已规定 0.5s 轮询、§5.4 心跳判定、§9.2 并发上限等行为，实现时需要可配置入口；`config.example.toml` 中逐项有注释 |
| `ppro_export_list_presets` 实现位置 | 未指定 | L1 侧扫描 AME 预设目录（可由 `export.preset_dirs` 覆盖） | 宿主公开 API 不提供预设枚举；实现里如实报告扫描过的目录 |

## 12. 待真机验证的技术假设（说明书 §17.2）

| 编号 | 假设 | 当前状态 |
|---|---|---|
| A-1 | DSH 启动的 MCP 服务器是否受工作区文件沙箱约束 | **部分验证**：部署器在写入失败时返回 `PANEL_DEPLOY_BLOCKED` 并给出手工部署退路；需在目标环境实测 |
| A-2 | 目标 PPro 版本 CEP 的 Node 运行时版本、`ws` 可否加载 | **已规避**：面板自带最小 RFC6455 实现（`js/vendor/ws`），只用 Node 内置模块 |
| A-3 | `app.encoder` 可用性与 `encodeSequence` 行为 | 待真机；实现做了特性检测，不可用时返回 `UNSUPPORTED_API` 并给 remedy |
| A-4 | 打开状态下文件级复制 `.prproj` 是否一致可靠 | **已加护栏**：复制大小不一致即拒绝，返回 `CHECKPOINT_FAILED`（宁可拒绝也不给坏检查点） |
| A-5 | QE 的 `razor` 等接口行为与稳定性 | 待真机；默认关闭、双重门禁、风险透出 |
| A-6 | `evalScript` 单次返回字符串长度上限 | **已加护栏**：超过 `bridge.max_response_chars` 返回 `RESULT_TOO_LARGE`，**不静默截断** |
| A-7 | 宿主弹模态对话框时求值是挂起还是抛异常 | 待真机；两种情况的处置都已实现（超时 → DESYNC + 提示检查对话框） |

## 13. 许可与免责

本仓库为工程实现与文档；使用它操作真实工程前请确保已有可用的工程备份。
T2 操作的检查点机制是最后一道防线，但它**不是备份系统的替代品**。
