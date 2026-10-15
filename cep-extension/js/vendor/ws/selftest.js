"use strict";
/*
 * =====================================================================
 * cep-extension/js/vendor/ws/selftest.js
 * ---------------------------------------------------------------------
 * 最小 WebSocket 服务实现的离线自测。用 Node 内置的全局 WebSocket 客户端
 * （Node 22+ 提供；开发机上用 DSH 自带的 Electron Node 24 运行）连接本实现的
 * 服务端，覆盖：
 *   1. HTTP Upgrade 握手成功、Sec-WebSocket-Accept 正确
 *   2. 文本帧收发（含中文与 200KB 大帧）
 *   3. ping / pong
 *   4. close 帧
 *   5. 未掩码的客户端帧被以 1002 拒绝
 *   6. 客户端分片帧被以 1003 拒绝
 *
 * 运行（本机无 node.exe，用 Electron 当 Node 用）：
 *   $env:ELECTRON_RUN_AS_NODE="1"
 *   & "F:\DeepseekHarness\DeepSeek Harness.exe" cep-extension\js\vendor\ws\selftest.js
 * 退出码 0 表示全部通过。
 * =====================================================================
 */

var http = require("http");
var crypto = require("crypto");
var net = require("net");
var path = require("path");

var ws = require(path.join(__dirname, "index.js"));
var WebSocketServer = ws.WebSocketServer;

var failures = 0;
var checks = 0;

function ok(name, detail) {
  checks++;
  console.log("ok   - " + name + (detail ? " (" + detail + ")" : ""));
}

function fail(name, detail) {
  checks++;
  failures++;
  console.log("FAIL - " + name + (detail ? ": " + detail : ""));
}

function assert(name, condition, detail) {
  if (condition) {
    ok(name, detail);
  } else {
    fail(name, detail);
  }
}

function delay(ms) {
  return new Promise(function (resolve) {
    setTimeout(resolve, ms);
  });
}

/* 诊断用：Electron-run-as-node 下静默退出时，必须看到到底在哪一步断的。 */
if (process.env.DSH_WS_SELFTEST_TRACE) {
  ["exit", "uncaughtException", "unhandledRejection", "beforeExit"].forEach(function (evt) {
    process.on(evt, function (a, b) {
      console.log("[trace] process." + evt + " " + (a && a.stack ? a.stack : a) + (b ? " " + b : ""));
    });
  });
}

/** 等待某个 Promise，超时则 reject。 */
function withTimeout(promise, ms, label) {
  return new Promise(function (resolve, reject) {
    var timer = setTimeout(function () {
      reject(new Error("timeout after " + ms + "ms: " + label));
    }, ms);
    promise.then(
      function (value) {
        clearTimeout(timer);
        resolve(value);
      },
      function (err) {
        clearTimeout(timer);
        reject(err);
      }
    );
  });
}

/** 打开一个已连接的 WebSocket 客户端。 */
function connect(port) {
  return withTimeout(
    new Promise(function (resolve, reject) {
      var client = new WebSocket("ws://127.0.0.1:" + port + "/");
      client.binaryType = "arraybuffer";
      client.onopen = function () {
        resolve(client);
      };
      client.onerror = function (event) {
        reject(new Error("client error: " + (event && event.message ? event.message : "unknown")));
      };
    }),
    5000,
    "client connect"
  );
}

/**
 * 用手写 TCP 客户端完成 Upgrade。测试未掩码帧/分片帧时必须手写，
 * 因为 Node 内置 WebSocket 客户端永远会正确掩码。
 * 返回 { socket, head, accept }。
 */
function rawUpgrade(port) {
  return withTimeout(
    new Promise(function (resolve, reject) {
      var key = crypto.randomBytes(16).toString("base64");
      var socket = net.connect(port, "127.0.0.1", function () {
        socket.write(
          "GET / HTTP/1.1\r\n" +
            "Host: 127.0.0.1:" + port + "\r\n" +
            "Upgrade: websocket\r\n" +
            "Connection: Upgrade\r\n" +
            "Sec-WebSocket-Key: " + key + "\r\n" +
            "Sec-WebSocket-Version: 13\r\n" +
            "\r\n"
        );
      });
      var buffer = Buffer.alloc(0);
      var handshaken = false;
      socket.on("data", function (chunk) {
        buffer = Buffer.concat([buffer, chunk]);
        if (!handshaken) {
          var idx = buffer.indexOf("\r\n\r\n");
          if (idx === -1) {
            return;
          }
          var head = buffer.slice(0, idx).toString("latin1");
          handshaken = true;
          buffer = buffer.slice(idx + 4);
          // 注册监听以保持引用稳定（socket 需要保持在 read 状态）。
          socket.setTimeout(0);
          resolve({ socket: socket, head: head, accept: key });
        }
      });
      socket.on("error", reject);
    }),
    5000,
    "raw upgrade"
  );
}

/** 从字节流里解析出一个服务端帧（服务端帧不掩码）。 */
function parseServerFrame(buf) {
  if (buf.length < 2) {
    return null;
  }
  var opcode = buf[0] & 0x0f;
  var length = buf[1] & 0x7f;
  var offset = 2;
  if (length === 126) {
    if (buf.length < 4) {
      return null;
    }
    length = buf.readUInt16BE(2);
    offset = 4;
  } else if (length === 127) {
    if (buf.length < 10) {
      return null;
    }
    length = buf.readUInt32BE(6);
    offset = 10;
  }
  if (buf.length < offset + length) {
    return null;
  }
  return { opcode: opcode, payload: buf.slice(offset, offset + length), size: offset + length };
}

function maskFrame(opcode, payload, fin) {
  var mask = crypto.randomBytes(4);
  var len = payload.length;
  var header;
  if (len < 126) {
    header = Buffer.alloc(2);
    header[1] = 0x80 | len;
  } else if (len < 65536) {
    header = Buffer.alloc(4);
    header[1] = 0x80 | 126;
    header.writeUInt16BE(len, 2);
  } else {
    header = Buffer.alloc(10);
    header[1] = 0x80 | 127;
    header.writeUInt32BE(0, 2);
    header.writeUInt32BE(len, 6);
  }
  header[0] = (fin === false ? 0x00 : 0x80) | (opcode & 0x0f);
  var masked = Buffer.alloc(len);
  for (var i = 0; i < len; i++) {
    masked[i] = payload[i] ^ mask[i % 4];
  }
  return Buffer.concat([header, mask, masked]);
}

function unmaskedFrame(opcode, payload) {
  var len = payload.length;
  var header = Buffer.alloc(2);
  header[0] = 0x80 | (opcode & 0x0f);
  header[1] = len; // 无掩码位
  return Buffer.concat([header, payload]);
}

/* 服务端连接队列：需要把"某个客户端 socket"和"服务端对应的 conn"配起来，
 * 否则很容易把测试帧打到一条已经关闭的连接上（本自测早期就踩过这个坑）。 */
var connQueue = [];
var connWaiters = [];

function onServerConnection(conn) {
  if (connWaiters.length) {
    connWaiters.shift()(conn);
  } else {
    connQueue.push(conn);
  }
}

function nextServerConn(label) {
  if (connQueue.length) {
    return Promise.resolve(connQueue.shift());
  }
  return withTimeout(
    new Promise(function (resolve) {
      connWaiters.push(resolve);
    }),
    5000,
    "server connection for " + label
  );
}

/** 建立一条手写 TCP + Upgrade 的连接，返回 { socket, serverConn, head }。 */
async function rawPair(port, label) {
  var p = nextServerConn(label);
  var raw = await rawUpgrade(port);
  var serverConn = await p;
  return { socket: raw.socket, serverConn: serverConn, head: raw.head, accept: raw.accept };
}

async function main() {
  var server = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  server.on("connection", onServerConnection);
  await withTimeout(
    new Promise(function (resolve, reject) {
      server.on("listening", resolve);
      server.on("error", reject);
    }),
    5000,
    "server listening"
  );

  assert("服务端监听成功并拿到动态端口", typeof server.port === "number" && server.port > 0, "port=" + server.port);

  // ---- 1. 握手：普通 HTTP 请求应被 426 拒绝 -------------------------
  await new Promise(function (resolve) {
    var req = http.request(
      { host: "127.0.0.1", port: server.port, path: "/", method: "GET" },
      function (res) {
        assert("非 Upgrade 的 HTTP 请求被拒绝", res.statusCode === 426, "status=" + res.statusCode);
        res.resume();
        res.on("end", resolve);
      }
    );
    req.on("error", function (err) {
      fail("非 Upgrade 的 HTTP 请求被拒绝", err.message);
      resolve();
    });
    req.end();
  });

  // ---- 2. 握手：正确计算 Sec-WebSocket-Accept ----------------------
  var p2 = nextServerConn("accept check");
  var raw = await rawUpgrade(server.port);
  var rawConn2 = await p2;
  var expectedAccept = crypto
    .createHash("sha1")
    .update(raw.accept + ws.GUID)
    .digest("base64");
  assert(
    "Upgrade 握手返回 101 且 Sec-WebSocket-Accept 正确",
    raw.head.indexOf("101") !== -1 && raw.head.indexOf(expectedAccept) !== -1,
    "accept 匹配"
  );
  rawConn2.close(1000, "handshake-check-done");
  raw.socket.destroy();

  // ---- 3. 连接事件 --------------------------------------------------
  var p3 = nextServerConn("main client");
  var client = await connect(server.port);
  var serverSide = await p3;
  assert("服务端触发 connection 事件", !!serverSide);

  // ---- 4. 文本收发：ASCII ------------------------------------------
  var echoed = new Promise(function (resolve) {
    client.onmessage = function (event) {
      resolve(String(event.data));
    };
  });
  serverSide.send("hello-from-server");
  var gotHello = await withTimeout(echoed, 5000, "ascii text frame");
  assert("文本帧 服务端→客户端", gotHello === "hello-from-server", "got=" + gotHello);

  // ---- 5. 文本收发：中文 -------------------------------------------
  var back = new Promise(function (resolve) {
    client.onmessage = function (event) {
      resolve(String(event.data));
    };
  });
  serverSide.send("中文测试：时间线插入片段 ✓");
  var gotChinese = await withTimeout(back, 5000, "utf8 text frame");
  assert("文本帧 中文 UTF-8 往返", gotChinese === "中文测试：时间线插入片段 ✓", "got=" + gotChinese);

  // ---- 6. 大帧 200KB ------------------------------------------------
  var big = "大".repeat(100000) + "-end"; // UTF-8 下 300KB+
  var bigBack = new Promise(function (resolve) {
    client.onmessage = function (event) {
      resolve(String(event.data));
    };
  });
  serverSide.send(big);
  var gotBig = await withTimeout(bigBack, 10000, "200KB+ text frame");
  assert(
    "200KB 以上大帧往返完整未截断",
    gotBig.length === big.length && gotBig === big,
    "len=" + gotBig.length
  );

  // 客户端 → 服务端 大帧
  var upBig = "x".repeat(200 * 1024);
  serverSide.on("message", function (text) {
    if (text.length === upBig.length) {
      ok("客户端 200KB 大帧被服务端完整接收", "len=" + text.length);
    }
  });
  client.send(upBig);
  await delay(300);

  // ---- 7. ping / pong ----------------------------------------------
  // 用一条**独立的手写连接**测控制帧：不能复用主连接，因为 mini-ws 的
  // Connection 已经挂了 socket 的 "data" 监听来解析帧，再挂一个会与之竞争
  // （两个 listener 都会收到 chunk，帧会被其中一方吃掉）。
  var p7 = nextServerConn("ping/pong");
  var raw7 = await rawUpgrade(server.port);
  var rawConn7 = await p7;

  // 7a. 服务端收到 ping 帧必须回 pong 帧（在线上观察字节）
  var pongFrame = await withTimeout(
    new Promise(function (resolve) {
      var acc = Buffer.alloc(0);
      var onData = function (chunk) {
        acc = Buffer.concat([acc, chunk]);
        var frame = parseServerFrame(acc);
        if (frame && frame.opcode === 0xa) {
          acc = acc.slice(frame.size);
          resolve(frame.payload);
        }
      };
      raw7.socket.on("data", onData);
      raw7.socket.write(maskFrame(0x9, Buffer.from("ping-payload")));
    }),
    5000,
    "pong frame on the wire"
  );
  assert(
    "ping 帧触发服务端回 pong（线上字节）",
    pongFrame.toString("utf8") === "ping-payload",
    "pong payload=" + pongFrame.toString("utf8")
  );

  // 7b. 服务端收到 pong 帧时触发 pong 事件
  var pongEvent = await withTimeout(
    new Promise(function (resolve) {
      rawConn7.on("pong", resolve);
      raw7.socket.write(maskFrame(0xa, Buffer.from("client-pong")));
    }),
    5000,
    "pong event"
  );
  assert(
    "收到 pong 帧触发 pong 事件",
    pongEvent.toString("utf8") === "client-pong",
    "payload=" + pongEvent.toString("utf8")
  );
  rawConn7.close(1000, "ping-pong-done");
  raw7.socket.destroy();
  await delay(100);

  // ---- 8. close 帧 --------------------------------------------------
  var clientClosed = new Promise(function (resolve) {
    client.onclose = function (event) {
      resolve(event);
    };
  });
  var serverCloseSeen = new Promise(function (resolve) {
    serverSide.on("close", function (code, reason) {
      resolve({ code: code, reason: reason });
    });
  });
  serverSide.close(1000, "bye");
  var closeEvent = await withTimeout(clientClosed, 5000, "client close");
  var serverCloseResult = await withTimeout(serverCloseSeen, 5000, "server close event");
  assert(
    "close 帧：客户端收到 1000 关闭",
    closeEvent && closeEvent.code === 1000,
    "client close code=" + (closeEvent && closeEvent.code)
  );
  assert(
    "close 帧：服务端 close 事件携带主动关闭码 1000",
    serverCloseResult.code === 1000,
    "server close code=" + serverCloseResult.code
  );
  await delay(100);

  // ---- 9. 未掩码帧被拒（1002） --------------------------------------
  var p9 = nextServerConn("unmasked frame");
  var raw2 = await rawUpgrade(server.port);
  var rawConn9 = await p9;
  var closeFrame = await withTimeout(
    new Promise(function (resolve) {
      var acc = Buffer.alloc(0);
      raw2.socket.on("data", function (chunk) {
        acc = Buffer.concat([acc, chunk]);
        var frame = parseServerFrame(acc);
        if (frame && frame.opcode === 0x8) {
          resolve(frame.payload);
        }
      });
      raw2.socket.on("close", function () {
        resolve(Buffer.alloc(0));
      });
      // 关键：监听器挂好之后再发未掩码帧，否则 close 帧可能在监听器就绪前就到。
      raw2.socket.write(unmaskedFrame(0x1, Buffer.from("unmasked")));
    }),
    5000,
    "unmasked rejection"
  );
  var closeCode = closeFrame.length >= 2 ? closeFrame.readUInt16BE(0) : -1;
  assert("未掩码的客户端帧被以 1002 拒绝", closeCode === 1002, "close code=" + closeCode);
  assert("未掩码帧连接已注册为服务端连接", !!rawConn9);
  raw2.socket.destroy();
  await delay(100);

  // ---- 10. 分片帧被拒（1003） ---------------------------------------
  var p10 = nextServerConn("fragmented frame");
  var raw3 = await rawUpgrade(server.port);
  await p10;
  var closeFrame3 = await withTimeout(
    new Promise(function (resolve) {
      var acc = Buffer.alloc(0);
      raw3.socket.on("data", function (chunk) {
        acc = Buffer.concat([acc, chunk]);
        var frame = parseServerFrame(acc);
        if (frame && frame.opcode === 0x8) {
          resolve(frame.payload);
        }
      });
      raw3.socket.on("close", function () {
        resolve(Buffer.alloc(0));
      });
      raw3.socket.write(maskFrame(0x1, Buffer.from("frag-start"), false)); // FIN=0
    }),
    5000,
    "fragment rejection"
  );
  var closeCode3 = closeFrame3.length >= 2 ? closeFrame3.readUInt16BE(0) : -1;
  assert("分片（FIN=0）文本帧被以 1003 拒绝", closeCode3 === 1003, "close code=" + closeCode3);
  raw3.socket.destroy();
  await delay(100);

  // ---- 11. 非法握手被拒 ---------------------------------------------
  await new Promise(function (resolve) {
    var key = crypto.randomBytes(16).toString("base64");
    var socket = net.connect(server.port, "127.0.0.1", function () {
      socket.write(
        "GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" +
          "Sec-WebSocket-Key: " + key + "\r\nSec-WebSocket-Version: 8\r\n\r\n"
      );
    });
    var acc = "";
    socket.on("data", function (chunk) {
      acc += chunk.toString("latin1");
      if (acc.indexOf("\r\n\r\n") !== -1 || acc.indexOf("400") !== -1) {
        assert("Sec-WebSocket-Version 非 13 时握手被拒", acc.indexOf("400") !== -1, acc.split("\r\n")[0]);
        socket.destroy();
        resolve();
      }
    });
    socket.on("error", function () {
      resolve();
    });
    setTimeout(function () {
      socket.destroy();
      resolve();
    }, 2000);
  });

  await withTimeout(
    new Promise(function (resolve) {
      server.close(resolve);
    }),
    5000,
    "server close"
  );
  ok("服务端 close() 正常返回");

  console.log("");
  console.log(failures === 0 ? "SELFTEST PASSED (" + checks + " checks)" : "SELFTEST FAILED (" + failures + "/" + checks + ")");
  process.exit(failures === 0 ? 0 : 1);
}

main().catch(function (err) {
  console.error("SELFTEST ERROR: " + (err && err.stack ? err.stack : err));
  process.exit(1);
});
