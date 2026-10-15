"use strict";
/*
 * =====================================================================
 * tests/l3/panel/fake_cep.js
 * ---------------------------------------------------------------------
 * 伪 CEP 环境：提供 `window.__adobe_cep__` 的替身、可脚本化返回值的
 * CSInterface 替身，以及一个**调用顺序记录器**。
 *
 * 设计要点：
 *   1. 不模拟宿主 DOM（不需要），只模拟面板真正依赖的三个能力：
 *      evalScript / getHostEnvironment / addEventListener。
 *   2. evalScript 的返回值按队列脚本化：每次调用取队首。队列空则**报错**，
 *      避免测试"少喂了返回值却被当成成功"。
 *   3. 记录每次 evalScript 的**调用序号**、脚本文本与当时的在途计数，
 *      用来断言"串行化"（契约 §4.1 要求一个在途）。
 *   4. 回调可以延迟触发（deferMs / manual），用来验证：
 *      - 第二个请求必须等第一个回调完成才会发出（串行化）；
 *      - 面板在待回调期间 inflight 必须为 1。
 * =====================================================================
 */

/**
 * 伪 __adobe_cep__ 原生桥。真实 CEP 里这个名字的对象一定存在，
 * 面板的 csinterface-shim 会用它的方法按官方 CSInterface 的名字重新暴露。
 *
 * 注意：本替身**不做业务**，evalScript 一律回 "EvalScript error."。
 * 需要脚本化返回值的测试请用 createFakeCsInterface。
 */
function createFakeAdobeCep(options) {
  options = options || {};
  var env = options.hostEnvironment || {
    appName: "PPRO",
    appVersion: "25.0.0",
    appLocale: "zh_CN",
    appUILocale: "zh_CN",
    appId: "PPRO",
    isAppOnline: false,
    cepVersion: "11.0.0"
  };
  var calls = [];
  var listeners = {};

  return {
    evalScript: function (script, callback) {
      calls.push(String(script));
      if (typeof callback === "function") {
        callback("EvalScript error.");
      }
      return "EvalScript error.";
    },
    getHostEnvironment: function () {
      return JSON.stringify(env);
    },
    getSystemPath: function (pathType) {
      if (pathType === "extension") {
        return "C:/Users/x/AppData/Roaming/Adobe/CEP/extensions/com.dsh.adobe.bridge/";
      }
      return "";
    },
    getOSInformation: function () {
      return "Windows 10";
    },
    addEventListener: function (type, listener) {
      if (!listeners[type]) {
        listeners[type] = [];
      }
      listeners[type].push(listener);
    },
    removeEventListener: function (type, listener) {
      var list = listeners[type] || [];
      for (var i = list.length - 1; i >= 0; i--) {
        if (list[i] === listener) {
          list.splice(i, 1);
        }
      }
    },
    dispatchEvent: function (event) {
      var list = listeners[event && event.type ? String(event.type) : ""] || [];
      for (var i = 0; i < list.length; i++) {
        list[i](event);
      }
    },
    closeExtension: function () {},
    _calls: calls,
    _listeners: listeners
  };
}

/**
 * 伪 CSInterface。返回值脚本化。
 *
 * @param {object} options
 *   results        string[]  按顺序返回的 evalScript 结果
 *   defaultResult  队列空时的返回值（不设则抛错）
 *   deferMs        number>0 时每次回调延迟多少毫秒触发（测串行化用）
 *   autoCallback   boolean   false 时不自动回调，由测试手动 flush（默认 true）
 *   hostEnvironment object   getHostEnvironment 返回的对象
 */
function createFakeCsInterface(options) {
  options = options || {};
  var queue = (options.results || []).slice();
  var defaultResult = options.hasOwnProperty("defaultResult") ? options.defaultResult : undefined;
  var deferMs = typeof options.deferMs === "number" ? options.deferMs : 0;
  var autoCallback = options.autoCallback !== false;

  var calls = []; // { index, script, at, inflightAtCall }
  var inflight = 0;
  var pending = []; // 手动模式下待触发的回调
  var maxInflight = 0;

  function nextResult() {
    if (queue.length > 0) {
      return queue.shift();
    }
    if (defaultResult !== undefined) {
      return defaultResult;
    }
    throw new Error(
      "fake CSInterface: evalScript 结果队列为空，且未设置 defaultResult。" +
        "请在测试里显式安排返回值（第 " + (calls.length + 1) + " 次调用）"
    );
  }

  var instance = {
    isCepEnvironment: true,
    source: "fake-csinterface",

    evalScript: function (script, callback) {
      inflight++;
      if (inflight > maxInflight) {
        maxInflight = inflight;
      }
      var record = {
        index: calls.length,
        script: String(script),
        at: Date.now(),
        inflightAtCall: inflight
      };
      calls.push(record);

      var result;
      try {
        result = nextResult();
      } catch (err) {
        inflight--;
        throw err;
      }
      if (typeof result === "function") {
        // 结果本身可以是函数：拿到 (script, record) 后自行决定返回什么，
        // 便于构造"依赖请求参数"的返回值。
        result = result(String(script), record);
      }

      function deliver() {
        inflight--;
        if (typeof callback === "function") {
          callback(result);
        }
      }

      if (!autoCallback) {
        pending.push(deliver);
        return undefined;
      }
      if (deferMs > 0) {
        setTimeout(deliver, deferMs);
      } else {
        deliver();
      }
      return undefined;
    },

    getHostEnvironment: function () {
      return JSON.stringify(
        options.hostEnvironment || {
          appName: "PPRO",
          appVersion: "25.0.0",
          appLocale: "zh_CN",
          appUILocale: "zh_CN",
          appId: "PPRO",
          isAppOnline: false,
          cepVersion: "11.0.0"
        }
      );
    },

    getSystemPath: function (pathType) {
      return pathType === "extension" ? "cep-extension/" : "";
    },

    getOSInformation: function () {
      return "Windows 10";
    },

    addEventListener: function (type, listener) {
      instance._events = instance._events || {};
      instance._events[type] = instance._events[type] || [];
      instance._events[type].push(listener);
    },

    removeEventListener: function () {},

    dispatchEvent: function (event) {
      var list = (instance._events && instance._events[event && event.type ? event.type : ""]) || [];
      for (var i = 0; i < list.length; i++) {
        list[i](event);
      }
    },

    closeExtension: function () {
      instance._closed = true;
    },

    /* -------- 测试用控制面 -------- */

    /** 全部 evalScript 调用记录（按顺序）。 */
    calls: function () {
      return calls.slice();
    },
    callCount: function () {
      return calls.length;
    },
    /** 第 n 次调用的脚本文本（0 基）。 */
    scriptAt: function (index) {
      return calls[index] ? calls[index].script : null;
    },
    lastScript: function () {
      return calls.length ? calls[calls.length - 1].script : null;
    },
    /** 追加一个脚本化返回值（排在队尾）。 */
    pushResult: function (result) {
      queue.push(result);
    },
    pendingCount: function () {
      return queue.length;
    },
    /** 当前在途的 evalScript 数（未回调完的）。 */
    inflight: function () {
      return inflight;
    },
    /** 观测到的最大在途数：串行化正确时必须恒为 1。 */
    maxInflight: function () {
      return maxInflight;
    },
    /** 手动模式下触发全部待回调。 */
    flushPending: function () {
      var list = pending.slice();
      pending = [];
      for (var i = 0; i < list.length; i++) {
        list[i]();
      }
      return list.length;
    },
    /** 重置调用记录（不影响队列）。 */
    resetCalls: function () {
      calls = [];
      maxInflight = inflight;
    }
  };

  return instance;
}

module.exports = {
  createFakeAdobeCep: createFakeAdobeCep,
  createFakeCsInterface: createFakeCsInterface
};
