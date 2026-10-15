"use strict";
/*
 * =====================================================================
 * cep-extension/lib/csinterface-shim.js
 * ---------------------------------------------------------------------
 * ⚠️ 这是 CSInterface 的**降级实现**，不是官方库。
 *
 * 【为什么存在】
 *   CEP 官方在扩展里引用的是 `lib/CSInterface.js`（Adobe 官方库，随扩展分发）。
 *   本仓库在没有网络的环境下无法取得该文件的官方副本，因此提供一个接口子集的
 *   降级实现，保证：
 *     (a) 扩展目录**不缺文件**（index.html 里的 <script src> 不会 404）；
 *     (b) 面板被当作普通网页在浏览器里打开时，UI 仍然能渲染并显示"非 CEP 环境"，
 *         便于做可见性调试；
 *     (c) 在真实 CEP 宿主内，本文件**不承担**桥接职责。
 *
 * 【真实 CEP 环境的行为】
 *   在真实的 CEP 宿主里 `window.__adobe_cep__` **一定存在**（这是 CEP 注入的原生
 *   桥接对象）。因此 resolveCsInterface() 的优先级是：
 *     1. 调用方显式传入的 CSInterface（测试用）；
 *     2. window.CSInterface（若 index.html 加载了官方 CSInterface.js，或包了官方库）；
 *     3. 用 window.__adobe_cep__ 构造的本文件包装器（包装器只是把原生桥接对象
 *        的方法按官方 CSInterface 的名字重新暴露，逻辑仍在原生桥上）；
 *     4. 纯 shim（没有 __adobe_cep__，即"非 CEP 环境"）。
 *   也就是说：**只要在宿主里跑，就一定有 __adobe_cep__，shim 的假实现不会被使用**；
 *   它只在普通浏览器里生效，并会把 isCepEnvironment 标为 false，让 UI 显示提示。
 *
 * 【部署时如何换成官方库】
 *   把 Adobe 官方的 CSInterface.js 放到 `cep-extension/lib/CSInterface.js`，并在
 *   index.html 里优先加载它即可（index.html 已经先试官方、后加载本 shim）。
 *   官方库定义 `window.CSInterface`，第 2 条优先级会命中它，本文件随即退居备用。
 *   本文件与官方库的差异只在于：只实现了被本面板用到的方法子集，且不做版本探测。
 *
 * 【与授权无关】
 *   本文件不读取、不判断、不干预任何宿主授权/许可状态（说明书 §1.3）。
 * =====================================================================
 */

(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    // 浏览器/CEP 面板里 index.html 之外的脚本可能直接用 window.DshCsInterfaceShim。
    root.DshCsInterfaceShim = api;
  }
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this), function () {
  var EVAL_SCRIPT_ERROR = "EvalScript error.";

  /* ---------------- 内部工具 ---------------- */

  function normalizeEvalResult(raw, callback) {
    // 官方 CSInterface.evalScript 的回调只会收到字符串或 "EvalScript error."。
    // 这里做一层归一，保证 dispatch.js 拿到的形态稳定。
    var value = raw === undefined || raw === null ? "" : String(raw);
    if (typeof callback === "function") {
      callback(value);
    }
    return value;
  }

  /* ---------------- 用 __adobe_cep__ 构造的包装器 ---------------- */

  /**
   * 把 CEP 注入的原生桥接对象（window.__adobe_cep__）按官方 CSInterface 的
   * 名字重新暴露。**不做任何模拟**：所有调用都落到原生桥上。
   */
  function CepNativeBridge(native) {
    this._native = native;
    this._source = "window.__adobe_cep__";
  }

  CepNativeBridge.prototype.evalScript = function (script, callback) {
    var self = this;
    try {
      // 不同 CEP 版本原生桥的签名不同：多数版本是 (script, callback)。
      // 这里按 (script, callback) 调用，并对返回值做一次归一，
      // 使同步返回结果的实现（少数版本）也能工作。
      var returned = this._native.evalScript(String(script), function (result) {
        return normalizeEvalResult(result, callback);
      });
      if (returned !== undefined && returned !== null && returned !== self) {
        // 某些实现会同步 return 结果；此时回调可能不会被调用。
        if (returned !== "") {
          return normalizeEvalResult(returned, callback);
        }
      }
    } catch (err) {
      // 桥接失败时按官方库的约定回 "EvalScript error."，让上层转成结构化错误。
      return normalizeEvalResult(EVAL_SCRIPT_ERROR, callback);
    }
    return undefined;
  };

  CepNativeBridge.prototype.getHostEnvironment = function () {
    try {
      var raw = this._native.getHostEnvironment();
      return typeof raw === "string" ? raw : JSON.stringify(raw);
    } catch (err) {
      return "{}";
    }
  };

  CepNativeBridge.prototype.getSystemPath = function (pathType) {
    try {
      return this._native.getSystemPath(String(pathType));
    } catch (err) {
      return "";
    }
  };

  CepNativeBridge.prototype.addEventListener = function (type, listener) {
    try {
      this._native.addEventListener(String(type), listener);
    } catch (err) {
      /* 事件注册失败不影响桥接 */
    }
  };

  CepNativeBridge.prototype.removeEventListener = function (type, listener) {
    try {
      this._native.removeEventListener(String(type), listener);
    } catch (err) {
      /* 忽略 */
    }
  };

  CepNativeBridge.prototype.dispatchEvent = function (event) {
    try {
      this._native.dispatchEvent(event);
    } catch (err) {
      /* 忽略 */
    }
  };

  CepNativeBridge.prototype.getOSInformation = function () {
    try {
      return this._native.getOSInformation();
    } catch (err) {
      return "";
    }
  };

  CepNativeBridge.prototype.closeExtension = function () {
    try {
      if (this._native.closeExtension) {
        this._native.closeExtension();
      }
    } catch (err) {
      /* 忽略 */
    }
  };

  CepNativeBridge.prototype.isCepEnvironment = true;
  CepNativeBridge.prototype.source = "window.__adobe_cep__";

  /* ---------------- 纯 shim（非 CEP 环境） ---------------- */

  /**
   * 没有任何原生桥时的替身。evalScript 一律回 "EvalScript error."，
   * 因为"没有宿主"等价于"求值必然失败"——**不允许**假装成功返回数据，
   * 否则会把"面板在浏览器里被打开"伪装成"命令执行成功"。
   */
  function ShimCsInterface(options) {
    options = options || {};
    this._source = "shim(non-CEP)";
    this._evalScriptImpl = typeof options.evalScript === "function" ? options.evalScript : null;
    this._hostEnvironment = options.hostEnvironment || null;
    this._listeners = {};
    this._evalCallCount = 0;
  }

  ShimCsInterface.prototype.evalScript = function (script, callback) {
    this._evalCallCount++;
    if (this._evalScriptImpl) {
      // 仅供测试注入：真正的 CEP 面板永远不会走到这里（__adobe_cep__ 必然存在）。
      return this._evalScriptImpl(String(script), callback);
    }
    return normalizeEvalResult(EVAL_SCRIPT_ERROR, callback);
  };

  ShimCsInterface.prototype.evalCallCount = function () {
    return this._evalCallCount;
  };

  ShimCsInterface.prototype.getHostEnvironment = function () {
    if (this._hostEnvironment) {
      return typeof this._hostEnvironment === "string" ? this._hostEnvironment : JSON.stringify(this._hostEnvironment);
    }
    return JSON.stringify({
      appName: "PPRO",
      appVersion: null,
      appLocale: null,
      appUILocale: null,
      appId: null,
      isAppOnline: false,
      nonCepEnvironment: true
    });
  };

  ShimCsInterface.prototype.getSystemPath = function (pathType) {
    switch (String(pathType)) {
      case "extension":
        return "cep-extension/";
      case "hostApplication":
        return "";
      case "userData":
        return "";
      case "commonFiles":
        return "";
      case "myDocuments":
        return "";
      case "desktop":
        return "";
      case "appData":
        return "";
      default:
        return "";
    }
  };

  ShimCsInterface.prototype.getOSInformation = function () {
    if (typeof navigator !== "undefined" && navigator.userAgent) {
      return navigator.userAgent;
    }
    return "";
  };

  ShimCsInterface.prototype.addEventListener = function (type, listener) {
    var key = String(type);
    if (!this._listeners[key]) {
      this._listeners[key] = [];
    }
    this._listeners[key].push(listener);
  };

  ShimCsInterface.prototype.removeEventListener = function (type, listener) {
    var list = this._listeners[String(type)];
    if (!list) {
      return;
    }
    for (var i = list.length - 1; i >= 0; i--) {
      if (list[i] === listener) {
        list.splice(i, 1);
      }
    }
  };

  ShimCsInterface.prototype.dispatchEvent = function (event) {
    var list = this._listeners[event && event.type ? String(event.type) : ""] || [];
    for (var i = 0; i < list.length; i++) {
      try {
        list[i](event);
      } catch (err) {
        /* 忽略 */
      }
    }
  };

  ShimCsInterface.prototype.closeExtension = function () {
    // 非 CEP 环境下没有可关闭的扩展；只做标记，不抛错。
    this._closed = true;
  };

  ShimCsInterface.prototype.isCepEnvironment = false;
  ShimCsInterface.prototype.source = "shim(non-CEP)";

  /* ---------------- 解析 ---------------- */

  /**
   * 解析应当使用的 CSInterface。优先级见文件头注释。
   *
   * @param {object} options
   *   windowRef    浏览器 window（可空）
   *   csInterface  显式传入的实例（测试用，可选）
   *   realCtor     显式传入的官方 CSInterface 构造函数（可选）
   * @returns {{csInterface: object, source: string, isCepEnvironment: boolean}}
   */
  function resolveCsInterface(options) {
    options = options || {};
    var windowRef = options.windowRef || null;

    // 1. 显式传入（测试驱动）
    if (options.csInterface) {
      return {
        csInterface: options.csInterface,
        source: options.csInterface.source || "injected",
        isCepEnvironment: options.csInterface.isCepEnvironment === true
      };
    }

    if (!windowRef) {
      return { csInterface: new ShimCsInterface(), source: "shim(no-window)", isCepEnvironment: false };
    }

    // 2. 页面里已经存在官方 CSInterface（index.html 先试官方库）
    var Ctor = options.realCtor || windowRef.CSInterface;
    if (typeof Ctor === "function") {
      try {
        var instance = new Ctor();
        return {
          csInterface: instance,
          source: instance && instance.source ? instance.source : "window.CSInterface",
          // 官方库在有 __adobe_cep__ 时才是真 CEP 环境。
          isCepEnvironment: !!windowRef.__adobe_cep__
        };
      } catch (err) {
        /* 官方库构造失败则继续降级 */
      }
    }

    // 3. 用宿主注入的原生桥构造包装器（真实 CEP 环境必然命中这里）
    if (windowRef.__adobe_cep__) {
      return {
        csInterface: new CepNativeBridge(windowRef.__adobe_cep__),
        source: "window.__adobe_cep__",
        isCepEnvironment: true
      };
    }

    // 4. 非 CEP 环境：纯 shim，并把"非 CEP"如实上报给 UI。
    return { csInterface: new ShimCsInterface(), source: "shim(non-CEP)", isCepEnvironment: false };
  }

  return {
    EVAL_SCRIPT_ERROR: EVAL_SCRIPT_ERROR,
    CepNativeBridge: CepNativeBridge,
    ShimCsInterface: ShimCsInterface,
    resolveCsInterface: resolveCsInterface,
    /** 兼容官方库的构造名，便于 index.html 里统一写法。 */
    CSInterface: ShimCsInterface
  };
});
