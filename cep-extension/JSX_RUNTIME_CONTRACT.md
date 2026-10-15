# CEP 面板（L3）运行时契约

本文件是 L1（Python/MCP 层）与 L3（宿主内 CEP 面板）之间的**接口定义**，与
`jsx/commands.json`（命令契约）配套使用。任何实现都必须满足这里列出的名称与结构，
否则 L1 侧会直接报 `HANDLER_MISSING` / `PROTOCOL_*` 类错误。

设计说明书：`docs/specs/2026-10-15-adobe-video-mcp-design.md`（下文简称"说明书"）。
本文件只补充说明书未展开到函数级别的细节。

---

## 1. 进程与文件布局

```
cep-extension/
├─ CSXS/manifest.xml      CEP 扩展清单（说明书 §4.3）
├─ index.html             面板入口（MainPath）
├─ .debug                 调试端口配置（开发期）
├─ js/                    Node 侧（支持 ES2015+，随宿主 CEP 运行时版本而定）
│  ├─ main.js             面板引导：自检 → 启动 WS 服务 → 写握手文件 → 注册心跳
│  ├─ ws-server.js        WebSocket 服务封装（认证、请求分发、心跳）
│  ├─ auth.js             令牌校验
│  ├─ dispatch.js         命令 → CSInterface.evalScript("DSH.dispatch(...)")
│  ├─ config.js           运行期配置（由 L1 下发）
│  ├─ logger.js           面板日志（同时写文件与面板控制台）
│  └─ vendor/ws/          随扩展打包的最小 WebSocket 服务实现（离线可用）
└─ jsx/                   ExtendScript 侧（**仅 ES3**，说明书 §6.1）
   ├─ commands.json        命令契约（唯一真源）
   ├─ dispatch.jsx         **自动生成**（scripts/gen_jsx_dispatch.py），勿手工编辑
   ├─ json2.jsx            ES3 JSON 兼容层
   ├─ es3_compat.jsx       trim/map/filter/indexOf/Object.keys 等补齐
   ├─ wrap.jsx             异常包装与结构化错误
   ├─ util.jsx             时间、引用解析、序列/条目查找、宿主快照
   └─ ops/*.jsx            各命令处理器（业务实现）
```

---

## 2. 连接建立（对应说明书 §5.1）

1. 面板启动时用 `net` 在 `127.0.0.1:0` 上监听，由操作系统分配端口。
2. 生成随机令牌（每次面板启动刷新，长度 ≥ 32 个十六进制字符）。
3. 写握手文件：`%TEMP%\dsh-adobe-mcp\handshake-<host>-<pid>.json`

   ```json
   {
     "protocol": 1,
     "port": 51234,
     "token": "<hex>",
     "host": "PPRO",
     "pid": 12345,
     "host_version": "25.0.0",
     "panel_version": "1.1.0",
     "contract_schema_version": 1,
     "started_at": 1730000000.0
   }
   ```

   * `pid` **必须是宿主进程号**。CEP 的 Node 运行时内嵌在宿主进程中，因此
     直接用 `process.pid` 即可（这是 Python 侧用 psutil 做存活校验的依据）。
   * `token` 只写握手文件，**不得**写入任何日志（说明书 §11 脱敏要求）。
4. Python 侧以 0.5s 间隔轮询该目录，读取后连接 `ws://127.0.0.1:<port>`。
5. 面板退出（`window.onunload` / `beforeunload`）时删除握手文件。

## 3. WebSocket 帧协议

所有帧都是 **UTF-8 JSON 文本帧**。Python 是客户端，面板是服务端。

| 方向 | 帧 | 结构 |
|---|---|---|
| Python → 面板 | 认证帧（**必须是连接后的第一帧**） | `{"type":"auth","protocol":1,"token":"...","client":"dsh-adobe-mcp","client_version":"1.1.0","config":{...}}` |
| 面板 → Python | 认证结果 | `{"type":"auth_ok","protocol":1,"panel_version":"1.1.0","contract_schema_version":1,"capabilities":{...}}` |
| 面板 → Python | 认证失败后立即关闭 | `{"type":"auth_failed","reason":"bad_token"}` |
| Python → 面板 | 请求 | `{"type":"req","id":"<uuid>","cmd":"project.import","args":{...},"meta":{"traceId":"...","dryRun":false,"idempotencyKey":null}}` |
| 面板 → Python | 响应 | `{"type":"res","id":"<uuid>","ok":true,"data":{...},"error":null,"host":{...},"warnings":[],"timing":{"host_ms":12,"panel_ms":3}}` |
| 面板 → Python | 心跳（每 `heartbeat_s`） | `{"type":"heartbeat","ts":1730000000.0,"uptime_ms":123456,"inflight":0}` |
| Python → 面板 | 配置下发 | `{"type":"config","config":{"qe_enabled":false,"log_level":"info","log_dir":"..."}}` |

要求：

* 认证失败或 3 秒内未收到认证帧 → 关闭连接，不泄漏任何信息。
* 除认证帧外，Python 在认证完成前发送的任何帧都视为协议违规。
* 收到未知 `type` → 回 `{"type":"error","reason":"unknown_frame_type"}` 并保持连接。
* 响应必须回填请求的 `id`；`host` 字段来自 ExtendScript 侧的宿主快照（见 §5）。

## 4. Node 侧必须提供的接口（`js/*.js`）

| 文件 | 必须导出/提供 |
|---|---|
| `js/main.js` | 面板引导。无导出要求，但必须完成：能力自检、WS 服务启动、握手文件写入、心跳定时器、`CSInterface` 事件注册、配置应用。 |
| `js/ws-server.js` | `createBridgeServer({ token, config, onRequest })` → `{ port, close() }`；内部负责认证、帧校验、心跳、请求路由。 |
| `js/auth.js` | `createToken()` → 随机 hex 字符串；`verifyToken(expected, actual)` → 常量时间比较。 |
| `js/dispatch.js` | `dispatchCommand(cmd, args, meta)` → `Promise<{ok, data, error}>`；内部用 `CSInterface.evalScript` 调 `DSH.dispatch`。**Node 侧命令**（`commands.json` 中 `executor: "node"`）在这里直接实现，不下发 ExtendScript。 |
| `js/config.js` | `setConfig(obj)` / `getConfig()`，缺省值来自 manifest 与 L1 下发。 |
| `js/logger.js` | `log(level, message, extra)`；写 `logs/panel-<host>-<pid>.log` 行式日志，并把最后 N 行保留在内存里供诊断。 |
| `js/vendor/ws/index.js` | 见 §7。 |

### 4.1 `dispatch.js` 调用 ExtendScript 的硬性要求

```js
// 正确做法：把整个请求序列化成一个字符串，再用 JSON.stringify 生成 JS 字符串字面量
const payload = JSON.stringify({ id, cmd, args, meta: { traceId, dryRun, idempotencyKey } });
const literal = JSON.stringify(payload)
  .replace(/\u2028/g, "\\u2028")   // ES3 字符串字面量里 U+2028/U+2029 是换行
  .replace(/\u2029/g, "\\u2029");
const script = `DSH.dispatch(${literal})`;
CSInterface.evalScript(script, (result) => { /* result 是 JSON 字符串或 "EvalScript error." */ });
```

* `evalScript` 的回调参数是**字符串**；返回 `"EvalScript error."` 时必须转成结构化错误
  `{code: "EVAL_SCRIPT_ERROR", message, remedy}`，不得当成正常数据。
* 返回字符串长度上限（说明书 A-6）尚未实测：默认按 100000 字符处理，超过时返回
  `{code: "RESULT_TOO_LARGE", ...}`，并在 `remedy` 中说明需要分块读取，**不得静默截断**。
* 面板必须串行化 `evalScript` 调用（同一时刻一个在途）：ExtendScript 求值是同步且阻塞
  宿主 UI 线程的（说明书约束 C-2）。L1 侧已按宿主加锁，面板侧再做一次队列保护。

## 5. ExtendScript 侧必须提供的全局接口（`jsx/*.jsx`）

每个文件都以这段固定写法开头（ES3 无模块系统，全部挂在全局 `DSH` 上）：

```jsx
if (typeof DSH === "undefined") { var DSH = {}; }
```

| 提供者 | 必须提供的成员 |
|---|---|
| `json2.jsx` | `DSH.json.parse(text)`、`DSH.json.stringify(value)`（ES3 实现，不依赖原生 `JSON`） |
| `es3_compat.jsx` | `Array.prototype.map/filter/indexOf/forEach/trim`、`String.prototype.trim`、`Object.keys`、`Date.now` 的补齐（若宿主缺失） |
| `wrap.jsx` | `DSH.fail(code, message, remedy, extra)`（抛出结构化错误）、`DSH.wrap(command, handler, args, ctx, traceId, started)` → 响应对象、`DSH.wrapError(command, traceId, code, message, started, remedy)`、`DSH.wrapParseFailure(rawText, error, started)`、`DSH.hostSnapshot()` |
| `util.jsx` | `DSH.config`、`DSH.setConfig(cfg)`、`DSH.qeEnabled()`、`DSH.ticks(seconds)`、`DSH.seconds(ticks)`、`DSH.parseRef(ref)`、`DSH.findSequence(selector)`、`DSH.findProjectItem(ref)`、`DSH.treePath(item)`、`DSH.nowMs()` |
| `ops/*.jsx` | 契约中声明的全部处理器函数（全局函数，名如 `timeline_insert_clip`） |
| `ops/qe.jsx` | QE 辅助：`DSH.qe.ensure()` 等；**任何 QE 调用都必须先通过 `DSH.qeEnabled()` 门禁**，且整体包在 try/catch 中 |

### 5.1 处理器签名与约束

```jsx
// args : 命令参数对象（契约 params 的字段，已由 L1 校验过类型）
// ctx  : { command, traceId, dryRun, qeEnabled, idempotencyKey, startedAt }
// 返回 : 业务负载对象（直接作为响应的 data）
function timeline_insert_clip(args, ctx) {
    var sequence = DSH.findSequence(args.sequence);
    var item = DSH.findProjectItem(args.item);
    if (!item) { throw DSH.fail("ITEM_NOT_FOUND", "找不到工程条目: " + args.item,
        "先用 ppro_project_item_find 确认真实路径或 nodeId"); }
    if (ctx.dryRun) {
        return { dry_run: true, actions: ["在 " + args.track_type + " 轨道 " + args.track_index
            + " 的 " + args.at_s + "s 处插入 " + item.name],
            checks: [{ name: "item_exists", ok: true, detail: DSH.treePath(item) }] };
    }
    // ... 真实执行
}
```

硬性约束：

1. **ES3 语法**：只能用 `var`；禁止箭头函数、模板字符串、默认参数、解构、`class`、
   `Promise`、`async/await`、`for...of`、展开运算符。构建期由
   `scripts/check_es3_syntax.py`（esprima AST 遍历）强制。
2. **dry-run**：`ctx.dryRun === true` 时只做前置条件校验，**不得触碰宿主 DOM**，
   返回结构与真实执行一致（说明书 §8.4）。
3. **参数**：不得出现"未在契约中声明"的参数名；必需参数缺失 -> `throw DSH.fail("INVALID_ARGUMENT", ...)`。
4. **时间**：契约里以 `_s` 结尾的都是秒；调用宿主 API 前用 `DSH.ticks()` 转换。
5. **QE**：`capability` 为 `qe` / `public_or_qe` 的命令，必须先试公开 DOM，失败且在
   `DSH.qeEnabled()` 为真时才走 QE；QE 失败不得影响已完成的公开 DOM 操作（说明书 §6.2）。
6. **错误**：一律 `throw DSH.fail(code, message, remedy, extra)`，由 `wrap.jsx` 统一包装；
   禁止直接 `throw new Error("...")`（会丢失 code/remedy）。
7. **禁止**：`app.enableQE()` 之外的任何宿主内部注入、二进制加载、授权相关操作
   （说明书 §1.3、§6.2）。

### 5.2 响应对象结构（由 `wrap.jsx` 保证）

```json
{
  "id": null,
  "ok": true,
  "data": { },
  "error": null,
  "host": { "app_version": "25.0.0", "project_path": "D:\\p.prproj", "dirty": false, "has_project": true },
  "warnings": [],
  "timing": { "host_ms": 12 }
}
```

失败时：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "ITEM_NOT_FOUND",
    "message": "找不到工程条目: X",
    "remedy": "先用 ppro_project_item_find 确认真实路径或 nodeId",
    "line": 42,
    "file": "D:\\...\\ops\\timeline.jsx",
    "command": "timeline.insert_clip",
    "script_excerpt": "40: ...\n41: ...\n42: ...",
    "args_snapshot": { }
  }
}
```

`id` 由 `dispatch.jsx` 在最后回填；`timing.host_ms` 是宿主内耗时。

### 5.3 命令处理器清单

处理器名与归属文件由 `jsx/commands.json` 决定（见 `docs/generated/commands.md` 的完整清单），
**不要手工维护第二份列表**。命名约定：命令 `a.b_c` → 处理器 `a_b_c`。

### 5.4 错误码（ExtendScript 侧）

| code | 含义 |
|---|---|
| `INVALID_ARGUMENT` | 参数缺失/类型不符/越界 |
| `NOT_IMPLEMENTED` | 该宿主版本没有可用实现路径 |
| `UNSUPPORTED_API` | 依赖的宿主 API 在此版本不存在或不可用（**不猜测**，直接报错并给 remedy） |
| `QE_DISABLED` | 需要 QE 但未启用 |
| `NO_PROJECT` | 当前没有打开的工程 |
| `NOT_FOUND` | 序列/轨道/片段/条目不存在 |
| `ITEM_NOT_FOUND` | 工程条目不存在 |
| `REF_INVALID` | clip ref 格式错误 |
| `DUPLICATE_NAME` | 名称冲突（如新建素材箱） |
| `DIALOG_RISK` | 该操作可能触发宿主模态对话框，已被拒绝 |
| `HOST_ERROR` | 宿主 API 抛出的异常（原始信息放在 `detail`） |

---

## 6. 开发期要求（说明书 §1.3 例外条款）

未签名扩展需要设置 `HKCU\Software\Adobe\CSXS.<n>\PlayerDebugMode = "1"`，
由 `scripts/dev_install_cep.ps1` 完成。这只影响扩展签名校验，**与宿主授权无关**，
且本项目不读取、不判断、不干预任何授权状态（说明书 §1.3）。

## 7. 最小 WebSocket 服务实现（`js/vendor/ws`）

CEP 内不可假定能访问外网，也不可假定宿主自带 `ws` 包，因此扩展自带一份最小实现。
要求：

* 只实现服务端所需的最小子集：HTTP Upgrade 握手（`Sec-WebSocket-Accept`）、文本帧读写、
  ping/pong、close 帧、分片帧拒绝（回 1003）、掩码校验（客户端帧必须带掩码）。
* API 形状（供 `ws-server.js` 使用）：

  ```js
  const { WebSocketServer } = require("./vendor/ws");
  const server = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  server.port;                       // OS 分配的端口
  server.on("connection", (conn) => {
    conn.send("text");
    conn.on("message", (text) => {});
    conn.on("close", (code, reason) => {});
    conn.close();
  });
  server.close();
  ```

* 仅使用 Node 内置模块（`http`/`net`/`crypto`/`buffer`），不得依赖 `Buffer` 之外的第三方包。
* 必须随附自测脚本并在本机跑通（见 §8）。

## 8. 验证命令

```powershell
# 1) 契约 → 路由表生成物是否同步（构建期门禁）
python scripts/gen_jsx_dispatch.py --check

# 2) ES3 方言静态检查（AST 遍历，说明书 §6.1/§14.5）
python scripts/check_es3_syntax.py

# 3) Node 侧语法检查（无 node.exe 时用 DSH 自带的 Electron 运行时）
$env:ELECTRON_RUN_AS_NODE="1"
& "F:\DeepseekHarness\DeepSeek Harness.exe" --check cep-extension\js\main.js

# 4) mini-ws 自测（服务端 + Node 内置 WebSocket 客户端）
& "F:\DeepseekHarness\DeepSeek Harness.exe" cep-extension\js\vendor\ws\selftest.js
```

以上 1–4 全部必须通过；其中 1、2 已纳入 `pytest` 门禁。
