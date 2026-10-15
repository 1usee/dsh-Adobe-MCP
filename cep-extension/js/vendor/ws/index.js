"use strict";
/*
 * =====================================================================
 * cep-extension/js/vendor/ws/index.js
 * ---------------------------------------------------------------------
 * 随扩展打包的**最小 RFC6455 WebSocket 服务端**实现。
 *
 * 为什么不用 npm 的 `ws`：
 *   1. CEP 面板运行在宿主的 CEF/Node 环境里，不保证有网络可以 npm install；
 *   2. 也不保证宿主自带 `ws` 包；
 *   3. 说明书 §13.1 要求扩展侧零第三方运行时依赖。
 * 因此这里只用 Node 内置模块（http / net / crypto / buffer）实现服务端所需的
 * 最小子集。
 *
 * 语法约束：宿主 CEP 自带的 Node 版本可能远低于开发机（本项目按 Node 12
 * 可用语法书写）。禁止 `??=`、可选链之外的新 API、`.at()`、`Object.hasOwn`、
 * `String.prototype.replaceAll`、顶层 await 等。
 *
 * 实现范围：
 *   - HTTP Upgrade 握手（Sec-WebSocket-Key / Sec-WebSocket-Version 校验，
 *     返回正确的 Sec-WebSocket-Accept）
 *   - 文本帧收发（含分片重组前的 1003 拒绝）
 *   - ping / pong
 *   - close 帧（回 close 并关闭底层 socket）
 *   - 客户端帧必须带掩码，否则以 1002 协议错误关闭
 *   - 超过上限的帧负载以 1009 关闭
 *
 * 明确不做（服务端不需要）：
 *   - permessage-deflate 扩展
 *   - 二进制帧的业务语义（收到 binary 帧按 1003 拒绝，本项目协议全是文本帧）
 *   - 客户端 API
 *
 * API 形状（契约 §7）：
 *   const { WebSocketServer } = require("./vendor/ws");
 *   const server = new WebSocketServer({ host: "127.0.0.1", port: 0 });
 *   server.port;                            // OS 分配的端口（listening 后可用）
 *   server.on("connection", function (conn) { ... });
 *   server.close();
 *   conn.send(text) / conn.on("message"|"close", fn) / conn.close(code, reason)
 * =====================================================================
 */

var http = require("http");
var crypto = require("crypto");

var GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

var OPCODE_CONTINUATION = 0x0;
var OPCODE_TEXT = 0x1;
var OPCODE_BINARY = 0x2;
var OPCODE_CLOSE = 0x8;
var OPCODE_PING = 0x9;
var OPCODE_PONG = 0xa;

var CLOSE_PROTOCOL_ERROR = 1002;
var CLOSE_UNSUPPORTED_DATA = 1003;
var CLOSE_TOO_LARGE = 1009;

// 单帧负载上限。本项目最大帧是 100000 字符的 evalScript 结果回传（契约 §4.1），
// 200KB 的余量足够覆盖 UTF-8 中文膨胀，同时避免被恶意大帧打爆内存。
var DEFAULT_MAX_PAYLOAD = 8 * 1024 * 1024;

function sha1Base64(text) {
  return crypto.createHash("sha1").update(text, "binary").digest("base64");
}

function isValidWebSocketKey(key) {
  if (typeof key !== "string") {
    return false;
  }
  if (key.length !== 24) {
    return false;
  }
  return /^[A-Za-z0-9+/]{22}==$/.test(key);
}

/** 极简 EventEmitter（不依赖 events 模块，避免宿主 polyfill 差异）。 */
function Emitter() {
  this._handlers = {};
}

Emitter.prototype.on = function (event, fn) {
  if (typeof fn !== "function") {
    return this;
  }
  if (!this._handlers[event]) {
    this._handlers[event] = [];
  }
  this._handlers[event].push(fn);
  return this;
};

Emitter.prototype.removeListener = function (event, fn) {
  var list = this._handlers[event];
  if (!list) {
    return this;
  }
  for (var i = list.length - 1; i >= 0; i--) {
    if (list[i] === fn) {
      list.splice(i, 1);
    }
  }
  return this;
};

Emitter.prototype.emit = function (event) {
  var list = this._handlers[event];
  if (!list || !list.length) {
    return false;
  }
  var args = Array.prototype.slice.call(arguments, 1);
  var snapshot = list.slice();
  for (var i = 0; i < snapshot.length; i++) {
    try {
      snapshot[i].apply(this, args);
    } catch (err) {
      // 监听器抛错不得中断帧循环；交给宿主控制台，不吞进日志正文。
      if (typeof console !== "undefined" && console.error) {
        console.error("[mini-ws] listener error: " + (err && err.message ? err.message : err));
      }
    }
  }
  return true;
};

/* ------------------------------------------------------------------ */
/* 单条连接                                                            */
/* ------------------------------------------------------------------ */

/**
 * @param {net.Socket} socket 已完成 Upgrade 的原始套接字
 */
function Connection(socket, options) {
  Emitter.call(this);
  options = options || {};
  this._socket = socket;
  this._buffer = Buffer.alloc(0);
  this._closed = false;
  this._closeEmitted = false;
  this._fragmented = false;
  this._localCloseCode = 0;
  this._localCloseReason = "";
  this._maxPayload = options.maxPayload || DEFAULT_MAX_PAYLOAD;
  // 服务端发出的帧一律不掩码（RFC6455 §5.1）。
  var self = this;
  socket.on("data", function (chunk) {
    self._onData(chunk);
  });
  socket.on("error", function () {
    self._finish(1006, "socket_error");
  });
  socket.on("close", function () {
    self._finish(1006, "socket_closed");
  });
  socket.setNoDelay(true);
}

Connection.prototype = Object.create(Emitter.prototype);
Connection.prototype.constructor = Connection;

Connection.prototype.remoteAddress = function () {
  return this._socket && this._socket.remoteAddress ? this._socket.remoteAddress : "";
};

/**
 * 暴露底层 socket，仅供本仓库的自测脚本在 TCP 层观察控制帧字节
 * （ping/pong 无法通过 Node 内置 WebSocket 客户端 API 触发）。
 * 生产代码（ws-server.js）不应使用。
 */
Connection.prototype.socket = function () {
  return this._socket;
};

/** 发送一个文本帧。返回 false 表示连接已关闭。 */
Connection.prototype.send = function (text) {
  if (this._closed || !this._socket.writable) {
    return false;
  }
  var payload = Buffer.from(String(text), "utf8");
  this._writeFrame(OPCODE_TEXT, payload);
  return true;
};

/** 发送 close 帧并关闭底层套接字。幂等。 */
Connection.prototype.close = function (code, reason) {
  if (this._closed) {
    return;
  }
  var reasonText = reason ? String(reason) : "";
  var payload;
  if (code) {
    var reasonBuf = Buffer.from(reasonText, "utf8");
    payload = Buffer.alloc(2 + reasonBuf.length);
    payload.writeUInt16BE(code & 0xffff, 0);
    reasonBuf.copy(payload, 2);
  } else {
    payload = Buffer.alloc(0);
  }
  this._writeFrame(OPCODE_CLOSE, payload);
  var self = this;
  // 记住本地主动关闭的码：底层 socket 关闭事件只能报告 1006（异常关闭），
  // 不能代表我方发出的关闭码，因此这里显式记住，保证 close 事件语义确定。
  this._localCloseCode = code || 1000;
  this._localCloseReason = reasonText;
  // 给对端一点时间收到 close 帧，再拆 socket。
  this._closeTimer = setTimeout(function () {
    self._teardown();
  }, 50);
  if (this._closeTimer && typeof this._closeTimer.unref === "function") {
    this._closeTimer.unref();
  }
  this._closed = true;
};

Connection.prototype._teardown = function () {
  if (this._closeTimer) {
    clearTimeout(this._closeTimer);
    this._closeTimer = null;
  }
  try {
    this._socket.destroy();
  } catch (err) {
    /* 已销毁 */
  }
};

Connection.prototype._finish = function (code, reason) {
  if (this._closeTimer) {
    clearTimeout(this._closeTimer);
    this._closeTimer = null;
  }
  this._closed = true;
  if (this._closeEmitted) {
    return;
  }
  this._closeEmitted = true;
  // 本地主动关闭时，底层 socket 只能报告 1006；用记录下来的关闭码覆盖，
  // 使调用方拿到确定语义（1000 = 我方正常关闭）。
  var finalCode = code;
  var finalReason = reason;
  if (this._localCloseCode && (code === 1006 || code === undefined)) {
    finalCode = this._localCloseCode;
    finalReason = this._localCloseReason;
  }
  this.emit("close", finalCode, finalReason);
};

Connection.prototype._writeFrame = function (opcode, payload) {
  var length = payload.length;
  var header;
  if (length < 126) {
    header = Buffer.alloc(2);
    header[1] = length;
  } else if (length < 65536) {
    header = Buffer.alloc(4);
    header[1] = 126;
    header.writeUInt16BE(length, 2);
  } else {
    header = Buffer.alloc(10);
    header[1] = 127;
    // 高 32 位恒为 0（本项目帧远小于 4GB）。
    header.writeUInt32BE(0, 2);
    header.writeUInt32BE(length, 6);
  }
  header[0] = 0x80 | (opcode & 0x0f); // FIN=1
  try {
    this._socket.write(Buffer.concat([header, payload]));
  } catch (err) {
    this._finish(1006, "write_failed");
  }
};

Connection.prototype._fail = function (code, reason) {
  this.close(code, reason);
};

Connection.prototype._onData = function (chunk) {
  if (this._closed) {
    return;
  }
  this._buffer = Buffer.concat([this._buffer, chunk]);
  // 单次 data 事件可能带来多个帧，循环消费直到不足一帧。
  while (!this._closed) {
    var frame = this._readFrame();
    if (!frame) {
      return;
    }
    if (frame.error) {
      this._fail(frame.error, frame.reason);
      return;
    }
    this._handleFrame(frame);
  }
};

/**
 * 从缓冲区尝试取出一帧。
 * @returns {null|{error?:number, reason?:string, fin:boolean, opcode:number, payload:Buffer}}
 */
Connection.prototype._readFrame = function () {
  var buf = this._buffer;
  if (buf.length < 2) {
    return null;
  }
  var b0 = buf[0];
  var b1 = buf[1];
  var fin = (b0 & 0x80) === 0x80;
  var rsv = b0 & 0x70;
  var opcode = b0 & 0x0f;
  var masked = (b1 & 0x80) === 0x80;
  var length = b1 & 0x7f;
  var offset = 2;

  if (rsv !== 0) {
    return { error: CLOSE_PROTOCOL_ERROR, reason: "rsv_bits_set" };
  }
  if (!masked) {
    // 客户端 → 服务端必须掩码（RFC6455 §5.1）。
    return { error: CLOSE_PROTOCOL_ERROR, reason: "unmasked_client_frame" };
  }
  if (length === 126) {
    if (buf.length < offset + 2) {
      return null;
    }
    length = buf.readUInt16BE(offset);
    offset += 2;
  } else if (length === 127) {
    if (buf.length < offset + 8) {
      return null;
    }
    var high = buf.readUInt32BE(offset);
    var low = buf.readUInt32BE(offset + 4);
    if (high !== 0) {
      return { error: CLOSE_TOO_LARGE, reason: "payload_too_large" };
    }
    length = low;
    offset += 8;
  }
  if (length > this._maxPayload) {
    return { error: CLOSE_TOO_LARGE, reason: "payload_too_large" };
  }
  if (buf.length < offset + 4 + length) {
    return null;
  }
  var mask = buf.slice(offset, offset + 4);
  offset += 4;
  var payload = Buffer.alloc(length);
  for (var i = 0; i < length; i++) {
    payload[i] = buf[offset + i] ^ mask[i % 4];
  }
  offset += length;
  this._buffer = buf.slice(offset);
  return { fin: fin, opcode: opcode, payload: payload };
};

Connection.prototype._handleFrame = function (frame) {
  var opcode = frame.opcode;

  if (opcode === OPCODE_PING) {
    this._writeFrame(OPCODE_PONG, frame.payload);
    return;
  }
  if (opcode === OPCODE_PONG) {
    this.emit("pong", frame.payload);
    return;
  }
  if (opcode === OPCODE_CLOSE) {
    var code = 1005;
    var reason = "";
    if (frame.payload.length >= 2) {
      code = frame.payload.readUInt16BE(0);
      reason = frame.payload.slice(2).toString("utf8");
    }
    // 顺序很重要：先回 close 帧，再拆 socket，最后通知业务层。
    // 若先把 _closed 置位，_writeFrame 会拒绝写出（但 _writeFrame 只检查 socket.writable，
    // 所以这里仍然显式保证顺序，避免将来有人给 _writeFrame 加上 _closed 检查而破坏协议）。
    this._writeFrame(OPCODE_CLOSE, frame.payload.length >= 2 ? frame.payload.slice(0, 2) : Buffer.alloc(0));
    this._closed = true;
    this._teardown();
    this._finish(code, reason);
    return;
  }
  if (opcode === OPCODE_BINARY) {
    this._fail(CLOSE_UNSUPPORTED_DATA, "binary_not_supported");
    return;
  }
  if (opcode === OPCODE_CONTINUATION) {
    // 本项目所有帧都是单帧文本；分片一律拒绝（契约 §7 要求 1003）。
    this._fail(CLOSE_UNSUPPORTED_DATA, "fragmented_frame");
    return;
  }
  if (opcode !== OPCODE_TEXT) {
    this._fail(CLOSE_PROTOCOL_ERROR, "unknown_opcode");
    return;
  }
  if (!frame.fin) {
    // 文本帧但 FIN=0：分片开始，拒绝。
    this._fail(CLOSE_UNSUPPORTED_DATA, "fragmented_text_frame");
    return;
  }
  this.emit("message", frame.payload.toString("utf8"));
};

/* ------------------------------------------------------------------ */
/* 服务端                                                              */
/* ------------------------------------------------------------------ */

/**
 * @param {{host?:string, port?:number, maxPayload?:number}} options
 *   host 默认 127.0.0.1（说明书约束 C-5：绝不监听非回环地址）。
 *   port 默认 0，由 OS 分配动态端口。
 */
function WebSocketServer(options) {
  Emitter.call(this);
  options = options || {};
  var self = this;
  this.host = options.host || "127.0.0.1";
  this.port = typeof options.port === "number" ? options.port : 0;
  this._maxPayload = options.maxPayload || DEFAULT_MAX_PAYLOAD;
  this._connections = [];
  this._closed = false;
  this._listenError = null;

  this._httpServer = http.createServer(function (req, res) {
    // 非 Upgrade 的普通 HTTP 请求：不回内容，避免暴露实现细节。
    res.writeHead(426, { "Content-Type": "text/plain", Connection: "close" });
    res.end("Upgrade Required\n");
  });

  this._httpServer.on("upgrade", function (req, socket, head) {
    self._handleUpgrade(req, socket, head);
  });

  this._httpServer.on("error", function (err) {
    self._listenError = err;
    self.emit("error", err);
  });

  this._httpServer.listen(this.port, this.host, function () {
    var addr = self._httpServer.address();
    self.port = addr && typeof addr === "object" ? addr.port : self.port;
    self.emit("listening", self.port);
  });
}

WebSocketServer.prototype = Object.create(Emitter.prototype);
WebSocketServer.prototype.constructor = WebSocketServer;

WebSocketServer.prototype._handleUpgrade = function (req, socket, head) {
  var key = req.headers["sec-websocket-key"];
  var version = req.headers["sec-websocket-version"];
  var upgrade = String(req.headers.upgrade || "").toLowerCase();

  if (upgrade !== "websocket" || !isValidWebSocketKey(key) || version !== "13") {
    socket.write("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n");
    socket.destroy();
    return;
  }

  var accept = sha1Base64(key + GUID);
  socket.write(
    "HTTP/1.1 101 Switching Protocols\r\n" +
      "Upgrade: websocket\r\n" +
      "Connection: Upgrade\r\n" +
      "Sec-WebSocket-Accept: " + accept + "\r\n" +
      "\r\n"
  );

  var conn = new Connection(socket, { maxPayload: this._maxPayload });
  this._connections.push(conn);
  var self = this;
  conn.on("close", function () {
    for (var i = self._connections.length - 1; i >= 0; i--) {
      if (self._connections[i] === conn) {
        self._connections.splice(i, 1);
      }
    }
  });

  if (head && head.length) {
    conn._onData(head);
  }
  this.emit("connection", conn, req);
};

/** 列出当前活动连接（只读快照）。 */
WebSocketServer.prototype.connections = function () {
  return this._connections.slice();
};

/** 关闭服务端：先关所有连接，再停监听。幂等。 */
WebSocketServer.prototype.close = function (callback) {
  if (this._closed) {
    if (typeof callback === "function") {
      callback();
    }
    return;
  }
  this._closed = true;
  var conns = this._connections.slice();
  for (var i = 0; i < conns.length; i++) {
    try {
      conns[i].close(1001, "server_shutdown");
    } catch (err) {
      /* 忽略 */
    }
  }
  var self = this;
  try {
    this._httpServer.close(function () {
      self.emit("close");
      if (typeof callback === "function") {
        callback();
      }
    });
  } catch (err) {
    if (typeof callback === "function") {
      callback();
    }
  }
  // 极端情况下 close 回调不来（keep-alive 挂起），兜底销毁。
  var t = setTimeout(function () {
    try {
      self._httpServer.closeAllConnections && self._httpServer.closeAllConnections();
    } catch (err) {
      /* Node 12 没有该 API，忽略 */
    }
  }, 1000);
  if (t && typeof t.unref === "function") {
    t.unref();
  }
};

module.exports = {
  WebSocketServer: WebSocketServer,
  Connection: Connection,
  GUID: GUID,
  computeAccept: function (key) {
    return sha1Base64(key + GUID);
  }
};
