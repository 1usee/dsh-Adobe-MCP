"use strict";
/*
 * =====================================================================
 * cep-extension/js/logger.js
 * ---------------------------------------------------------------------
 * 面板日志。契约 §4：`log(level, message, extra)`；写
 * `logs/panel-<host>-<pid>.log` 行式日志，并把最后 N 行保留在内存里供诊断。
 *
 * 说明书 §11：
 *   - 面板日志位置 `logs/panel-<宿主>-<进程号>.log`，宿主异常时是唯一线索；
 *   - 日志脱敏：握手令牌、用户名等敏感信息写入前脱敏。
 *
 * 本模块的脱敏是**双保险**：
 *   1. 对象/数组按已知敏感键名递归替换为 "<redacted>"；
 *   2. 字符串内容再做一次"疑似长 hex 令牌"的模式替换。
 * 任何日志出口都经过这两步，因此即使调用方不小心把令牌塞进 extra，
 * 也不会以明文落盘。
 *
 * 写文件用 appendFileSync：行短、量小（每请求几行），同步写能保证宿主
 * 崩溃时日志不丢，这正是 §11 说"宿主异常时是唯一线索"的前提。
 *
 * 语法约束：Node 12 可用语法（详见 js/vendor/ws/index.js 头部说明）。
 * =====================================================================
 */

var fs = require("fs");
var os = require("os");
var path = require("path");

var LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };

// 与令牌无关的保险：任何 32 位以上纯 hex 串都当作疑似令牌脱敏。
// 有意只匹配"整段独立出现"的情况，避免把 nodeId 之类的短 hex 误伤。
var SUSPICIOUS_HEX = /\b[0-9a-fA-F]{32,}\b/g;
var SENSITIVE_KEY = /token|secret|password|passwd|credential|authorization|apikey|api_key/i;

var MAX_MESSAGE_CHARS = 2000;

/*
 * 令牌防线（说明书 §11）：调用方若把当前令牌注册进 logger，则**任何**日志出口
 * 都会把该字符串整体替换掉，无论它出现在消息、extra 的键名还是值里。
 * 这是比"按可疑键名替换"更强的一道保险，因为令牌落到日志里通常正是因为
 * 调用方没有意识到自己在打印什么。
 */
var registeredSecrets = [];

function registerSecret(secret) {
  if (typeof secret === "string" && secret.length >= 8 && registeredSecrets.indexOf(secret) === -1) {
    registeredSecrets.push(secret);
  }
}

function scrubSecrets(text) {
  var out = text;
  for (var i = 0; i < registeredSecrets.length; i++) {
    var secret = registeredSecrets[i];
    if (secret && out.indexOf(secret) !== -1) {
      // split/join 而不是 replace，避免密钥里的正则元字符被当成模式。
      out = out.split(secret).join("<redacted:secret>");
    }
  }
  return out;
}

function pad2(n) {
  return n < 10 ? "0" + n : String(n);
}

function timestamp() {
  var d = new Date();
  return (
    d.getFullYear() +
    "-" + pad2(d.getMonth() + 1) +
    "-" + pad2(d.getDate()) +
    "T" + pad2(d.getHours()) +
    ":" + pad2(d.getMinutes()) +
    ":" + pad2(d.getSeconds()) +
    "." + String(d.getMilliseconds() + 1000).slice(1)
  );
}

/** 递归脱敏。深度有上限，避免环状对象导致栈溢出。 */
function redact(value, depth) {
  depth = depth || 0;
  if (depth > 6) {
    return "<max-depth>";
  }
  if (value === null || value === undefined) {
    return value;
  }
  if (typeof value === "string") {
    var s = value.length > MAX_MESSAGE_CHARS ? value.slice(0, MAX_MESSAGE_CHARS) + "…<truncated>" : value;
    return scrubSecrets(s).replace(SUSPICIOUS_HEX, "<redacted:hex>");
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "function") {
    return "<function>";
  }
  if (value instanceof Error) {
    return {
      name: value.name,
      message: redact(String(value.message || ""), depth + 1),
      code: value.code || undefined
    };
  }
  if (Array.isArray(value)) {
    var arr = [];
    var limit = Math.min(value.length, 50);
    for (var i = 0; i < limit; i++) {
      arr.push(redact(value[i], depth + 1));
    }
    if (value.length > limit) {
      arr.push("<+" + (value.length - limit) + " more>");
    }
    return arr;
  }
  if (typeof value === "object") {
    var out = {};
    var keys = Object.keys(value);
    for (var k = 0; k < keys.length; k++) {
      var key = keys[k];
      if (SENSITIVE_KEY.test(key)) {
        out[key] = "<redacted>";
      } else {
        out[key] = redact(value[key], depth + 1);
      }
    }
    return out;
  }
  return String(value);
}

/**
 * @param {object} options
 *   host         宿主标识（PPRO 等），用于文件名
 *   logDir       日志目录（缺省取 config.log_dir）
 *   level        级别阈值
 *   tailLines    内存保留行数
 *   toConsole    是否同时写控制台
 *   pid          进程号（测试可覆盖）
 *   nowMs        取时间戳的函数（测试可覆盖，便于断言稳定）
 */
function createLogger(options) {
  options = options || {};
  var host = options.host || "UNKNOWN";
  var pid = options.pid || process.pid;
  var logDir = options.logDir || path.join(os.tmpdir(), "dsh-adobe-mcp", "logs");
  var level = LEVELS[options.level] ? options.level : "info";
  var tailLines = typeof options.tailLines === "number" ? options.tailLines : 200;
  var toConsole = options.toConsole !== false;

  var fileName = options.fileName || "panel-" + host + "-" + pid + ".log";
  var filePath = path.join(logDir, fileName);
  var tail = [];
  var lineListeners = [];
  var fileReady = false;
  var fileError = null;
  var droppedLines = 0;

  function ensureDir() {
    if (fileReady) {
      return true;
    }
    if (fileError) {
      return false;
    }
    try {
      if (!fs.existsSync(logDir)) {
        fs.mkdirSync(logDir, { recursive: true });
      }
      fileReady = true;
      return true;
    } catch (err) {
      fileError = err;
      return false;
    }
  }

  function writeFile(line) {
    if (!ensureDir()) {
      // 日志目录不可写（例如 %LOCALAPPDATA% 被策略限制）：降级为仅内存 + 控制台，
      // 并且只提示一次，避免把日志系统变成刷屏源。
      if (droppedLines === 0 && toConsole) {
        console.error("[panel-logger] 无法写日志文件 " + filePath + ": " + (fileError && fileError.message));
      }
      droppedLines++;
      return;
    }
    try {
      fs.appendFileSync(filePath, line + os.EOL);
    } catch (err) {
      fileError = err;
      droppedLines++;
    }
  }

  function shouldLog(lvl) {
    var want = LEVELS[lvl] || LEVELS.info;
    var threshold = LEVELS[level] || LEVELS.info;
    return want >= threshold;
  }

  function pushTail(record) {
    tail.push(record);
    while (tail.length > tailLines) {
      tail.shift();
    }
  }

  function emitLine(record) {
    for (var i = 0; i < lineListeners.length; i++) {
      try {
        lineListeners[i](record);
      } catch (err) {
        // UI 监听器出错不得影响日志写入。
      }
    }
  }

  function log(lvl, message, extra) {
    var useLevel = LEVELS[lvl] ? lvl : "info";
    if (!shouldLog(useLevel)) {
      return null;
    }
    var record = {
      ts: timestamp(),
      t: Date.now(),
      level: useLevel,
      pid: pid,
      host: host,
      msg: scrubSecrets(redact(typeof message === "string" ? message : String(message)))
    };
    if (extra !== undefined && extra !== null) {
      record.extra = scrubSecrets(JSON.stringify(redact(extra)));
      // extra 以"已序列化字符串"存回，避免这里再做一次对象遍历；
      // 但对外暴露的 tail 仍然应是对象，所以解析回去。
      try {
        record.extra = JSON.parse(record.extra);
      } catch (err) {
        /* 理论不可达：redact 输出必然可序列化 */
      }
    }
    var line = scrubSecrets(JSON.stringify(record));
    writeFile(line);
    pushTail(record);
    emitLine(record);
    if (toConsole) {
      var consoleFn = useLevel === "error" ? console.error : useLevel === "warn" ? console.warn : console.log;
      if (typeof consoleFn === "function") {
        consoleFn("[panel:" + useLevel + "] " + record.msg);
      }
    }
    return record;
  }

  return {
    log: log,
    debug: function (message, extra) {
      return log("debug", message, extra);
    },
    info: function (message, extra) {
      return log("info", message, extra);
    },
    warn: function (message, extra) {
      return log("warn", message, extra);
    },
    error: function (message, extra) {
      return log("error", message, extra);
    },
    /** 内存里最后 N 行（UI 显示用）。 */
    tail: function (count) {
      var n = typeof count === "number" ? count : tail.length;
      return tail.slice(Math.max(0, tail.length - n));
    },
    /** 已写入磁盘的日志文件绝对路径。 */
    filePath: function () {
      return filePath;
    },
    logDir: function () {
      return logDir;
    },
    /** 运行期切换级别/目录（L1 下发 config 时调用）。 */
    setLevel: function (next) {
      if (LEVELS[next]) {
        level = next;
      }
    },
    getLevel: function () {
      return level;
    },
    setLogDir: function (nextDir) {
      if (typeof nextDir === "string" && nextDir && nextDir !== logDir) {
        logDir = nextDir;
        filePath = path.join(logDir, fileName);
        fileReady = false;
        fileError = null;
      }
    },
    /** 日志落盘是否正常（面板 UI 用它做健康提示）。 */
    health: function () {
      return { fileReady: fileReady, fileError: fileError ? String(fileError.message || fileError) : null, dropped: droppedLines };
    },
    /** 新增一行的回调，供面板控制台区域实时追加。返回取消函数。 */
    onLine: function (fn) {
      if (typeof fn !== "function") {
        return function () {};
      }
      lineListeners.push(fn);
      var self = this;
      return function off() {
        for (var i = lineListeners.length - 1; i >= 0; i--) {
          if (lineListeners[i] === fn) {
            lineListeners.splice(i, 1);
          }
        }
      };
    },
    /** 供测试断言：确认令牌明文没有以任何形式出现在已写日志里。 */
    _debugAllLines: function () {
      return tail.slice();
    },
    _redact: redact
  };
}

module.exports = {
  LEVELS: LEVELS,
  createLogger: createLogger,
  redact: redact,
  timestamp: timestamp,
  registerSecret: registerSecret,
  scrubSecrets: scrubSecrets,
  /** 供测试断言：当前已注册的敏感串数量（不暴露内容）。 */
  secretCount: function () {
    return registeredSecrets.length;
  },
  /** 仅供测试清场。 */
  _clearSecrets: function () {
    registeredSecrets = [];
  }
};
