"use strict";
/*
 * =====================================================================
 * cep-extension/js/config.js
 * ---------------------------------------------------------------------
 * 面板运行期配置。契约 §4：`setConfig(obj)` / `getConfig()`。
 *
 * 配置来源优先级（低 → 高）：
 *   内置默认值  <  manifest/宿主探测值  <  L1 通过认证帧下发的 config
 *
 * 说明：
 *   - 所有键都做白名单收口。L1 下发的未知键被忽略并记一条 warning，避免
 *     面板内部状态被任意键污染。
 *   - 数值键做范围钳制，防止配置把心跳周期设成 0 导致连接被反复重建。
 *   - 本文件**不**持有令牌，也不读取任何授权状态。
 *
 * 语法约束：宿主 CEP 自带的 Node 可能远老于开发机，本文件按 Node 12 可用语法
 * 书写（不用 `??=`、`.at()`、`Object.hasOwn`、可选链赋值等）。
 * =====================================================================
 */

var os = require("os");
var path = require("path");

var PANEL_VERSION = "1.1.0";
var CONTRACT_SCHEMA_VERSION = 1;
var PROTOCOL_VERSION = 1;

/** 默认面板日志目录（说明书 §10：log_dir 默认 %LOCALAPPDATA%\dsh-adobe-mcp\logs）。 */
function defaultLogDir() {
  var base = process.env.LOCALAPPDATA || process.env.APPDATA || "";
  if (!base) {
    base = path.join(os.homedir ? os.homedir() : os.tmpdir(), "AppData", "Local");
  }
  return path.join(base, "dsh-adobe-mcp", "logs");
}

/** 默认握手文件目录（说明书 §5.1：%TEMP%\dsh-adobe-mcp）。 */
function defaultHandshakeDir() {
  var tmp = process.env.TEMP || process.env.TMP || process.env.TMPDIR || os.tmpdir();
  return path.join(tmp, "dsh-adobe-mcp");
}

var DEFAULTS = {
  // 心跳间隔（秒），说明书 §5.4 默认 5。
  heartbeat_s: 5,
  // 连接建立后等待认证帧的时限（毫秒），契约 §3：3 秒。
  auth_timeout_ms: 3000,
  // 日志级别：debug / info / warn / error。
  log_level: "info",
  // 面板日志目录（L1 下发时覆盖）。
  log_dir: defaultLogDir(),
  // 握手文件目录（测试可覆盖）。
  handshake_dir: defaultHandshakeDir(),
  // QE 门禁（说明书 §6.2：默认关闭）。面板只上报，不做策略判断。
  qe_enabled: false,
  // evalScript 返回字符串长度上限（说明书 A-6 未实测，契约 §4.1 默认 100000）。
  eval_max_chars: 100000,
  // 日志内存保留行数（面板 UI 显示最近日志用）。
  log_tail_lines: 200,
  // 认证失败/协议违规后关闭连接的延迟（毫秒）：留出时间把最后一帧发出去。
  close_grace_ms: 60,
  // 是否把日志同时写到控制台（CEP 的 CEF 调试控制台）。
  log_to_console: true
};

var NUMERIC_BOUNDS = {
  heartbeat_s: [1, 300],
  auth_timeout_ms: [500, 60000],
  eval_max_chars: [1024, 50000000],
  log_tail_lines: [0, 5000],
  close_grace_ms: [0, 5000]
};

var ALLOWED_KEYS = Object.keys(DEFAULTS);

var state = {};
var warnings = [];
var listeners = [];

function hasOwn(obj, key) {
  return Object.prototype.hasOwnProperty.call(obj, key);
}

function resetState() {
  state = {};
  for (var i = 0; i < ALLOWED_KEYS.length; i++) {
    state[ALLOWED_KEYS[i]] = DEFAULTS[ALLOWED_KEYS[i]];
  }
  warnings = [];
}

resetState();

function clampNumber(key, value) {
  var bounds = NUMERIC_BOUNDS[key];
  var num = Number(value);
  if (!isFinite(num)) {
    return null;
  }
  if (bounds) {
    if (num < bounds[0]) {
      warnings.push("config." + key + " 低于下限 " + bounds[0] + "，已钳制");
      return bounds[0];
    }
    if (num > bounds[1]) {
      warnings.push("config." + key + " 高于上限 " + bounds[1] + "，已钳制");
      return bounds[1];
    }
  }
  return num;
}

/**
 * 合并一份配置。未知键被忽略（记 warning），已知键做类型/范围收口。
 * @param {object} patch
 * @returns {{applied: object, ignored: string[], warnings: string[]}}
 */
function setConfig(patch) {
  var applied = {};
  var ignored = [];
  if (!patch || typeof patch !== "object") {
    return { applied: applied, ignored: ignored, warnings: [] };
  }
  for (var key in patch) {
    if (!hasOwn(patch, key)) {
      continue;
    }
    if (!hasOwn(DEFAULTS, key)) {
      ignored.push(key);
      continue;
    }
    var raw = patch[key];
    var value = raw;
    if (hasOwn(NUMERIC_BOUNDS, key)) {
      var clamped = clampNumber(key, raw);
      if (clamped === null) {
        ignored.push(key);
        continue;
      }
      value = clamped;
    } else if (key === "qe_enabled" || key === "log_to_console") {
      value = raw === true || raw === "true" || raw === 1;
    } else if (typeof raw === "string") {
      value = raw;
    } else if (typeof raw === "number" || typeof raw === "boolean") {
      value = String(raw);
    } else {
      ignored.push(key);
      continue;
    }
    state[key] = value;
    applied[key] = value;
  }
  for (var i = 0; i < ignored.length; i++) {
    warnings.push("config 忽略了未知/非法键: " + ignored[i]);
  }
  notify(applied);
  return { applied: applied, ignored: ignored, warnings: warnings.slice() };
}

/** 取当前配置的**浅拷贝**（调用方改它不会影响内部状态）。 */
function getConfig() {
  var copy = {};
  for (var i = 0; i < ALLOWED_KEYS.length; i++) {
    copy[ALLOWED_KEYS[i]] = state[ALLOWED_KEYS[i]];
  }
  return copy;
}

function getConfigValue(key, fallback) {
  if (hasOwn(state, key)) {
    return state[key];
  }
  return fallback;
}

function setConfigValue(key, value) {
  var patch = {};
  patch[key] = value;
  return setConfig(patch);
}

/** 配置被修改时的回调（ws-server / main 用它热应用心跳周期等）。 */
function onConfigChange(fn) {
  if (typeof fn === "function") {
    listeners.push(fn);
  }
  return function off() {
    for (var i = listeners.length - 1; i >= 0; i--) {
      if (listeners[i] === fn) {
        listeners.splice(i, 1);
      }
    }
  };
}

function notify(applied) {
  for (var i = 0; i < listeners.length; i++) {
    try {
      listeners[i](getConfig(), applied);
    } catch (err) {
      // 监听器出错不得让配置应用失败。
      warnings.push("config listener error: " + (err && err.message ? err.message : String(err)));
    }
  }
}

function getWarnings() {
  return warnings.slice();
}

function clearWarnings() {
  warnings = [];
}

/**
 * 脱敏后的配置快照，供日志/UI 使用。
 * 这里额外加一层保险：任何看起来像密钥的键都不会出现在输出里。
 */
function describeConfig() {
  var cfg = getConfig();
  var out = {};
  for (var key in cfg) {
    if (!hasOwn(cfg, key)) {
      continue;
    }
    if (/token|secret|password/i.test(key)) {
      out[key] = "<redacted>";
    } else {
      out[key] = cfg[key];
    }
  }
  return out;
}

module.exports = {
  PANEL_VERSION: PANEL_VERSION,
  CONTRACT_SCHEMA_VERSION: CONTRACT_SCHEMA_VERSION,
  PROTOCOL_VERSION: PROTOCOL_VERSION,
  DEFAULTS: DEFAULTS,
  ALLOWED_KEYS: ALLOWED_KEYS,
  setConfig: setConfig,
  getConfig: getConfig,
  getConfigValue: getConfigValue,
  setConfigValue: setConfigValue,
  onConfigChange: onConfigChange,
  getWarnings: getWarnings,
  clearWarnings: clearWarnings,
  describeConfig: describeConfig,
  resetConfig: resetState
};
