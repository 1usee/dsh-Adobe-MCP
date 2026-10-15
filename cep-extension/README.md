# DSH Adobe Bridge —— CEP 面板（L3 宿主内代理层）

宿主内的 CEP 扩展。**面板是 WebSocket 服务端**（L1 Python 侧是客户端）：
只绑定 `127.0.0.1:0` 拿动态端口，把连接信息写进握手文件，L1 轮询发现后连上来。

接口规范见 `JSX_RUNTIME_CONTRACT.md`，设计依据见 `docs/specs/2026-10-15-adobe-video-mcp-design.md`。

## 目录

```
cep-extension/
├─ CSXS/manifest.xml        扩展清单（§4.3 必需元素；含 --enable-nodejs / --mixed-context）
├─ index.html               面板入口（MainPath）
├─ .debug                   开发期 CEF 调试端口 8092
├─ lib/csinterface-shim.js  CSInterface 降级实现（官方库缺失时兜底，见文件头注释）
└─ js/
   ├─ main.js               引导：自检 → 起服务 → 写握手文件 → 心跳 → 退出清理
   ├─ ws-server.js          桥接服务端：认证、帧校验、心跳、路由
   ├─ dispatch.js           命令分发 + evalScript 串行化（node 命令就地实现）
   ├─ config.js             运行期配置（默认值 + L1 下发覆盖）
   ├─ logger.js             面板日志（行式落盘 + 内存尾部 + 令牌脱敏）
   ├─ auth.js               令牌生成与常量时间比较
   └─ vendor/ws/             自带最小 RFC6455 服务端（离线可用，零第三方依赖）
```

面板日志：`%LOCALAPPDATA%\dsh-adobe-mcp\logs\panel-<宿主>-<进程号>.log`

## 部署（用户级扩展目录）

目标目录（无需管理员权限，说明书 §4.2）：

```
%APPDATA%\Adobe\CEP\extensions\com.dsh.adobe.bridge\
```

1. 把 `cep-extension/` 的**内容**复制到上面的目录（CSXS、index.html、js、lib 都在根下）。
2. 确认 `CSXS/manifest.xml` 里的 `ExtensionBundleId` 与目录名一致（`com.dsh.adobe.bridge`）。
3. 编译期/部署前跑一次 `python scripts/gen_jsx_dispatch.py --check`，确认 `jsx/` 与契约同步。
4. 完全退出并重启宿主（Premiere Pro），然后 **窗口 > 扩展 > DSH Adobe Bridge**。

仓库脚本 `scripts/dev_install_cep.ps1` 做同样的复制与清单校验。

## PlayerDebugMode（开发期必需）

未签名扩展不会被 CEP 加载。开启方式（Adobe 官方给扩展开发者的标准设置）：

```powershell
# n = CSXS 主版本，如 9 / 10 / 11（对应宿主 CEP 版本）
New-ItemProperty -Path "HKCU:\Software\Adobe\CSXS.11" -Name PlayerDebugMode -Value "1" -PropertyType String -Force
```

只影响扩展**签名校验**，与宿主授权无关；本项目不读取、不判断、不干预授权状态（说明书 §1.3）。

## 排障

| 现象 | 检查 |
|---|---|
| 面板不出现 | 目录名/`ExtensionBundleId` 是否一致；PlayerDebugMode 是否开了；宿主是否重启过 |
| `require is not defined` | `manifest.xml` 的 `CEFCommandLine` 是否**同时**有 `--enable-nodejs` 与 `--mixed-context` |
| L1 报 `PANEL_NOT_LOADED` | `%TEMP%\dsh-adobe-mcp\` 下是否有 `handshake-<host>-<pid>.json`；面板日志是否写成功 |
| 连接被立刻关闭 / `auth_failed` | 令牌是否过期（面板每次启动刷新令牌，必须重读握手文件）；L1 是否读了陈旧握手文件 |
| 面板显示「非 CEP 环境」 | 说明面板被当普通网页打开了；真实宿主机里 `window.__adobe_cep__` 一定存在 |
| 命令报 `HANDLER_MISSING` | `jsx/` 未部署完整，或 `dispatch.jsx` 与 `commands.json` 不同步 |
| 命令报 `RESULT_TOO_LARGE` | 返回值超过 `eval_max_chars`（默认 100000）。面板**不截断**，请缩小查询范围或分页 |
| `dev_install_cep.ps1` 拒绝部署，报 `MANIFEST_RUNTIME` / `MANIFEST_MAINPATH` | 见下方「已知跨模块不一致」 |

### 已知跨模块不一致（需 L1 侧修正，非本目录问题）

L1 的清单校验器 `src/dsh_adobe_mcp/bridge/manifest_check.py` 与 CEP 标准写法有 4 处命名不符，
会让**完全合规**的 manifest 被判为不合规（其中 2 处是阻断项）：

| 位置 | L1 当前写法 | CEP 标准 / 本 manifest 写法 |
|---|---|---|
| RequiredRuntime 子元素 | `root.iter("Runtime")` | 子元素名是 `RequiredRuntime`，其**文本**为 `CSXS` |
| 运行时名字取值 | `runtime.get("Name")` | 名字来自元素**文本**（`<RequiredRuntime Name="CSXS">` 才是属性写法） |
| 运行时版本取值 | `runtime.get("Version")` | 版本来自元素**文本**（`<RequiredRuntime Version="9.0">` 才是属性写法） |
| MainPath 定位 | `root.find("MainPath")` + `.get("Value")` | 位置是 `DispatchInfo/Resources/MainPath`，值是元素**文本** |

实测：把上述 4 处换成「兼容属性与文本两种写法 + `.//MainPath` 递归查找」之后，
同一个 manifest 在校验器下 `ok=True`、无阻断项、无警告。

调试端口：开了 PlayerDebugMode 且 `.debug` 存在时，用浏览器打开 `http://127.0.0.1:8092` 检查面板 DOM 与控制台。
正式使用请从部署目录移除 `.debug`，避免长期开放调试端口。

## 在宿主外运行 Node 侧自测

本机没有 `node.exe`，用 DSH 自带的 Electron 当 Node 用（Node 24）。一键跑完五组验证：

```powershell
pwsh -File tests\l3\panel\verify.ps1
```

依次为：面板自测（153 项）、mini-ws 自测（17 项）、每个 `.js` 的 `--check`、
manifest XML 解析与 §4.3 必需元素自查、`index.html` 引用脚本存在性。单独运行：

```powershell
$env:ELECTRON_RUN_AS_NODE = "1"
$exe = "F:\DeepseekHarness\DeepSeek Harness.exe"
& $exe cep-extension\js\vendor\ws\selftest.js    # mini-ws 自测
& $exe tests\l3\panel\run.js                     # 面板 Node 侧自测（真实面板代码 + 伪 CEP）
```

面板自测结果另写入 `tests/l3/panel/last-run.json`（含 `exit_code`），便于独立核对。

> **⚠️ 本机运行注意（已实测）**：Electron 二进制在「把子进程 stdout 捕获进 PowerShell 变量」
> 时会被**提前终止**（约 100ms，`$LASTEXITCODE` 为空），长脚本会因此只输出前几行。
> 必须重定向到文件：`Start-Process -FilePath $exe -ArgumentList "..." -NoNewWindow -Wait
> -RedirectStandardOutput out.txt -RedirectStandardError err.txt`。该二进制还是 GUI 子系统程序，
> `-PassThru` 的 `ExitCode` 可能为空，故以 `last-run.json` 的 `exit_code` 为准。

## 边界声明

- 只使用 Node 内置模块，无第三方运行时依赖；按 Node 12 可用语法书写（不带 `??`、可选链、`.at()`）。
- 桥接通信只在 `127.0.0.1`；令牌每次启动刷新，**绝不写入日志**（日志只记指纹）。
- 面板不做风险分级、不做策略判断——安全策略只在 L1（说明书约束 C-3）。
- 不读取、不判断、不干预宿主授权状态（说明书 §1.3）。
