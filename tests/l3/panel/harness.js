"use strict";
/*
 * =====================================================================
 * tests/l3/panel/harness.js
 * ---------------------------------------------------------------------
 * L3 面板测试脚手架。提供：
 *   - 临时目录（握手文件 + 日志 + 伪造的 %TEMP%），测试结束清理；
 *   - 用伪 CSInterface 启动**真实**面板（js/main.js 的 startPanel）；
 *   - 一个 WebSocket 测试客户端（Node 内置全局 WebSocket），
 *     会记录所有收到的帧，并支持"等某一帧出现"；
 *   - 断言辅助（check / checkEqual / checkThrows …），逐条打印 ok / FAIL。
 *
 * 关键约束：这里**不重新实现**面板逻辑。测试驱动的是 js/main.js →
 * js/ws-server.js → js/dispatch.js → js/vendor/ws 这条真实链路，
 * 只把 CEP 宿主能力（CSInterface）换成替身。
 * =====================================================================
 */

var fs = require("fs");
var os = require("os");
var path = require("path");

var fakeCep = require(path.join(__dirname, "fake_cep.js"));

var PANEL_ROOT = path.resolve(__dirname, "..", "..", "..", "cep-extension");
var JS_DIR = path.join(PANEL_ROOT, "js");

/* ------------------------------------------------------------------ */
/* 断言与打印                                                          */
/* ------------------------------------------------------------------ */

function createReport() {
  var checks = [];
  var failures = 0;

  function ok(name, detail) {
    checks.push({ status: "ok", name: name, detail: detail || "" });
    console.log("ok   - " + name + (detail ? " (" + detail + ")" : ""));
  }

  function fail(name, detail) {
    checks.push({ status: "FAIL", name: name, detail: detail || "" });
    failures++;
    console.log("FAIL - " + name + (detail ? ": " + detail : ""));
  }

  return {
    ok: ok,
    fail: fail,
    check: function (name, condition, detail) {
      if (condition) {
        ok(name, detail);
      } else {
        fail(name, detail);
      }
      return !!condition;
    },
    checkEqual: function (name, actual, expected, extra) {
      var same = actual === expected;
      if (same) {
        ok(name, "= " + JSON.stringify(actual) + (extra ? " " + extra : ""));
      } else {
        fail(name, "期望 " + JSON.stringify(expected) + "，实际 " + JSON.stringify(actual) + (extra ? " " + extra : ""));
      }
      return same;
    },
    section: function (title) {
      console.log("");
      console.log("## " + title);
    },
    summary: function () {
      console.log("");
      console.log("------------------------------------------------------------------");
      if (failures === 0) {
        console.log("PANEL L3 HARNESS PASSED (" + checks.length + " checks)");
      } else {
        console.log("PANEL L3 HARNESS FAILED (" + failures + " / " + checks.length + " checks)");
      }
      return { failures: failures, total: checks.length, checks: checks };
    }
  };
}

/* ------------------------------------------------------------------ */
/* 临时目录                                                           */
/* ------------------------------------------------------------------ */

function createTempEnv(prefix) {
  var base = path.join(os.tmpdir(), prefix || "dsh-l3-panel-test");
  var unique = base + "-" + process.pid + "-" + Date.now().toString(36);
  var handshakeDir = path.join(unique, "handshake");
  var logDir = path.join(unique, "logs");
  fs.mkdirSync(handshakeDir, { recursive: true });
  fs.mkdirSync(logDir, { recursive: true });

  return {
    root: unique,
    handshakeDir: handshakeDir,
    logDir: logDir,
    /** 列出当前握手文件（只取文件名）。 */
    handshakeFiles: function () {
      try {
        return fs.readdirSync(handshakeDir);
      } catch (err) {
        return [];
      }
    },
    readHandshake: function () {
      var files = this.handshakeFiles();
      if (files.length === 0) {
        return null;
      }
      return JSON.parse(fs.readFileSync(path.join(handshakeDir, files[0]), "utf8"));
    },
    logFiles: function () {
      try {
        return fs.readdirSync(logDir);
      } catch (err) {
        return [];
      }
    },
    /** 读出全部日志行（含只有内存的降级情况由 logger.tail 兜底）。 */
    readLogText: function () {
      var files = this.logFiles();
      var out = "";
      for (var i = 0; i < files.length; i++) {
        out += fs.readFileSync(path.join(logDir, files[i]), "utf8");
      }
      return out;
    },
    cleanup: function () {
      try {
        fs.rmSync(unique, { recursive: true, force: true });
      } catch (err) {
        /* 清理失败不阻塞测试 */
      }
    }
  };
}

/* ------------------------------------------------------------------ */
/* 启动面板                                                            */
/* ------------------------------------------------------------------ */

/**
 * 用伪 CSInterface 启动真实面板。
 *
 * @param {object} options
 *   env          createTempEnv() 的结果（必需）
 *   csInterface  伪 CSInterface（缺省自建一个 defaultResult 的）
 *   config       覆盖面板配置（如 heartbeat_s / eval_max_chars）
 * @returns {Promise<object>} { panel, csInterface, main, config, logger, env }
 */
function startTestPanel(options) {
  options = options || {};
  var env = options.env || createTempEnv();

  // 每次都清空 require 缓存，保证 config 模块的全局状态不会在用例之间串味。
  var configPath = path.join(JS_DIR, "config.js");
  var loggerPath = path.join(JS_DIR, "logger.js");
  var authPath = path.join(JS_DIR, "auth.js");
  var dispatchPath = path.join(JS_DIR, "dispatch.js");
  var wsServerPath = path.join(JS_DIR, "ws-server.js");
  var mainPath = path.join(JS_DIR, "main.js");
  var shimPath = path.resolve(PANEL_ROOT, "lib", "csinterface-shim.js");
  var miniWsPath = path.join(JS_DIR, "vendor", "ws", "index.js");

  [
    configPath, loggerPath, authPath, dispatchPath, wsServerPath, mainPath,
    shimPath, miniWsPath
  ].forEach(function (p) {
    delete require.cache[require.resolve(p)];
  });

  var config = require(configPath);
  config.resetConfig();
  config.setConfig({
    handshake_dir: env.handshakeDir,
    log_dir: env.logDir,
    log_level: options.logLevel || "debug",
    log_to_console: false,
    log_tail_lines: 500
  });
  if (options.config) {
    config.setConfig(options.config);
  }

  var csInterface =
    options.csInterface ||
    fakeCep.createFakeCsInterface({ defaultResult: JSON.stringify({ id: null, ok: true, data: {}, error: null, host: null, warnings: [], timing: { host_ms: 1 } }) });

  var main = require(mainPath);

  return main
    .startPanel({
      csInterface: csInterface,
      documentRef: options.documentRef !== undefined ? options.documentRef : null,
      windowRef: options.windowRef !== undefined ? options.windowRef : null,
      handshakeDir: env.handshakeDir,
      logDir: env.logDir,
      host: options.host || "PPRO",
      port: 0
    })
    .then(function (panel) {
      return {
        panel: panel,
        csInterface: csInterface,
        main: main,
        config: config,
        logger: panel.logger,
        env: env,
        dispatcher: panel.dispatcher,
        bridge: panel.bridge
      };
    });
}

/* ------------------------------------------------------------------ */
/* WebSocket 测试客户端                                                */
/* ------------------------------------------------------------------ */

function delay(ms) {
  return new Promise(function (resolve) {
    setTimeout(resolve, ms);
  });
}

function withTimeout(promise, ms, label) {
  return new Promise(function (resolve, reject) {
    var timer = setTimeout(function () {
      reject(new Error("超时 " + ms + "ms: " + label));
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

/**
 * 连接面板并记录所有收到的帧。
 *
 * @returns {Promise<object>} 客户端句柄
 */
function connectClient(port, options) {
  options = options || {};
  return withTimeout(
    new Promise(function (resolve, reject) {
      var client = new WebSocket("ws://127.0.0.1:" + port + "/");
      var frames = [];
      var waiters = [];
      var closedInfo = null;
      var closeWaiters = [];

      function pushFrame(frame) {
        frames.push(frame);
        for (var i = waiters.length - 1; i >= 0; i--) {
          if (waiters[i].match(frame)) {
            var waiter = waiters.splice(i, 1)[0];
            clearTimeout(waiter.timer);
            waiter.resolve(frame);
          }
        }
      }

      client.onmessage = function (event) {
        var text = String(event.data);
        var parsed = null;
        try {
          parsed = JSON.parse(text);
        } catch (err) {
          parsed = { _unparsed: text };
        }
        pushFrame(parsed);
      };
      client.onerror = function (event) {
        if (!client._opened) {
          reject(new Error("客户端连接失败: " + (event && event.message ? event.message : "unknown")));
        }
      };
      client.onclose = function (event) {
        closedInfo = { code: event.code, reason: event.reason, clean: event.wasClean };
        for (var i = closeWaiters.length - 1; i >= 0; i--) {
          var w = closeWaiters.splice(i, 1)[0];
          clearTimeout(w.timer);
          w.resolve(closedInfo);
        }
      };
      client.onopen = function () {
        client._opened = true;
        resolve({
          raw: client,
          port: port,
          frames: function () {
            return frames.slice();
          },
          lastFrame: function () {
            return frames.length ? frames[frames.length - 1] : null;
          },
          send: function (obj) {
            client.send(typeof obj === "string" ? obj : JSON.stringify(obj));
          },
          /** 等一帧满足条件的消息；已收到过的自然不会再触发，所以先查历史。 */
          waitFor: function (match, ms, label) {
            for (var i = 0; i < frames.length; i++) {
              if (match(frames[i])) {
                return Promise.resolve(frames[i]);
              }
            }
            return withTimeout(
              new Promise(function (res) {
                var waiter = { match: match, resolve: res, timer: null };
                waiter.timer = setTimeout(function () {
                  var idx = waiters.indexOf(waiter);
                  if (idx !== -1) {
                    waiters.splice(idx, 1);
                  }
                }, ms || 5000);
                waiters.push(waiter);
              }),
              ms || 5000,
              label || "waitFor frame"
            );
          },
          byType: function (type) {
            return frames.filter(function (f) {
              return f && f.type === type;
            });
          },
          waitForType: function (type, ms) {
            return this.waitFor(function (f) {
              return f && f.type === type;
            }, ms, "frame type=" + type);
          },
          waitForClose: function (ms) {
            if (closedInfo) {
              return Promise.resolve(closedInfo);
            }
            return withTimeout(
              new Promise(function (res) {
                var w = { resolve: res, timer: null };
                w.timer = setTimeout(function () {
                  var idx = closeWaiters.indexOf(w);
                  if (idx !== -1) {
                    closeWaiters.splice(idx, 1);
                  }
                }, ms || 5000);
                closeWaiters.push(w);
              }),
              ms || 5000,
              "connection close"
            );
          },
          closedInfo: function () {
            return closedInfo;
          },
          authenticate: function (token, extra) {
            var frame = {
              type: "auth",
              protocol: 1,
              token: token,
              client: "dsh-adobe-mcp",
              client_version: "1.1.0"
            };
            if (extra && extra.config) {
              frame.config = extra.config;
            }
            this.send(frame);
            return this.waitForType("auth_ok", 5000);
          },
          close: function () {
            try {
              client.close();
            } catch (err) {
              /* 已关闭 */
            }
          }
        });
      };
    }),
    8000,
    "client connect"
  );
}

/* ------------------------------------------------------------------ */
/* 小工具                                                             */
/* ------------------------------------------------------------------ */

/**
 * JSON 字符串字面量的反转义：把脚本里的 `DSH.dispatch("<...>")` 里的
 * 字符串字面量还原成它表示的字符串。用来断言面板生成的脚本确实能被
 * ExtendScript 正确解析（而不是只做字符串包含匹配）。
 */
function unquoteJsStringLiteral(literal) {
  // 用 JSON.parse 做反转义：JSON 字符串字面量与 ES3 字符串字面量在
  // \uXXXX、\"、\\、\n、\t、\b、\f、\r、\/ 上的语义一致，足以验证。
  return JSON.parse(literal);
}

/**
 * 从 `DSH.dispatch(<literal>)` 脚本中取出 literal 并解析出 payload 对象。
 * 这样测试断言的是"ExtendScript 实际会收到的对象"，而不是拼出来的字符串。
 */
function extractDispatchPayload(script) {
  var prefix = "DSH.dispatch(";
  if (script.indexOf(prefix) !== 0 || script.charAt(script.length - 1) !== ")") {
    throw new Error("脚本文本不是 DSH.dispatch(<literal>) 形式: " + script.slice(0, 80));
  }
  var literal = script.slice(prefix.length, script.length - 1);
  var inner = unquoteJsStringLiteral(literal);
  return JSON.parse(inner);
}

/** 递归查找对象中是否出现给定字符串（用于断言令牌没泄漏进日志）。 */
function deepContains(value, needle, depth) {
  depth = depth || 0;
  if (depth > 8 || value === null || value === undefined) {
    return false;
  }
  if (typeof value === "string") {
    return value.indexOf(needle) !== -1;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return false;
  }
  var keys = Object.keys(value);
  for (var i = 0; i < keys.length; i++) {
    if (deepContains(value[keys[i]], needle, depth + 1)) {
      return true;
    }
  }
  return false;
}

/* ------------------------------------------------------------------ */
/* 极简 DOM 替身                                                      */
/* ------------------------------------------------------------------ */

/**
 * 极简 DOM 替身：只为让 js/main.js 的 createPanelUi 真正跑一遍。
 *
 * 动机：本任务实现过程中唯一一个真实 bug（panel.config 写成了
 * configModule）就藏在 UI 渲染里，而 UI 代码在无 DOM 的测试里从不执行。
 * 这个替身不做布局，只实现 getElementById / createElement /
 * addEventListener / textContent / style / scrollTop 这些被用到的成员，
 * 目的是让"挂载 UI → 渲染状态 → 渲染日志 → 点按钮"这条路径的引用错误
 * 与类型错误在测试里暴露出来。
 */
function createFakeDom() {
  var elements = {};
  var listeners = {};

  function makeElement(id, tagName) {
    return {
      id: id,
      tagName: tagName || "div",
      textContent: "",
      innerHTML: "",
      value: "",
      style: {},
      className: "",
      scrollTop: 0,
      scrollHeight: 100,
      parentNode: null,
      children: [],
      addEventListener: function (type, fn) {
        listeners[id + ":" + type] = listeners[id + ":" + type] || [];
        listeners[id + ":" + type].push(fn);
      },
      removeEventListener: function () {},
      appendChild: function (child) {
        this.children.push(child);
        child.parentNode = this;
        elements[child.id] = child;
        return child;
      },
      removeChild: function (child) {
        for (var i = this.children.length - 1; i >= 0; i--) {
          if (this.children[i] === child) {
            this.children.splice(i, 1);
          }
        }
        child.parentNode = null;
        return child;
      },
      select: function () {},
      focus: function () {}
    };
  }

  var body = makeElement("body", "body");
  elements.body = body;

  var documentRef = {
    readyState: "complete",
    body: body,
    _elements: elements,
    _listeners: listeners,
    getElementById: function (id) {
      return elements[id] || null;
    },
    /** 测试可以自行补元素（例如注入一个 #log-tail 之外的节点）。 */
    _setElement: function (id, tagName) {
      var el = makeElement(id, tagName);
      elements[id] = el;
      return el;
    },
    createElement: function (tagName) {
      return makeElement(null, tagName);
    },
    addEventListener: function (type, fn) {
      listeners["document:" + type] = listeners["document:" + type] || [];
      listeners["document:" + type].push(fn);
    },
    execCommand: function () {
      return true;
    },
    /**
     * 模拟点击某个按钮：调用 addEventListener 注册的处理器。
     * 这一步会真正触发 UI 里的 renderStatus / handshakeCopyText 等代码。
     */
    _click: function (id) {
      var list = listeners[id + ":click"] || [];
      for (var i = 0; i < list.length; i++) {
        list[i]({ type: "click", target: elements[id] });
      }
      return list.length;
    }
  };

  return documentRef;
}

/**
 * 伪造的 window：只提供 UI 用到的 addEventListener / location / navigator。
 * 注意这里**故意不提供 __adobe_cep__**，用来验证"非 CEP 环境"分支。
 */
function createFakeWindow(options) {
  options = options || {};
  var listeners = {};
  return {
    __adobe_cep__: options.adobeCep,
    CSInterface: options.CSInterface,
    location: { reload: function () {}, href: "file:///cep-extension/index.html" },
    navigator: {},
    addEventListener: function (type, fn) {
      listeners[type] = listeners[type] || [];
      listeners[type].push(fn);
    },
    removeEventListener: function () {},
    _listeners: listeners,
    _fire: function (type) {
      var list = listeners[type] || [];
      for (var i = 0; i < list.length; i++) {
        list[i]({ type: type });
      }
      return list.length;
    }
  };
}

/** 构造一个合法的成功响应 JSON 字符串（模拟 jsx/wrap.jsx 的输出）。 */
function makeHostResponse(overrides) {
  var base = {
    id: null,
    ok: true,
    data: { ok_field: true },
    error: null,
    host: { app_version: "25.0.0", project_path: "D:\\p.prproj", dirty: false, has_project: true },
    warnings: [],
    timing: { host_ms: 12 }
  };
  if (overrides) {
    for (var key in overrides) {
      if (Object.prototype.hasOwnProperty.call(overrides, key)) {
        base[key] = overrides[key];
      }
    }
  }
  return JSON.stringify(base);
}

module.exports = {
  PANEL_ROOT: PANEL_ROOT,
  JS_DIR: JS_DIR,
  createReport: createReport,
  createTempEnv: createTempEnv,
  startTestPanel: startTestPanel,
  connectClient: connectClient,
  makeHostResponse: makeHostResponse,
  extractDispatchPayload: extractDispatchPayload,
  unquoteJsStringLiteral: unquoteJsStringLiteral,
  deepContains: deepContains,
  createFakeDom: createFakeDom,
  createFakeWindow: createFakeWindow,
  delay: delay,
  withTimeout: withTimeout,
  fakeCep: fakeCep
};
