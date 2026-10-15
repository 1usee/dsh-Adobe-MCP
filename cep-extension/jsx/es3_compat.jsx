// =====================================================================
// es3_compat.jsx —— ECMAScript 3 缺失能力补齐（说明书 §6.1）
//
// ExtendScript 是 ES3 方言：`Array.prototype.map/filter/indexOf/forEach`、
// `String.prototype.trim`、`Object.keys`、`Date.now` 在不同宿主版本上时有时无
// （有的版本补了一部分 ES5）。这里逐项做**特性检测**，缺什么补什么，
// 已存在的一律不覆盖——覆盖宿主原生实现只会制造难查的差异。
//
// 载入顺序：json2.jsx 之后、wrap.jsx 之前。本文件不依赖任何 DSH 成员。
// =====================================================================

if (typeof DSH === "undefined") { var DSH = {}; }

(function () {
    var arrayProto = Array.prototype;
    var stringProto = String.prototype;

    function define(target, name, implementation) {
        if (typeof target[name] !== "function") {
            target[name] = implementation;
        }
    }

    // ---- Array.prototype ----------------------------------------------

    define(arrayProto, "forEach", function (callback, thisArg) {
        if (typeof callback !== "function") {
            throw { name: "TypeError", message: "Array.prototype.forEach: 回调不是函数" };
        }
        var i;
        for (i = 0; i < this.length; i += 1) {
            if (Object.prototype.hasOwnProperty.call(this, i)) {
                callback.call(thisArg, this[i], i, this);
            }
        }
    });

    define(arrayProto, "map", function (callback, thisArg) {
        if (typeof callback !== "function") {
            throw { name: "TypeError", message: "Array.prototype.map: 回调不是函数" };
        }
        var out = [];
        var i;
        for (i = 0; i < this.length; i += 1) {
            if (Object.prototype.hasOwnProperty.call(this, i)) {
                out[i] = callback.call(thisArg, this[i], i, this);
            }
        }
        return out;
    });

    define(arrayProto, "filter", function (callback, thisArg) {
        if (typeof callback !== "function") {
            throw { name: "TypeError", message: "Array.prototype.filter: 回调不是函数" };
        }
        var out = [];
        var i;
        for (i = 0; i < this.length; i += 1) {
            if (Object.prototype.hasOwnProperty.call(this, i)) {
                if (callback.call(thisArg, this[i], i, this)) {
                    out[out.length] = this[i];
                }
            }
        }
        return out;
    });

    define(arrayProto, "indexOf", function (searchElement, fromIndex) {
        var length = this.length;
        var start = (typeof fromIndex === "number") ? fromIndex : 0;
        var i;
        if (start < 0) { start = length + start; }
        if (start < 0) { start = 0; }
        for (i = start; i < length; i += 1) {
            if (Object.prototype.hasOwnProperty.call(this, i) && this[i] === searchElement) {
                return i;
            }
        }
        return -1;
    });

    // 契约 §5 把 "Array.prototype.trim" 列在补齐清单里。ES3/ES5 都没有数组 trim
    // 语义，这里按"去掉数组首尾的空槽（undefined）”定义，仅为满足契约完整性；
    // 业务代码不应依赖它（真要清空槽请显式写循环）。
    define(arrayProto, "trim", function () {
        var start = 0;
        var end = this.length;
        var out = [];
        var i;
        while (start < end && typeof this[start] === "undefined") { start += 1; }
        while (end > start && typeof this[end - 1] === "undefined") { end -= 1; }
        for (i = start; i < end; i += 1) { out[out.length] = this[i]; }
        return out;
    });

    // ---- String.prototype ---------------------------------------------

    define(stringProto, "trim", function () {
        return String(this).replace(/^[\s\u00a0]+/, "").replace(/[\s\u00a0]+$/, "");
    });

    if (typeof stringProto.trimLeft !== "function" && typeof stringProto.trimStart !== "function") {
        stringProto.trimLeft = function () { return String(this).replace(/^[\s\u00a0]+/, ""); };
    }
    if (typeof stringProto.trimRight !== "function" && typeof stringProto.trimEnd !== "function") {
        stringProto.trimRight = function () { return String(this).replace(/[\s\u00a0]+$/, ""); };
    }

    // ---- Object / Date ------------------------------------------------

    if (typeof Object.keys !== "function") {
        Object.keys = function (object) {
            var out = [];
            var key;
            if (object === null || typeof object === "undefined") {
                throw { name: "TypeError", message: "Object.keys: 目标不是对象" };
            }
            for (key in object) {
                if (Object.prototype.hasOwnProperty.call(object, key)) {
                    out[out.length] = key;
                }
            }
            return out;
        };
    }

    if (typeof Date.now !== "function") {
        Date.now = function () { return new Date().getTime(); };
    }
}());
