"use strict";
/*
 * =====================================================================
 * cep-extension/js/dispatch.js
 * ---------------------------------------------------------------------
 * 命令分发：把 WS 请求变成 `CSInterface.evalScript("DSH.dispatch(<json>)")`，
 * 并把返回的 JSON 字符串回填成响应信封。契约 §4：
 *   dispatchCommand(cmd, args, meta) → Promise<{ok, data, error}>
 *
 * 硬性要求（契约 §4.1，说明书约束 C-2）：
 *   1. **串行化**：同一时刻只允许一个 evalScript 在途。ExtendScript 求值是同步
 *      且阻塞宿主 UI 线程的，并发求值会导致宿主无响应或状态错乱。
 *   2. **序列化方式**：把整个请求 JSON.stringify 一次，再对**它**做
 *      JSON.stringify 生成 JS 字符串字面量，并把 U+2028/U+2029 转义成
 *      \u2028 / \u2029（ES3 字符串字面量里这两个字符是换行，会直接语法错误）。
 *   3. **"EvalScript error."** 必须转成结构化错误
 *      {code:"EVAL_SCRIPT_ERROR", message, remedy}，不得当成正常数据。
 *   4. **长度上限**：超过 eval_max_chars 返回 RESULT_TOO_LARGE，**不得静默截断**。
 *   5. **node 执行器命令**（commands.json 里 executor:"node"）在 Node 侧直接实现，
 *      不下发 ExtendScript，也**不得**调用 evalScript。
 *
 * 语法约束：Node 12 可用语法（详见 js/vendor/ws/index.js 头部说明）。
 * =====================================================================
 */

/*
 * 命令契约里 executor === "node" 的命令清单。
 *
 * 这里**内嵌快照**而不是运行期读 ../../jsx/commands.json，理由：CEP 部署形态下
 * 只保证扩展目录内部相对路径稳定，用 fs 读上级目录会在打包方式变化时静默失败。
 * 代价是两份清单可能漂移，因此：
 *   - 快照数量由 EXPECTED_NODE_COMMAND_COUNT 在启动时断言（不一致就写 error 日志）；
 *   - tests/l3/panel/run.js 直接从 jsx/commands.json 读取真源并与之逐字比对。
 * 真源仍是 cep-extension/jsx/commands.json（本文件不修改它）。
 */
var NODE_COMMANDS = {
  "host.ping": { handler: "handlePing", risk: "T0", idempotent: true },
  "host.capabilities": { handler: "handleCapabilities", risk: "T0", idempotent: true },
  "panel.reload": { handler: "handleReload", risk: "T0", idempotent: false }
};

var EXPECTED_NODE_COMMAND_COUNT = 3;

var CONTRACT_SCHEMA_VERSION = 1;
var PANEL_PROTOCOL_VERSION = 1;

var EVAL_SCRIPT_ERROR_TOKEN = "EvalScript error.";

/* 错误码与 jsx/dispatch.jsx / 契约 §5.4 对齐。 */
var ERROR_EVAL_SCRIPT = "EVAL_SCRIPT_ERROR";
var ERROR_RESULT_TOO_LARGE = "RESULT_TOO_LARGE";
var ERROR_NO_CSINTERFACE = "CSINTERFACE_UNAVAILABLE";
var ERROR_BAD_RESPONSE = "PANEL_BAD_RESPONSE";
var ERROR_INVALID_ARGUMENT = "INVALID_ARGUMENT";
var ERROR_UNKNOWN_COMMAND = "UNKNOWN_COMMAND";

function hasOwn(obj, key) {
  return Object.prototype.hasOwnProperty.call(obj, key);
}

function str(value) {
  return value === null || value === undefined ? "" : String(value);
}

function isObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

/* ------------------------------------------------------------------ */
/* evalScript 脚本文本构造                                             */
/* ------------------------------------------------------------------ */

/**
 * 把一次命令请求转成 evalScript 脚本文本（契约 §4.1 的写法）。
 *
 * 生成的 literal 是**合法 JS 字符串字面量**：JSON.stringify 会处理
 * U+0000–U+001F 控制字符（\u0000–\u001f）、双引号（\"）与反斜杠（\\）；
 * 只有 U+2028/U+2029 是 JSON 允许、ES3 字符串字面量却不接受的字符，
 * 所以必须单独转义。ES2019 起 JS 允许字面量里出现它们，因此这个转义
 * 对现代引擎无害，对 ExtendScript（ES3）是必需的。
 *
 * @param {object} request { id, cmd, args, meta }
 * @returns {{script: string, payload: string, literal: string}}
 */
function buildEvalScript(request) {
  var payload = JSON.stringify(request);
  var literal = JSON.stringify(payload);
  literal = literal.replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029");
  return { script: "DSH.dispatch(" + literal + ")", payload: payload, literal: literal };
}

/* ------------------------------------------------------------------ */
/* 响应解析                                                            */
/* ------------------------------------------------------------------ */

function structuredError(code, message, remedy, extra) {
  var err = { code: code, message: str(message), remedy: remedy === undefined ? null : remedy };
  if (isObject(extra)) {
    for (var key in extra) {
      if (hasOwn(extra, key) && !hasOwn(err, key)) {
        err[key] = extra[key];
      }
    }
  }
  return err;
}

function badResponse(detail) {
  return structuredError(
    ERROR_BAD_RESPONSE,
    "面板收到无法解析的 ExtendScript 返回值：" + detail,
    "查看面板日志中该 traceId 的原始返回；若为宿主版本差异，请同步 cep-extension 后重启面板"
  );
}

/**
 * 解析 evalScript 回调给出的字符串。
 * @returns {{ok: boolean, data: any, error: object|null, response: object|null}}
 */
function parseEvalScriptResult(rawResult, maxChars) {
  if (rawResult === null || rawResult === undefined) {
    return {
      ok: false,
      data: null,
      error: structuredError(
        ERROR_EVAL_SCRIPT,
        "evalScript 返回 " + (rawResult === null ? "null" : "undefined") +
          "（宿主未返回结果，通常是 ExtendScript 抛出未捕获异常）",
        "检查 jsx/ 是否部署完整；ExtendScript 未捕获异常会直接吞掉 EVAL 结果"
      ),
      response: null
    };
  }

  var text = str(rawResult);

  if (text === EVAL_SCRIPT_ERROR_TOKEN) {
    return {
      ok: false,
      data: null,
      error: structuredError(
        ERROR_EVAL_SCRIPT,
        'ExtendScript 求值失败（宿主返回 "' + EVAL_SCRIPT_ERROR_TOKEN + '"）',
        "常见原因：jsx 未部署完整、ES3 语法错误、或 DSH.dispatch 未定义。请比对 CEP 扩展目录下的 jsx/ 与仓库"
      ),
      response: null
    };
  }

  if (text.length === 0) {
    return { ok: false, data: null, error: badResponse("空字符串"), response: null };
  }

  // 上限判断放在 JSON.parse 之前：超大返回值不仅难处理，解析本身也会长时间
  // 阻塞面板的 JS 线程（同一线程还要维持 WS 心跳）。
  if (typeof maxChars === "number" && maxChars > 0 && text.length > maxChars) {
    return {
      ok: false,
      data: null,
      error: structuredError(
        ERROR_RESULT_TOO_LARGE,
        "ExtendScript 返回值长度 " + text.length + " 字符，超过上限 " + maxChars,
        "结果未被截断，而是整条拒绝：请缩小查询范围或分页/分块读取。面板不做静默截断",
        { result_chars: text.length, eval_max_chars: maxChars, truncated: false }
      ),
      response: null
    };
  }

  var parsed;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    return {
      ok: false,
      data: null,
      error: badResponse("JSON 解析失败: " + (err && err.message ? err.message : String(err))),
      response: null
    };
  }

  if (!isObject(parsed)) {
    return { ok: false, data: null, error: badResponse("顶层不是对象"), response: null };
  }

  var ok = parsed.ok === true;
  var error = null;
  if (!ok) {
    error = isObject(parsed.error)
      ? parsed.error
      : structuredError(
          ERROR_BAD_RESPONSE,
          "ExtendScript 返回 ok=false 但没有给出 error 对象",
          "这是 jsx/wrap.jsx 的实现缺陷，请上报；宿主原始返回已记录在面板日志"
        );
  }
  return { ok: ok, data: ok ? parsed.data : null, error: error, response: parsed };
}

/* ------------------------------------------------------------------ */
/* Node 侧命令实现                                                     */
/* ------------------------------------------------------------------ */

/**
 * 构造 node 执行器命令的处理器。每个处理器返回业务负载（作为响应 data）。
 *
 * @param {object} deps { panel, config, reload }
 */
function createNodeHandlers(deps) {
  deps = deps || {};
  var panel = deps.panel || {};
  var config = deps.config || null;
  var startedAt = Date.now();

  function uptimeMs() {
    return Date.now() - startedAt;
  }

  function configValue(key, fallback) {
    if (config && typeof config.getConfigValue === "function") {
      return config.getConfigValue(key, fallback);
    }
    return fallback;
  }

  function handlePing() {
    return {
      pong: true,
      panel_version: panel.panelVersion || "1.1.0",
      uptime_ms: uptimeMs()
    };
  }

  function handleCapabilities() {
    var rt = panel.runtime || {};
    return {
      node_version: process.versions && process.versions.node ? process.versions.node : null,
      cep_version: rt.cepVersion === undefined ? null : rt.cepVersion,
      mixed_context: rt.mixedContext === true,
      qe_enabled: configValue("qe_enabled", false) === true,
      eval_max_chars: configValue("eval_max_chars", 100000),
      host_app_id: rt.hostAppId === undefined ? null : rt.hostAppId,
      /* 以下为面板自有补充字段，L1 可忽略，仅用于排障。 */
      panel_version: panel.panelVersion || "1.1.0",
      contract_schema_version: CONTRACT_SCHEMA_VERSION,
      protocol: PANEL_PROTOCOL_VERSION,
      pid: process.pid,
      uptime_ms: uptimeMs(),
      csinterface_available: rt.csinterfaceAvailable === true,
      cep_environment: rt.cepEnvironment === true,
      node_executor_commands: Object.keys(NODE_COMMANDS)
    };
  }

  function handleReload() {
    // 必须先把响应发出去再重载：reload 会重置整个面板 JS 环境，连接会短暂中断。
    var reload = deps.reload;
    setTimeout(function () {
      try {
        if (typeof reload === "function") {
          reload();
        } else if (typeof window !== "undefined" && window.location && typeof window.location.reload === "function") {
          window.location.reload();
        }
      } catch (err) {
        /* 重载失败已无从上报（连接即将断开），交给宿主控制台 */
        if (typeof console !== "undefined" && console.error) {
          console.error("[panel] reload 失败: " + (err && err.message ? err.message : err));
        }
      }
    }, 50);
    return { reloading: true };
  }

  return {
    handlePing: handlePing,
    handleCapabilities: handleCapabilities,
    handleReload: handleReload
  };
}

/* ------------------------------------------------------------------ */
/* Dispatcher                                                          */
/* ------------------------------------------------------------------ */

function makeEnvelope(id, ok, data, error, host, warnings, hostMs, panelMs) {
  return {
    id: id === undefined ? null : id,
    ok: ok === true,
    data: ok === true ? data : null,
    error: ok === true ? null : error,
    host: host || null,
    warnings: Array.isArray(warnings) ? warnings : [],
    timing: {
      host_ms: hostMs === undefined ? null : hostMs,
      panel_ms: panelMs === undefined ? null : panelMs
    }
  };
}

/**
 * 创建分发器。串行队列在这里实现：所有 evalScript 调用排在一个 FIFO 上，
 * 队首完成后才发起下一个（一个在途）。
 *
 * @param {object} deps
 *   csInterface  {evalScript(script, cb)} —— CEP 的 CSInterface 实例或替身
 *   logger       面板 logger（可选）
 *   config       js/config.js 模块（可选）
 *   panel        {panelVersion, runtime:{cepVersion, mixedContext, hostAppId, ...}}
 *   onResponse   function(cmd, envelope, request) —— 响应组装完成后回调
 *   reload       function() —— panel.reload 用
 *   clock        function() => ms（测试可注入，便于断言面板侧耗时）
 */
function createDispatcher(deps) {
  deps = deps || {};
  var csInterface = deps.csInterface || null;
  var logger = deps.logger || null;
  var config = deps.config || null;
  var panel = deps.panel || {};
  var onResponse = typeof deps.onResponse === "function" ? deps.onResponse : null;
  var clock = typeof deps.clock === "function" ? deps.clock : function () { return Date.now(); };

  var nodeHandlers = createNodeHandlers({ panel: panel, config: config, reload: deps.reload });

  var queue = [];
  var inflight = null;
  var busy = false;

  var stats = {
    total: 0,
    succeeded: 0,
    failed: 0,
    rejected: 0,
    max_queue_depth: 0,
    eval_script_calls: 0,
    node_command_calls: 0
  };

  if (Object.keys(NODE_COMMANDS).length !== EXPECTED_NODE_COMMAND_COUNT) {
    if (logger) {
      logger.error("node 执行器命令快照与预期数量不一致，可能与 commands.json 漂移", {
        expected: EXPECTED_NODE_COMMAND_COUNT,
        actual: Object.keys(NODE_COMMANDS)
      });
    }
  }

  function logErr(message, extra) {
    if (logger) {
      logger.error(message, extra);
    }
  }

  function evalMaxChars() {
    if (config && typeof config.getConfigValue === "function") {
      var v = config.getConfigValue("eval_max_chars", 100000);
      if (typeof v === "number" && v > 0) {
        return v;
      }
    }
    return 100000;
  }

  /** 通知 + 兑现 Promise + 放行下一个。所有完成路径都必须经由这里。 */
  function finish(item, envelope) {
    busy = false;
    inflight = null;
    if (envelope.ok) {
      stats.succeeded++;
    } else {
      stats.failed++;
    }
    if (logger) {
      logger.debug("命令完成", {
        cmd: item.request.cmd,
        id: item.request.id,
        traceId: item.request.meta ? item.request.meta.traceId : null,
        ok: envelope.ok,
        error: envelope.error ? envelope.error.code : null,
        panel_ms: envelope.timing ? envelope.timing.panel_ms : null
      });
    }
    if (onResponse) {
      try {
        onResponse(item.request.cmd, envelope, item.request);
      } catch (err) {
        logErr("onResponse 回调抛错", { message: err && err.message ? err.message : String(err) });
      }
    }
    try {
      item.resolve(envelope);
    } catch (err) {
      logErr("resolve 响应失败", { message: err && err.message ? err.message : String(err) });
    }
    pump();
  }

  /** 队列泵：一次只放行一个（串行化的唯一入口）。 */
  function pump() {
    if (busy || queue.length === 0) {
      return;
    }
    var item = queue.shift();
    inflight = item;
    busy = true;
    if (item.isNode) {
      runNodeCommand(item);
    } else {
      runEvalScript(item);
    }
  }

  function runNodeCommand(item) {
    var request = item.request;
    var spec = NODE_COMMANDS[request.cmd];
    var started = clock();
    var handler = nodeHandlers[spec.handler];
    if (typeof handler !== "function") {
      finish(item, makeEnvelope(request.id, false, null,
        structuredError(ERROR_UNKNOWN_COMMAND,
          "node 命令 " + request.cmd + " 的处理器 " + spec.handler + " 未实现",
          "这是面板实现缺陷，请上报"), null, [], null, null));
      return;
    }
    stats.node_command_calls++;
    try {
      var data = handler(request.args, request.meta);
      finish(item, makeEnvelope(request.id, true, data, null, null, [], 0, clock() - started));
    } catch (err) {
      finish(item, makeEnvelope(request.id, false, null,
        structuredError(ERROR_BAD_RESPONSE,
          "node 命令 " + request.cmd + " 执行失败: " + (err && err.message ? err.message : String(err)),
          "这是面板实现缺陷，请上报"), null, [], null, clock() - started));
    }
  }

  function runEvalScript(item) {
    var request = item.request;
    var built;
    try {
      built = buildEvalScript(request);
    } catch (err) {
      finish(item, makeEnvelope(request.id, false, null,
        structuredError(ERROR_INVALID_ARGUMENT,
          "请求无法序列化为 JSON: " + (err && err.message ? err.message : String(err)),
          "检查 args 是否含循环引用或不可序列化的值"), null, [], null, null));
      return;
    }

    if (!csInterface || typeof csInterface.evalScript !== "function") {
      finish(item, makeEnvelope(request.id, false, null,
        structuredError(ERROR_NO_CSINTERFACE,
          "面板不在 CEP 环境中（CSInterface.evalScript 不可用），无法下发 ExtendScript",
          "在宿主内打开面板：窗口 > 扩展 > DSH Adobe Bridge。普通浏览器只能查看面板状态"),
        null, [], null, null));
      return;
    }

    stats.eval_script_calls++;
    var evalStart = clock();
    var callbackFired = false;

    function onEvalDone(rawResult) {
      // 同一请求只接受一次回调：某些宿主版本异常时会重复回调，
      // 重复进入会把串行队列的 busy 标志打乱。
      if (callbackFired) {
        logErr("evalScript 对同一请求重复回调，已忽略", { id: request.id, cmd: request.cmd });
        return;
      }
      callbackFired = true;
      var panelMs = clock() - evalStart;
      var parsed = parseEvalScriptResult(rawResult, evalMaxChars());
      var env = makeEnvelope(
        request.id,
        parsed.ok,
        parsed.data,
        parsed.error,
        parsed.response ? parsed.response.host : null,
        parsed.response && Array.isArray(parsed.response.warnings) ? parsed.response.warnings : [],
        parsed.response && parsed.response.timing ? parsed.response.timing.host_ms : null,
        panelMs
      );
      if (parsed.response) {
        if (parsed.response.capability) {
          env.capability = parsed.response.capability;
        }
        if (hasOwn(parsed.response, "replayed")) {
          env.replayed = parsed.response.replayed;
        }
        if (hasOwn(parsed.response, "qe_enabled")) {
          env.qe_enabled = parsed.response.qe_enabled;
        }
      }
      finish(item, env);
    }

    try {
      csInterface.evalScript(built.script, onEvalDone);
    } catch (err) {
      finish(item, makeEnvelope(request.id, false, null,
        structuredError(ERROR_EVAL_SCRIPT,
          "调用 CSInterface.evalScript 抛出异常: " + (err && err.message ? err.message : String(err)),
          "面板与宿主的 CEP 桥可能已失效：请重启面板；若持续出现，检查 manifest 的 RequiredRuntime 是否与宿主版本匹配"),
        null, [], null, null));
    }
  }

  function enqueue(cmd, args, meta) {
    meta = isObject(meta) ? meta : {};
    var request = {
      id: meta.id === undefined ? null : meta.id,
      cmd: str(cmd),
      args: isObject(args) ? args : {},
      meta: {
        traceId: meta.traceId === undefined ? null : meta.traceId,
        dryRun: meta.dryRun === true,
        idempotencyKey:
          meta.idempotencyKey === undefined || meta.idempotencyKey === null ? null : String(meta.idempotencyKey)
      }
    };
    stats.total++;

    return new Promise(function (resolve) {
      if (!request.cmd) {
        // 无命令名的请求不进队列，直接拒绝，避免污染串行序列。
        stats.rejected++;
        var env = makeEnvelope(request.id, false, null,
          structuredError(ERROR_INVALID_ARGUMENT, "缺少 cmd 字段",
            '请求必须形如 {"type":"req","id":"...","cmd":"app.info","args":{}}'),
          null, [], null, 0);
        if (onResponse) {
          try {
            onResponse(request.cmd, env, request);
          } catch (err) {
            logErr("onResponse 回调抛错", { message: err && err.message ? err.message : String(err) });
          }
        }
        resolve(env);
        return;
      }
      queue.push({
        request: request,
        resolve: resolve,
        // node 执行器命令走 Node 侧实现，绝不进 evalScript。
        isNode: hasOwn(NODE_COMMANDS, request.cmd)
      });
      if (queue.length > stats.max_queue_depth) {
        stats.max_queue_depth = queue.length;
      }
      pump();
    });
  }

  return {
    /** 契约 §4 的入口名。meta.id 作为响应 id 回填。 */
    dispatchCommand: enqueue,
    /** 在途（正在执行）的请求数：0 或 1，心跳 inflight 字段用它。 */
    inflightCount: function () {
      return busy ? 1 : 0;
    },
    /** 排队等待的请求数。 */
    queueDepth: function () {
      return queue.length;
    },
    /** 在途 + 排队。 */
    pendingCount: function () {
      return queue.length + (busy ? 1 : 0);
    },
    stats: function () {
      var copy = {};
      for (var key in stats) {
        if (hasOwn(stats, key)) {
          copy[key] = stats[key];
        }
      }
      copy.inflight = busy ? 1 : 0;
      copy.queue_depth = queue.length;
      return copy;
    },
    buildEvalScript: buildEvalScript,
    nodeCommands: function () {
      return Object.keys(NODE_COMMANDS);
    },
    _nodeHandlers: nodeHandlers
  };
}

/* ------------------------------------------------------------------ */
/* 默认（面板运行期）分发器                                            */
/* ------------------------------------------------------------------ */

var runtimeDeps = {
  csInterface: null,
  logger: null,
  config: null,
  panel: null,
  reload: null,
  clock: null,
  onResponse: null
};

var sharedDispatcher = null;

/**
 * 由 main.js 在启动时注入运行期依赖；tests/l3/panel/harness.js 用同一入口
 * 换入伪 CSInterface。任何一项变化都会重建分发器（避免旧队列残留）。
 */
function setRuntime(deps) {
  deps = deps || {};
  var keys = ["csInterface", "logger", "config", "panel", "reload", "clock", "onResponse"];
  for (var i = 0; i < keys.length; i++) {
    if (hasOwn(deps, keys[i])) {
      runtimeDeps[keys[i]] = deps[keys[i]];
    }
  }
  sharedDispatcher = null;
}

function getDispatcher() {
  if (!sharedDispatcher) {
    sharedDispatcher = createDispatcher({
      csInterface: runtimeDeps.csInterface,
      logger: runtimeDeps.logger,
      config: runtimeDeps.config,
      panel: runtimeDeps.panel || {},
      reload: runtimeDeps.reload,
      clock: runtimeDeps.clock,
      onResponse: runtimeDeps.onResponse
    });
  }
  return sharedDispatcher;
}

/**
 * 契约 §4 要求的模块级入口：dispatchCommand(cmd, args, meta)
 * @returns {Promise<{ok: boolean, data: any, error: object|null}>}
 */
function dispatchCommand(cmd, args, meta) {
  return getDispatcher().dispatchCommand(cmd, args, meta);
}

module.exports = {
  CONTRACT_SCHEMA_VERSION: CONTRACT_SCHEMA_VERSION,
  PANEL_PROTOCOL_VERSION: PANEL_PROTOCOL_VERSION,
  NODE_COMMANDS: NODE_COMMANDS,
  EXPECTED_NODE_COMMAND_COUNT: EXPECTED_NODE_COMMAND_COUNT,
  EVAL_SCRIPT_ERROR_TOKEN: EVAL_SCRIPT_ERROR_TOKEN,
  buildEvalScript: buildEvalScript,
  parseEvalScriptResult: parseEvalScriptResult,
  createNodeHandlers: createNodeHandlers,
  createDispatcher: createDispatcher,
  setRuntime: setRuntime,
  getDispatcher: getDispatcher,
  dispatchCommand: dispatchCommand
};
