"use strict";
/*
 * =====================================================================
 * cep-extension/js/ws-server.js
 * ---------------------------------------------------------------------
 * 桥接服务端。契约 §4：
 *   createBridgeServer({ token, config, onRequest }) → { port, close() }
 * 内部负责认证、帧校验、心跳、请求路由。
 *
 * 协议（契约 §3，全部为 UTF-8 JSON 文本帧；Python 是客户端，面板是服务端）：
 *   Python → 面板  {"type":"auth","protocol":1,"token":"...","client":...,"client_version":...,"config":{...}}
 *   面板 → Python  {"type":"auth_ok","protocol":1,"panel_version":...,"contract_schema_version":1,"capabilities":{...}}
 *   面板 → Python  {"type":"auth_failed","reason":"..."} 然后立即关闭
 *   Python → 面板  {"type":"req","id":"...","cmd":"...","args":{...},"meta":{...}}
 *   面板 → Python  {"type":"res","id":"...","ok":true,"data":{...},"error":null,...}
 *   面板 → Python  {"type":"heartbeat","ts":...,"uptime_ms":...,"inflight":0}
 *   Python → 面板  {"type":"config","config":{...}}
 *   任一方 →       {"type":"error","reason":"unknown_frame_type"}（未知 type 保持连接）
 *
 * 安全要求（说明书 §5.1、§11、约束 C-5）：
 *   - 只绑定 127.0.0.1:0（动态端口），绝不监听非回环地址；
 *   - 连接后**第一帧必须是认证帧**，3 秒内未收到即关闭；
 *   - 令牌用常量时间比较（js/auth.js），失败立即关闭且不泄漏信息；
 *   - 令牌明文**绝不进入日志**（logger.registerSecret 做全局兜底）。
 * =====================================================================
 */

var path = require("path");

var miniWs = require(path.join(__dirname, "vendor", "ws", "index.js"));
var WebSocketServer = miniWs.WebSocketServer;
var auth = require(path.join(__dirname, "auth.js"));

var PROTOCOL = 1;
var CLOSE_POLICY_VIOLATION = 1008;
var CLOSE_PROTOCOL_ERROR = 1002;
var CLOSE_GOING_AWAY = 1001;

/** 认证失败原因（写给客户端用于排障，不含任何敏感信息）。 */
var REASON = {
  BAD_TOKEN: "bad_token",
  BAD_PROTOCOL: "bad_protocol",
  BAD_AUTH_FRAME: "bad_auth_frame",
  AUTH_TIMEOUT: "auth_timeout",
  NOT_AUTHENTICATED: "not_authenticated"
};

function isObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function nowSeconds() {
  return Date.now() / 1000;
}

/**
 * @param {object} options
 *   token          string   —— 必需的握手令牌（由 main.js 生成，长度 ≥ 32 hex）
 *   config         object   —— js/config.js 模块（可为 null）
 *   onRequest      function(cmd, args, meta) → Promise<响应信封>
 *   logger         object   —— js/logger.js 实例（可为 null）
 *   panel          object   —— {panelVersion, runtime:{...}}，用于 auth_ok 与 capabilities
 *   inflightProvider function() => number —— 心跳 inflight 字段
 *   host           string   —— 默认 127.0.0.1（约束 C-5）
 *   port           number   —— 默认 0（动态）
 *   clock          function() => ms —— 测试注入
 */
function createBridgeServer(options) {
  options = options || {};
  var token = options.token;
  var config = options.config || null;
  var onRequest = options.onRequest;
  var logger = options.logger || null;
  var panel = options.panel || {};
  var clock = typeof options.clock === "function" ? options.clock : function () { return Date.now(); };
  var inflightProvider = typeof options.inflightProvider === "function" ? options.inflightProvider : null;

  if (typeof token !== "string" || token.length < 32) {
    throw new Error("createBridgeServer 需要长度 >= 32 的 token（契约 §2）");
  }
  if (typeof onRequest !== "function") {
    throw new Error("createBridgeServer 需要 onRequest(cmd, args, meta) 回调");
  }

  // 令牌注册进 logger：此后任何日志出口都会把它的明文替换掉（说明书 §11）。
  if (logger && typeof logger.registerSecret === "function") {
    logger.registerSecret(token);
  }

  var cfg = function (key, fallback) {
    if (config && typeof config.getConfigValue === "function") {
      var v = config.getConfigValue(key, fallback);
      return v === undefined ? fallback : v;
    }
    return fallback;
  };

  var startedAtMs = clock();
  var connections = [];
  var heartbeatTimer = null;
  var closed = false;
  var counters = {
    accepted_connections: 0,
    auth_ok: 0,
    auth_failed: 0,
    requests: 0,
    protocol_violations: 0,
    heartbeat_frames: 0
  };

  function log(level, message, extra) {
    if (!logger) {
      return;
    }
    var fn = logger[level];
    if (typeof fn === "function") {
      fn.call(logger, message, extra);
    }
  }

  var server = new WebSocketServer({
    host: options.host || "127.0.0.1",
    port: typeof options.port === "number" ? options.port : 0
  });

  server.on("connection", function (conn) {
    counters.accepted_connections++;
    var session = {
      conn: conn,
      authenticated: false,
      authTimer: null,
      startedAtMs: clock(),
      remote: conn.remoteAddress ? conn.remoteAddress() : "",
      client: null,
      clientVersion: null,
      requestCount: 0,
      closed: false,
      closeReason: null
    };
    connections.push(session);

    // 契约 §3：3 秒内未收到认证帧 → 关闭，不泄漏任何信息。
    session.authTimer = setTimeout(function () {
      if (session.authenticated || session.closed) {
        return;
      }
      counters.protocol_violations++;
      log("warn", "认证超时，关闭连接", { remote: session.remote, timeout_ms: cfg("auth_timeout_ms", 3000) });
      closeSession(session, CLOSE_PROTOCOL_ERROR, REASON.AUTH_TIMEOUT);
    }, cfg("auth_timeout_ms", 3000));
    if (session.authTimer && typeof session.authTimer.unref === "function") {
      session.authTimer.unref();
    }

    conn.on("message", function (text) {
      handleFrame(session, text);
    });

    conn.on("close", function (code, reason) {
      if (session.authTimer) {
        clearTimeout(session.authTimer);
        session.authTimer = null;
      }
      session.closed = true;
      session.closeReason = reason || null;
      for (var i = connections.length - 1; i >= 0; i--) {
        if (connections[i] === session) {
          connections.splice(i, 1);
        }
      }
      log("debug", "连接关闭", {
        remote: session.remote,
        authenticated: session.authenticated,
        close_code: code,
        reason: session.closeReason,
        requests: session.requestCount
      });
    });
  });

  /** 把最后一帧发出去再关闭，避免客户端只看到"连接被重置"。 */
  function closeSession(session, code, reason) {
    if (session.closed) {
      return;
    }
    var grace = cfg("close_grace_ms", 60);
    var timer = setTimeout(function () {
      try {
        session.conn.close(code, reason);
      } catch (err) {
        /* 已关闭 */
      }
    }, grace);
    if (timer && typeof timer.unref === "function") {
      timer.unref();
    }
    // 标记为不可再处理新帧，防止关闭窗口期内又收到请求。
    session.closing = true;
  }

  function sendJson(session, payload) {
    // session.closing 只表示"即将关闭"，此时仍必须允许发送最后一帧
    // （auth_failed / error）。只有底层连接已关闭才拒绝。
    if (session.closed) {
      return false;
    }
    try {
      return session.conn.send(JSON.stringify(payload));
    } catch (err) {
      log("error", "发送帧失败", { message: err && err.message ? err.message : String(err) });
      return false;
    }
  }

  function authFailed(session, reason, detail) {
    counters.auth_failed++;
    session.closing = true;
    log("warn", "认证失败，关闭连接", {
      reason: reason,
      remote: session.remote,
      detail: detail === undefined ? null : detail
    });
    sendJson(session, { type: "auth_failed", reason: reason });
    closeSession(session, CLOSE_POLICY_VIOLATION, reason);
  }

  function handleFrame(session, text) {
    if (session.closed) {
      return;
    }
    var frame;
    try {
      frame = JSON.parse(text);
    } catch (err) {
      counters.protocol_violations++;
      log("warn", "收到非 JSON 帧", { remote: session.remote, chars: text ? text.length : 0 });
      sendJson(session, { type: "error", reason: "invalid_json" });
      if (!session.authenticated) {
        // 认证前发非 JSON 帧同样是协议违规（契约 §3）。
        closeSession(session, CLOSE_PROTOCOL_ERROR, REASON.BAD_AUTH_FRAME);
      }
      return;
    }

    if (!isObject(frame) || typeof frame.type !== "string") {
      counters.protocol_violations++;
      sendJson(session, { type: "error", reason: "missing_type" });
      if (!session.authenticated) {
        closeSession(session, CLOSE_PROTOCOL_ERROR, REASON.BAD_AUTH_FRAME);
      }
      return;
    }

    // ---------------- 认证阶段 ----------------
    if (!session.authenticated) {
      if (frame.type !== "auth") {
        // 契约 §3：除认证帧外，认证完成前发送的任何帧都视为协议违规。
        counters.protocol_violations++;
        log("warn", "认证前收到非认证帧", { remote: session.remote, type: frame.type });
        sendJson(session, { type: "error", reason: "not_authenticated" });
        closeSession(session, CLOSE_POLICY_VIOLATION, REASON.NOT_AUTHENTICATED);
        return;
      }
      handleAuth(session, frame);
      return;
    }

    // ---------------- 已认证 ----------------
    if (frame.type === "req") {
      handleRequest(session, frame);
      return;
    }
    if (frame.type === "config") {
      applyConfig(session, frame);
      return;
    }
    if (frame.type === "heartbeat" || frame.type === "pong") {
      // Python 侧不会主动心跳，但容忍且不回错。
      return;
    }
    // 契约 §3：未知 type → 回 error 并**保持连接**。
    log("debug", "收到未知帧类型", { type: frame.type });
    sendJson(session, { type: "error", reason: "unknown_frame_type" });
  }

  function handleAuth(session, frame) {
    if (frame.protocol !== undefined && frame.protocol !== PROTOCOL) {
      authFailed(session, REASON.BAD_PROTOCOL, { got: frame.protocol, want: PROTOCOL });
      return;
    }
    if (typeof frame.token !== "string") {
      authFailed(session, REASON.BAD_AUTH_FRAME);
      return;
    }
    if (!auth.verifyToken(token, frame.token)) {
      // 令牌指纹只用于"两端是不是同一个令牌"的排障，不含令牌本身。
      authFailed(session, REASON.BAD_TOKEN, {
        expected_fingerprint: auth.tokenFingerprint(token),
        got_fingerprint: auth.tokenFingerprint(frame.token)
      });
      return;
    }

    if (session.authTimer) {
      clearTimeout(session.authTimer);
      session.authTimer = null;
    }
    session.authenticated = true;
    session.client = typeof frame.client === "string" ? frame.client : null;
    session.clientVersion = typeof frame.client_version === "string" ? frame.client_version : null;
    counters.auth_ok++;

    // 认证帧里可以带 config（契约 §3 的认证帧结构含 config 字段）。
    if (isObject(frame.config)) {
      applyConfig(session, { config: frame.config });
    }

    var rt = panel.runtime || {};
    sendJson(session, {
      type: "auth_ok",
      protocol: PROTOCOL,
      panel_version: panel.panelVersion || "1.1.0",
      contract_schema_version: panel.contractSchemaVersion || 1,
      capabilities: {
        node_version: process.versions && process.versions.node ? process.versions.node : null,
        cep_version: rt.cepVersion === undefined ? null : rt.cepVersion,
        mixed_context: rt.mixedContext === true,
        qe_enabled: cfg("qe_enabled", false) === true,
        eval_max_chars: cfg("eval_max_chars", 100000),
        host_app_id: rt.hostAppId === undefined ? null : rt.hostAppId,
        node_executor_commands: panel.nodeCommands || null
      }
    });
    log("info", "客户端认证成功", {
      client: session.client,
      client_version: session.clientVersion,
      remote: session.remote
    });
  }

  function applyConfig(session, frame) {
    if (!isObject(frame.config)) {
      sendJson(session, { type: "error", reason: "bad_config" });
      return;
    }
    if (!config || typeof config.setConfig !== "function") {
      sendJson(session, { type: "error", reason: "config_not_supported" });
      return;
    }
    var result = config.setConfig(frame.config);
    // 重新排定心跳周期（heartbeat_s 可能被改）。
    scheduleHeartbeat();
    log("info", "应用 L1 下发的配置", {
      applied: Object.keys(result.applied || {}),
      ignored: result.ignored || []
    });
    sendJson(session, {
      type: "config_ack",
      applied: Object.keys(result.applied || {}),
      ignored: result.ignored || []
    });
  }

  function handleRequest(session, frame) {
    var id = frame.id === undefined ? null : frame.id;
    var cmd = typeof frame.cmd === "string" ? frame.cmd : "";
    var args = isObject(frame.args) ? frame.args : {};
    var meta = isObject(frame.meta) ? frame.meta : {};
    session.requestCount++;
    counters.requests++;

    var requestMeta = {
      id: id,
      traceId: meta.traceId === undefined ? null : meta.traceId,
      dryRun: meta.dryRun === true,
      idempotencyKey: meta.idempotencyKey === undefined ? null : meta.idempotencyKey
    };

    var responded = false;
    function respond(envelope) {
      if (responded) {
        log("warn", "同一请求被响应两次，已忽略第二次", { id: id, cmd: cmd });
        return;
      }
      responded = true;
      if (session.closed) {
        return;
      }
      var out = {
        type: "res",
        id: envelope && envelope.id !== undefined ? envelope.id : id,
        ok: !!(envelope && envelope.ok),
        data: envelope ? envelope.data : null,
        error: envelope ? envelope.error : null,
        host: envelope ? envelope.host : null,
        warnings: envelope && Array.isArray(envelope.warnings) ? envelope.warnings : [],
        timing: envelope && envelope.timing ? envelope.timing : { host_ms: null, panel_ms: null }
      };
      if (envelope && envelope.capability) {
        out.capability = envelope.capability;
      }
      if (envelope && envelope.replayed) {
        out.replayed = true;
      }
      sendJson(session, out);
    }

    var result;
    try {
      result = onRequest(cmd, args, requestMeta);
    } catch (err) {
      respond({
        id: id,
        ok: false,
        data: null,
        error: {
          code: "PANEL_INTERNAL_ERROR",
          message: "面板处理请求时抛出同步异常: " + (err && err.message ? err.message : String(err)),
          remedy: "这是面板实现缺陷，请上报；面板日志含该请求的 traceId"
        },
        host: null,
        warnings: [],
        timing: { host_ms: null, panel_ms: null }
      });
      return;
    }

    if (!result || typeof result.then !== "function") {
      // 允许 onRequest 返回同步结果。
      respond(result);
      return;
    }
    result.then(respond, function (err) {
      respond({
        id: id,
        ok: false,
        data: null,
        error: {
          code: "PANEL_INTERNAL_ERROR",
          message: "面板处理请求失败: " + (err && err.message ? err.message : String(err)),
          remedy: "这是面板实现缺陷，请上报；面板日志含该请求的 traceId"
        },
        host: null,
        warnings: [],
        timing: { host_ms: null, panel_ms: null }
      });
    });
  }

  function heartbeatIntervalMs() {
    var seconds = cfg("heartbeat_s", 5);
    var num = Number(seconds);
    if (!isFinite(num) || num <= 0) {
      num = 5;
    }
    return Math.max(1000, Math.round(num * 1000));
  }

  function sendHeartbeats() {
    if (closed) {
      return;
    }
    var inflight = 0;
    if (inflightProvider) {
      try {
        inflight = inflightProvider() || 0;
      } catch (err) {
        inflight = 0;
      }
    }
    var payload = {
      type: "heartbeat",
      ts: nowSeconds(),
      uptime_ms: clock() - startedAtMs,
      inflight: inflight
    };
    var sent = 0;
    for (var i = 0; i < connections.length; i++) {
      var session = connections[i];
      if (session.authenticated && !session.closed && !session.closing) {
        if (sendJson(session, payload)) {
          sent++;
        }
      }
    }
    if (sent > 0) {
      counters.heartbeat_frames += sent;
      log("debug", "心跳已发送", { connections: sent, inflight: inflight });
    }
  }

  function scheduleHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
    if (closed) {
      return;
    }
    var ms = heartbeatIntervalMs();
    heartbeatTimer = setInterval(sendHeartbeats, ms);
    if (heartbeatTimer && typeof heartbeatTimer.unref === "function") {
      heartbeatTimer.unref();
    }
    return ms;
  }

  // 服务端在 listen 完成前拿不到动态端口，这里暴露一个 promise，调用方可以等。
  var listening = new Promise(function (resolve, reject) {
    server.on("listening", function (port) {
      startedAtMs = clock();
      api.port = port;
      var ms = scheduleHeartbeat();
      log("info", "桥接服务端已监听", {
        host: options.host || "127.0.0.1",
        port: port,
        heartbeat_ms: ms,
        panel_version: panel.panelVersion || "1.1.0"
      });
      resolve(port);
    });
    server.on("error", function (err) {
      log("error", "桥接服务端监听失败", { message: err && err.message ? err.message : String(err) });
      reject(err);
    });
  });

  var api = {
    /**
     * OS 分配的端口。listen 完成前为 0，因此请等 `ready()`（绑定后兑现）再读。
     * 用普通属性而不是 getter：CEP 自带 Node 较老，且测试替身
     * （tests/l3/panel/fake_cep.js）复制属性时会丢失 getter。
     */
    port: 0,
    /** listen 完成（拿到端口、心跳已启动）后兑现，值为端口号。 */
    ready: function () {
      return listening;
    },
    host: options.host || "127.0.0.1",
    connectionCount: function () {
      return connections.length;
    },
    authenticatedCount: function () {
      var n = 0;
      for (var i = 0; i < connections.length; i++) {
        if (connections[i].authenticated) {
          n++;
        }
      }
      return n;
    },
    /** 连接状态快照，面板 UI 与握手文件诊断用（不含任何令牌信息）。 */
    status: function () {
      var clients = [];
      for (var i = 0; i < connections.length; i++) {
        var s = connections[i];
        clients.push({
          remote: s.remote,
          authenticated: s.authenticated,
          client: s.client,
          client_version: s.clientVersion,
          requests: s.requestCount,
          uptime_ms: clock() - s.startedAtMs
        });
      }
      return {
        port: api.port,
        host: options.host || "127.0.0.1",
        uptime_ms: clock() - startedAtMs,
        heartbeat_ms: heartbeatIntervalMs(),
        connections: clients,
        counters: counters
      };
    },
    /** 当前心跳周期（毫秒），测试断言用。 */
    heartbeatMs: heartbeatIntervalMs,
    /** 手动触发一次心跳（测试用，避免等真实定时器）。 */
    _sendHeartbeats: sendHeartbeats,
    /** 直接广播（main.js 向已认证客户端推送通知）。 */
    broadcast: function (payload) {
      var sent = 0;
      for (var i = 0; i < connections.length; i++) {
        var session = connections[i];
        if (session.authenticated && !session.closed && !session.closing) {
          if (sendJson(session, payload)) {
            sent++;
          }
        }
      }
      return sent;
    },
    /** 心跳定时器是否在跑（1/0）。 */
    intervals: function () {
      return heartbeatTimer ? 1 : 0;
    },
    close: function (callback) {
      if (closed) {
        if (typeof callback === "function") {
          callback();
        }
        return;
      }
      closed = true;
      if (heartbeatTimer) {
        clearInterval(heartbeatTimer);
        heartbeatTimer = null;
      }
      for (var i = 0; i < connections.length; i++) {
        var session = connections[i];
        if (session.authTimer) {
          clearTimeout(session.authTimer);
          session.authTimer = null;
        }
        try {
          session.conn.close(CLOSE_GOING_AWAY, "panel_shutdown");
        } catch (err) {
          /* 已关闭 */
        }
      }
      log("info", "桥接服务端关闭", { counters: counters });
      server.close(function () {
        if (typeof callback === "function") {
          callback();
        }
      });
    }
  };

  return api;
}

module.exports = {
  PROTOCOL: PROTOCOL,
  REASON: REASON,
  createBridgeServer: createBridgeServer
};
