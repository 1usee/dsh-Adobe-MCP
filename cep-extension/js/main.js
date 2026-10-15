"use strict";
/*
 * =====================================================================
 * cep-extension/js/main.js
 * ---------------------------------------------------------------------
 * 面板引导。契约 §4 要求的动作序列：
 *   能力自检 → 启动 WS 服务 → 写握手文件 → 注册心跳 → 注册 CSInterface 事件
 *   → 应用 L1 下发的配置 → 退出时删除握手文件
 *
 * 说明书 §5.1：
 *   1. 绑定 127.0.0.1:0（动态端口）；
 *   2. 每次启动生成新令牌；
 *   3. 写 %TEMP%\dsh-adobe-mcp\handshake-<host>-<pid>.json（pid 必须是宿主进程号）；
 *   4. 面板退出（beforeunload / unload）时删除握手文件。
 *
 * 两种运行形态：
 *   - **CEP 宿主内**（真实部署）：window.__adobe_cep__ 存在，由 CSInterface 的
 *     evalScript 与 ExtendScript 通信；document 存在，UI 会被挂载。
 *   - **Node 下被测试驱动**（tests/l3/panel）：传入 fakeCsInterface，且没有 document。
 *     此时跳过全部 DOM 操作，仍然完整走"令牌 → 服务端 → 分派 → 握手文件"这条
 *     主链路，因此测试覆盖的是**同一份代码**，不是平行实现。
 * =====================================================================
 */

var fs = require("fs");
var os = require("os");
var path = require("path");

var configModule = require(path.join(__dirname, "config.js"));
var loggerModule = require(path.join(__dirname, "logger.js"));
var authModule = require(path.join(__dirname, "auth.js"));
var dispatchModule = require(path.join(__dirname, "dispatch.js"));
var wsServerModule = require(path.join(__dirname, "ws-server.js"));
// 注意：CSInterface 的降级实现放在 lib/ 而不是 js/（index.html 的 <script> 也指向
// lib/，这样部署时用官方 CSInterface.js 覆盖 lib/ 即可，js/ 目录不受影响）。
var csinterfaceModule = require(path.join(__dirname, "..", "lib", "csinterface-shim.js"));

/* ------------------------------------------------------------------ */
/* 宿主探测                                                            */
/* ------------------------------------------------------------------ */

function detectHostId(csInterface) {
  try {
    if (csInterface && typeof csInterface.getHostEnvironment === "function") {
      var env = csInterface.getHostEnvironment();
      if (typeof env === "string") {
        env = JSON.parse(env);
      }
      if (env && env.appName) {
        // CEP 的 appName 形如 "PPRO" / "AEFT" / "AME"，正好是宿主标识。
        return String(env.appName);
      }
    }
  } catch (err) {
    /* 降级到默认 */
  }
  return "PPRO";
}

function detectHostVersion(csInterface) {
  try {
    if (csInterface && typeof csInterface.getHostEnvironment === "function") {
      var env = csInterface.getHostEnvironment();
      if (typeof env === "string") {
        env = JSON.parse(env);
      }
      if (env && env.appVersion) {
        return String(env.appVersion);
      }
    }
  } catch (err) {
    /* 宿主版本探测失败不应阻断启动 */
  }
  return null;
}

function detectCepVersion(csInterface) {
  try {
    if (csInterface && typeof csInterface.getHostEnvironment === "function") {
      var env = csInterface.getHostEnvironment();
      if (typeof env === "string") {
        env = JSON.parse(env);
      }
      if (env) {
        // CSInterface.getHostEnvironment() 在 CEP 9+ 返回含 appVersion 的对象；
        // 这里优先取显式的 CSXS 版本字段，取不到就回退到应用版本，避免编造。
        if (env.cepVersion) {
          return String(env.cepVersion);
        }
        if (env.CSXSVersion) {
          return String(env.CSXSVersion);
        }
      }
    }
  } catch (err) {
    /* 忽略 */
  }
  return null;
}

/* ------------------------------------------------------------------ */
/* 握手文件                                                            */
/* ------------------------------------------------------------------ */

function handshakeFileName(host, pid) {
  return "handshake-" + host + "-" + pid + ".json";
}

/**
 * 写握手文件。字段严格按契约 §2。
 * 权限：Windows 上尽力而为（用 0o600 打开，POSIX 上生效；NTFS 由继承 ACL 决定）。
 */
function writeHandshakeFile(dir, payload, logger) {
  var filePath = path.join(dir, handshakeFileName(payload.host, payload.pid));
  try {
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    var text = JSON.stringify(payload, null, 2);
    fs.writeFileSync(filePath, text, { encoding: "utf8", mode: 0o600 });
    try {
      // 尽力收紧权限；Windows 上该调用可能无效或被忽略，属预期。
      fs.chmodSync(filePath, 0o600);
    } catch (err) {
      /* Windows 上不保证生效，忽略 */
    }
    return { ok: true, path: filePath };
  } catch (err) {
    if (logger) {
      logger.error("握手文件写入失败，L1 将无法发现面板", {
        dir: dir,
        message: err && err.message ? err.message : String(err)
      });
    }
    return { ok: false, path: filePath, error: err };
  }
}

function removeHandshakeFile(filePath, logger) {
  if (!filePath) {
    return false;
  }
  try {
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
      if (logger) {
        logger.info("握手文件已删除", { path: filePath });
      }
    }
    return true;
  } catch (err) {
    if (logger) {
      logger.warn("握手文件删除失败", { path: filePath, message: err && err.message ? err.message : String(err) });
    }
    return false;
  }
}

/* ------------------------------------------------------------------ */
/* 面板引导                                                            */
/* ------------------------------------------------------------------ */

/**
 * 启动面板。可被测试直接调用。
 *
 * @param {object} options
 *   csInterface   CSInterface 实例（缺省自动解析：window.__adobe_cep__ → shim）
 *   documentRef   document 对象（缺省全局 document；无 DOM 时传 null）
 *   windowRef     window 对象
 *   logger        logger 实例（缺省自建）
 *   handshakeDir  覆盖握手目录（测试用临时目录）
 *   logDir        覆盖日志目录（测试用）
 *   host          覆盖宿主标识（测试用）
 *   port          覆盖监听端口（缺省 0 = 动态）
 * @returns {Promise<object>} panel 句柄
 */
function startPanel(options) {
  options = options || {};

  var windowRef = options.windowRef || (typeof window !== "undefined" ? window : null);
  var documentRef = options.documentRef !== undefined ? options.documentRef : (typeof document !== "undefined" ? document : null);

  // --- 1. CSInterface 解析：真实宿主优先，shim 兜底 --------------------
  var resolved = csinterfaceModule.resolveCsInterface({
    windowRef: windowRef,
    csInterface: options.csInterface,
    realCtor: options.realCtor
  });
  var csInterface = resolved.csInterface;
  var hostId = options.host || detectHostId(csInterface);
  var hostVersion = detectHostVersion(csInterface);
  var cepVersion = detectCepVersion(csInterface);

  // --- 2. 配置与日志 ------------------------------------------------
  var cfg = configModule.getConfig();
  var logDir = options.logDir || cfg.log_dir;
  var logger =
    options.logger ||
    loggerModule.createLogger({
      host: hostId,
      pid: process.pid,
      logDir: logDir,
      level: cfg.log_level,
      tailLines: cfg.log_tail_lines,
      toConsole: cfg.log_to_console
    });

  // --- 3. 能力自检 --------------------------------------------------
  var ability = {
    csinterface_available: !!(csInterface && typeof csInterface.evalScript === "function"),
    cep_environment: resolved.isCepEnvironment === true,
    csinterface_source: resolved.source
  };
  logger.info("面板启动", {
    host: hostId,
    host_version: hostVersion,
    panel_version: configModule.PANEL_VERSION,
    pid: process.pid,
    node_version: process.versions && process.versions.node ? process.versions.node : null,
    csinterface_source: ability.csinterface_source,
    cep_environment: ability.cep_environment
  });
  if (!ability.csinterface_available) {
    logger.warn("CSInterface.evalScript 不可用：面板只能响应 node 执行器命令（host.ping / host.capabilities / panel.reload）", {
      source: ability.csinterface_source
    });
  }

  // --- 4. 令牌 ------------------------------------------------------
  var token = options.token || authModule.createToken();
  logger.debug("已生成握手令牌", {
    fingerprint: authModule.tokenFingerprint(token),
    length: token.length
  });

  // --- 5. 分发器 ----------------------------------------------------
  var panelInfo = {
    panelVersion: configModule.PANEL_VERSION,
    contractSchemaVersion: configModule.CONTRACT_SCHEMA_VERSION,
    nodeCommands: Object.keys(dispatchModule.NODE_COMMANDS),
    runtime: {
      cepVersion: cepVersion,
      mixedContext: ability.cep_environment,
      hostAppId: hostId,
      csinterfaceAvailable: ability.csinterface_available,
      cepEnvironment: ability.cep_environment
    }
  };

  var ui = null; // 延迟创建：enableUi 时才建，避免无 DOM 时构造失败

  var dispatcher = dispatchModule.createDispatcher({
    csInterface: csInterface,
    logger: logger,
    config: configModule,
    panel: panelInfo,
    reload: function () {
      if (windowRef && windowRef.location && typeof windowRef.location.reload === "function") {
        windowRef.location.reload();
      }
    },
    onResponse: function (cmd, envelope, request) {
      if (ui) {
        ui.onResponse(cmd, envelope, request);
      }
    }
  });

  // 让模块级 dispatchCommand 走同一份依赖（契约 §4 的入口名）。
  dispatchModule.setRuntime({
    csInterface: csInterface,
    logger: logger,
    config: configModule,
    panel: panelInfo,
    reload: function () {
      if (windowRef && windowRef.location && typeof windowRef.location.reload === "function") {
        windowRef.location.reload();
      }
    }
  });

  // --- 6. WS 服务端 --------------------------------------------------
  var bridge = wsServerModule.createBridgeServer({
    token: token,
    config: configModule,
    logger: logger,
    panel: panelInfo,
    inflightProvider: function () {
      return dispatcher.inflightCount();
    },
    host: "127.0.0.1",
    port: typeof options.port === "number" ? options.port : 0,
    onRequest: function (cmd, args, meta) {
      return dispatcher.dispatchCommand(cmd, args, meta);
    }
  });

  var panel = {
    host: hostId,
    hostVersion: hostVersion,
    panelVersion: configModule.PANEL_VERSION,
    pid: process.pid,
    token: token,
    logger: logger,
    config: configModule,
    dispatcher: dispatcher,
    bridge: bridge,
    csInterface: csInterface,
    ability: ability,
    handshakePath: null,
    startedAt: Date.now(),
    stopped: false
  };

  return bridge
    .ready()
    .then(function (port) {
      // --- 7. 握手文件 ---------------------------------------------
      var handshakeDir = options.handshakeDir || configModule.getConfigValue("handshake_dir", os.tmpdir());
      var payload = {
        protocol: configModule.PROTOCOL_VERSION,
        port: port,
        token: token,
        host: hostId,
        pid: process.pid,
        host_version: hostVersion,
        panel_version: configModule.PANEL_VERSION,
        contract_schema_version: configModule.CONTRACT_SCHEMA_VERSION,
        started_at: Date.now() / 1000
      };
      var written = writeHandshakeFile(handshakeDir, payload, logger);
      panel.handshakePath = written.ok ? written.path : null;
      panel.handshakePayload = payload;

      // --- 8. CSInterface 事件 -------------------------------------
      if (csInterface && typeof csInterface.addEventListener === "function") {
        try {
          csInterface.addEventListener("com.adobe.csxs.events.ThemeColorChanged", function (event) {
            logger.debug("收到 CEP 事件", { type: event && event.type ? event.type : null });
            if (ui) {
              ui.onHostEvent(event);
            }
          });
        } catch (err) {
          logger.warn("注册 CSInterface 事件失败（不影响桥接）", {
            message: err && err.message ? err.message : String(err)
          });
        }
      }

      // --- 9. 退出清理 ---------------------------------------------
      registerCleanup(panel, windowRef, documentRef);

      // --- 10. UI --------------------------------------------------
      if (documentRef) {
        try {
          ui = createPanelUi({ panel: panel, documentRef: documentRef, windowRef: windowRef, logger: logger });
          ui.mount();
        } catch (err) {
          logger.warn("面板 UI 挂载失败（桥接不受影响）", {
            message: err && err.message ? err.message : String(err)
          });
          ui = null;
        }
      }

      logger.info("面板已就绪", {
        port: port,
        handshake: panel.handshakePath,
        csinterface_available: ability.csinterface_available
      });
      return panel;
    })
    .catch(function (err) {
      logger.error("面板启动失败", { message: err && err.message ? err.message : String(err) });
      throw err;
    });
}

/**
 * 面板退出时删除握手文件（契约 §2.5）。
 * 同时挂 process 级兜底：CEP 面板被宿主强制卸载时 unload 事件可能来不及跑，
 * 但宿主进程存活时 process 尚在，能在 exit 上清理。
 *
 * 实现细节（这里踩过一个坑，值得写清楚）：
 *   CEP 的 Node 是**长驻进程**，而"重载面板"会让面板的 JS 环境重建——等于
 *   本模块被重新求值一次，模块级变量全部归零。若把"已注册 exit 监听器"的标记
 *   放在模块作用域，每次重载都会再注册一个 (process, "exit") 监听器，
 *   重载十几次后就会触发 MaxListenersExceededWarning。
 *   （本项目的 tests/l3/panel/run.js 连开 11 个面板，正是这样把问题暴露出来的：
 *   测试脚手架每个用例都会清 require 缓存以隔离 config/logger 的全局状态，
 *   这与真实宿主里"重载面板"的语义一致。）
 *
 *   因此标记与面板列表都存在 **global** 上：无论本模块被求值多少次，
 *   监听器只注册一次、活跃面板集合也始终是同一个。
 */
function getExitRegistry() {
  var KEY = "__DSH_ADOBE_PANEL_EXIT_REGISTRY__";
  var g = typeof globalThis !== "undefined" ? globalThis : typeof global !== "undefined" ? global : null;
  if (!g) {
    // 极端环境没有 global 对象：退化为模块级（至少本次求值内是正确的）。
    if (!moduleExitFallback) {
      moduleExitFallback = { installed: false, panels: [] };
    }
    return moduleExitFallback;
  }
  if (!g[KEY]) {
    g[KEY] = { installed: false, panels: [] };
  }
  return g[KEY];
}

var moduleExitFallback = null;

function installProcessExitHook() {
  var registry = getExitRegistry();
  if (registry.installed) {
    return;
  }
  registry.installed = true;
  try {
    // 用 once 而不是 on：exit 只会触发一次，once 能保证即使被重复安装也最多
    // 留下一份"已消费"的监听器（与上面的 registry 守卫构成双保险）。
    process.once("exit", function () {
      // 只做同步清理（unlinkSync），exit 阶段不能有异步操作。
      var panels = getExitRegistry().panels;
      for (var i = 0; i < panels.length; i++) {
        try {
          panels[i].stop();
        } catch (err) {
          /* 退出阶段忽略一切异常 */
        }
      }
    });
  } catch (err) {
    registry.installed = false;
  }
}

function registerCleanup(panel, windowRef, documentRef) {
  function stop() {
    if (panel.stopped) {
      return;
    }
    panel.stopped = true;
    var panels = getExitRegistry().panels;
    for (var i = panels.length - 1; i >= 0; i--) {
      if (panels[i] === panel) {
        panels.splice(i, 1);
      }
    }
    removeHandshakeFile(panel.handshakePath, panel.logger);
    try {
      panel.bridge.close(function () {});
    } catch (err) {
      /* 已关闭 */
    }
    panel.logger.info("面板已停止", {});
  }
  panel.stop = stop;
  getExitRegistry().panels.push(panel);
  installProcessExitHook();

  try {
    if (windowRef && typeof windowRef.addEventListener === "function") {
      windowRef.addEventListener("beforeunload", stop);
      windowRef.addEventListener("unload", stop);
    }
  } catch (err) {
    /* 忽略 */
  }
  try {
    if (documentRef && typeof documentRef.addEventListener === "function") {
      documentRef.addEventListener("beforeunload", stop);
      documentRef.addEventListener("unload", stop);
    }
  } catch (err) {
    /* 忽略 */
  }
}

/* ------------------------------------------------------------------ */
/* 面板 UI（仅在有 document 时创建）                                   */
/* ------------------------------------------------------------------ */

function createPanelUi(deps) {
  var panel = deps.panel;
  var doc = deps.documentRef;
  var logger = deps.logger;
  var lastLogs = [];

  function byId(id) {
    return doc.getElementById ? doc.getElementById(id) : null;
  }

  function setText(id, text) {
    var el = byId(id);
    if (el) {
      el.textContent = String(text);
    }
  }

  function renderStatus() {
    var status = panel.bridge.status();
    setText("conn-state", status.connections.length === 0 ? "等待 L1 连接" : panel.bridge.authenticatedCount() + " 个已认证连接");
    setText("conn-port", status.port);
    setText("conn-host", panel.host + (panel.hostVersion ? " " + panel.hostVersion : ""));
    setText("panel-version", panel.panelVersion + " / 契约 v" + configModule.CONTRACT_SCHEMA_VERSION);
    setText("eval-state", panel.ability.csinterface_available ? "可用" : "不可用（仅 node 命令）");
    setText("handshake-state", panel.handshakePath || "未写入");
    setText("uptime", Math.round(status.uptime_ms / 1000) + " s");
    var stats = panel.dispatcher.stats();
    setText("stats", "请求 " + stats.total + " / 成功 " + stats.succeeded + " / 失败 " + stats.failed + " / 在途 " + stats.inflight);
  }

  function renderLogs() {
    var el = byId("log-tail");
    if (!el) {
      return;
    }
    var lines = logger.tail(60).map(function (rec) {
      return rec.ts + " [" + rec.level + "] " + rec.msg;
    });
    lastLogs = lines;
    el.textContent = lines.join("\n");
    if (el.scrollTop !== undefined) {
      el.scrollTop = el.scrollHeight;
    }
  }

  function copyText(text) {
    if (deps.windowRef && deps.windowRef.navigator && deps.windowRef.navigator.clipboard) {
      return deps.windowRef.navigator.clipboard.writeText(text);
    }
    // CEP 的 CEF 版本可能没有 clipboard API，退化为 textarea + execCommand。
    var area = doc.createElement("textarea");
    area.value = text;
    if (doc.body) {
      doc.body.appendChild(area);
    }
    area.select();
    try {
      if (doc.execCommand) {
        doc.execCommand("copy");
      }
    } catch (err) {
      /* 忽略 */
    }
    if (area.parentNode) {
      area.parentNode.removeChild(area);
    }
    return null;
  }

  /** 排障用的握手信息。**含令牌**，因此只允许复制到剪贴板，绝不写日志。 */
  function handshakeCopyText() {
    var payload = panel.handshakePayload || {};
    return JSON.stringify({
      protocol: payload.protocol,
      port: payload.port,
      token: payload.token,
      host: payload.host,
      pid: payload.pid,
      host_version: payload.host_version,
      panel_version: payload.panel_version,
      contract_schema_version: payload.contract_schema_version,
      started_at: payload.started_at,
      handshake_file: panel.handshakePath,
      log_file: logger.filePath(),
      csinterface_source: panel.ability.csinterface_source,
      cep_environment: panel.ability.cep_environment
    }, null, 2);
  }

  function bind(id, event, handler) {
    var el = byId(id);
    if (el && typeof el.addEventListener === "function") {
      el.addEventListener(event, handler);
    }
  }

  return {
    mount: function () {
      bind("btn-copy-handshake", "click", function () {
        var text = handshakeCopyText();
        copyText(text);
        setText("copy-state", "已复制（含令牌，请勿粘贴进日志/工单）");
      });
      bind("btn-refresh", "click", renderStatus);
      bind("btn-open-logdir", "click", function () {
        setText("log-path", logger.filePath());
      });
      setText("log-path", logger.filePath());
      if (!panel.ability.cep_environment) {
        var banner = byId("env-banner");
        if (banner) {
          banner.textContent = "非 CEP 环境：面板在普通浏览器中打开，ExtendScript 不可用，仅显示状态。";
          banner.style.display = "block";
        }
      }
      renderStatus();
      renderLogs();
      logger.onLine(function () {
        renderLogs();
      });
      // 状态区每 2 秒刷新一次（连接数、请求计数、运行时长）。
      var timer = setInterval(function () {
        if (panel.stopped) {
          clearInterval(timer);
          return;
        }
        renderStatus();
      }, 2000);
      if (timer && typeof timer.unref === "function") {
        timer.unref();
      }
    },
    onResponse: function (cmd, envelope) {
      setText("last-cmd", cmd + " → " + (envelope.ok ? "ok" : "失败 " + (envelope.error ? envelope.error.code : "?")));
      renderStatus();
    },
    onHostEvent: function (event) {
      logger.debug("宿主事件", { type: event && event.type ? event.type : null });
    },
    renderStatus: renderStatus,
    handshakeCopyText: handshakeCopyText
  };
}

/* ------------------------------------------------------------------ */
/* 自动引导（真实 CEP 环境）                                           */
/* ------------------------------------------------------------------ */

var autoStarted = false;

function autoStart() {
  if (autoStarted) {
    return;
  }
  var windowRef = typeof window !== "undefined" ? window : null;
  var documentRef = typeof document !== "undefined" ? document : null;
  if (!documentRef) {
    // 无 DOM（例如被 Node 直接 require）：不自动启动，由调用方显式 startPanel()。
    return;
  }
  autoStarted = true;
  var run = function () {
    startPanel({ windowRef: windowRef, documentRef: documentRef }).catch(function (err) {
      // 启动失败必须显示在面板上：此时日志可能还没建好，只能直接改 DOM。
      try {
        var el = documentRef.getElementById("boot-error");
        if (el) {
          el.style.display = "block";
          el.textContent = "面板启动失败：" + (err && err.message ? err.message : String(err));
        }
      } catch (uiErr) {
        /* 忽略 */
      }
    });
  };
  if (documentRef.readyState === "loading") {
    documentRef.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
}

module.exports = {
  PANEL_VERSION: configModule.PANEL_VERSION,
  startPanel: startPanel,
  autoStart: autoStart,
  writeHandshakeFile: writeHandshakeFile,
  removeHandshakeFile: removeHandshakeFile,
  handshakeFileName: handshakeFileName,
  detectHostId: detectHostId,
  createPanelUi: createPanelUi
};

// 在真实 CEP 面板里 index.html 只加载本文件，因此这里自举。
autoStart();
