// =====================================================================
// json2.jsx —— ES3 JSON 兼容层（说明书 §6.1：ExtendScript 无原生 JSON 可用）
//
// 为什么自己实现：ExtendScript 基于 ECMAScript 3，老版本宿主没有原生 JSON；
// 桥接协议要求"ExtendScript 侧的每一帧都是 JSON 字符串"，所以序列化/反序列化
// 是本层的地基。它必须**在任何其它模块之前**载入（dispatch.jsx 的 include 顺序）。
//
// 设计约束：
//   * 纯 ES3：不用 map/filter/indexOf/trim（本文件先于 es3_compat.jsx 载入，
//     不能在加载期或调用期假设补齐层已就绪）。
//   * 输出**纯 ASCII**：所有非可打印字符与 U+007F..U+FFFF 一律转义为 \uXXXX。
//     这样返回值可以安全地穿过 CSInterface.evalScript 的字符串通道，
//     不会因为宿主/控制台编码差异而损坏中文等字符。
//   * 不使用原生 JSON（即使宿主提供），保证行为在所有版本一致、可测。
//
// 对外接口（契约 §5）：DSH.json.parse(text) / DSH.json.stringify(value)
// =====================================================================

if (typeof DSH === "undefined") { var DSH = {}; }
if (typeof DSH.json === "undefined") { DSH.json = {}; }

(function () {
    var json = DSH.json;

    // ---- 工具 ---------------------------------------------------------

    function isArray(value) {
        return Object.prototype.toString.apply(value) === "[object Array]";
    }

    function hasOwn(obj, key) {
        return Object.prototype.hasOwnProperty.call(obj, key);
    }

    function hex4(code) {
        var digits = "0123456789abcdef";
        var out = "";
        out += digits.charAt((code >> 12) & 15);
        out += digits.charAt((code >> 8) & 15);
        out += digits.charAt((code >> 4) & 15);
        out += digits.charAt(code & 15);
        return out;
    }

    function quoteString(text) {
        var out = "\"";
        var i;
        var ch;
        var code;
        for (i = 0; i < text.length; i += 1) {
            ch = text.charAt(i);
            code = text.charCodeAt(i);
            if (ch === "\"") {
                out += "\\\"";
            } else if (ch === "\\") {
                out += "\\\\";
            } else if (code === 8) {
                out += "\\b";
            } else if (code === 9) {
                out += "\\t";
            } else if (code === 10) {
                out += "\\n";
            } else if (code === 12) {
                out += "\\f";
            } else if (code === 13) {
                out += "\\r";
            } else if (code < 32 || code > 126) {
                out += "\\u" + hex4(code);
            } else {
                out += ch;
            }
        }
        return out + "\"";
    }

    function numberToString(value) {
        // NaN / Infinity 在 JSON 里没有表示，按 null 处理（与 JSON.stringify 一致）。
        if (typeof value !== "number" || !isFinite(value)) { return "null"; }
        return String(value);
    }

    // ---- 序列化 -------------------------------------------------------

    function serialize(value, depth, seen) {
        var type = typeof value;
        var out;
        var key;
        var item;
        var first;
        var i;

        if (value === null) { return "null"; }
        if (type === "string") { return quoteString(value); }
        if (type === "number") { return numberToString(value); }
        if (type === "boolean") { return value ? "true" : "false"; }
        if (type === "undefined" || type === "function" || type === "unknown") { return undefined; }
        if (type === "xml") { return quoteString(value.toString()); }

        if (depth > 64) {
            throw { name: "TypeError", message: "DSH.json.stringify: 嵌套超过 64 层，疑似循环引用" };
        }
        for (i = 0; i < seen.length; i += 1) {
            if (seen[i] === value) {
                throw { name: "TypeError", message: "DSH.json.stringify: 检测到循环引用" };
            }
        }
        seen[seen.length] = value;

        var tag = Object.prototype.toString.apply(value);

        if (isArray(value)) {
            out = "[";
            for (i = 0; i < value.length; i += 1) {
                if (i > 0) { out += ","; }
                item = serialize(value[i], depth + 1, seen);
                out += (typeof item === "undefined") ? "null" : item;
            }
            out += "]";
        } else if (tag === "[object Date]") {
            out = quoteString(dateToIso(value));
        } else if (tag === "[object Object]") {
            // 与 JSON.stringify 一致：普通对象若提供 toJSON()，优先使用它。
            if (typeof value.toJSON === "function") {
                item = serialize(value.toJSON(), depth + 1, seen);
                out = (typeof item === "undefined") ? "null" : item;
            } else {
                out = "{";
                first = true;
                for (key in value) {
                    if (!hasOwn(value, key)) { continue; }
                    item = serialize(value[key], depth + 1, seen);
                    if (typeof item === "undefined") { continue; }
                    if (!first) { out += ","; }
                    first = false;
                    out += quoteString(key) + ":" + item;
                }
                out += "}";
            }
        } else {
            // 既不是数组/日期/普通对象：宿主 DOM 对象（ProjectItem、Time 等）。
            // 序列化它是编程错误——统一降级为字符串，绝不让循环引用炸掉整条响应。
            out = quoteString(String(value));
        }

        seen.length = seen.length - 1;
        return out;
    }

    function pad2(value) {
        return (value < 10 ? "0" : "") + String(value);
    }

    function dateToIso(date) {
        return String(date.getUTCFullYear()) + "-" + pad2(date.getUTCMonth() + 1) + "-"
            + pad2(date.getUTCDate()) + "T" + pad2(date.getUTCHours()) + ":"
            + pad2(date.getUTCMinutes()) + ":" + pad2(date.getUTCSeconds()) + "Z";
    }

    json.stringify = function (value) {
        var out = serialize(value, 0, []);
        return (typeof out === "undefined") ? "null" : out;
    };

    // ---- 反序列化 -----------------------------------------------------

    function parseError(message, at, text) {
        return {
            name: "SyntaxError",
            message: "DSH.json.parse: " + message + "（位置 " + at + "）",
            at: at,
            text: String(text).substring(0, 200)
        };
    }

    json.parse = function (text) {
        var source = String(text);
        var at = 0;
        var ch = " ";

        function nextChar(expect) {
            if (expect && expect !== ch) {
                throw parseError("期望 \"" + expect + "\"，实际是 \"" + ch + "\"", at - 1, source);
            }
            ch = source.charAt(at);
            at += 1;
            return ch;
        }

        function skipWhite() {
            while (ch !== "" && ch <= " ") { nextChar(); }
        }

        function parseString() {
            var out = "";
            var escape;
            var hex;
            var i;
            if (ch !== "\"") { throw parseError("字符串必须以双引号开始", at - 1, source); }
            while (nextChar()) {
                if (ch === "\"") {
                    nextChar();
                    return out;
                }
                if (ch === "\\") {
                    nextChar();
                    if (ch === "u") {
                        hex = 0;
                        for (i = 0; i < 4; i += 1) {
                            nextChar();
                            escape = ch;
                            if (escape >= "0" && escape <= "9") {
                                hex = hex * 16 + (escape.charCodeAt(0) - 48);
                            } else if (escape >= "a" && escape <= "f") {
                                hex = hex * 16 + (escape.charCodeAt(0) - 87);
                            } else if (escape >= "A" && escape <= "F") {
                                hex = hex * 16 + (escape.charCodeAt(0) - 55);
                            } else {
                                throw parseError("\\u 后面必须是 4 位十六进制", at - 1, source);
                            }
                        }
                        out += String.fromCharCode(hex);
                    } else if (ch === "b") { out += "\b"; }
                    else if (ch === "f") { out += "\f"; }
                    else if (ch === "n") { out += "\n"; }
                    else if (ch === "r") { out += "\r"; }
                    else if (ch === "t") { out += "\t"; }
                    else if (ch === "\"" || ch === "\\" || ch === "/") { out += ch; }
                    else { throw parseError("未知转义 \\" + ch, at - 1, source); }
                } else {
                    if (ch.charCodeAt(0) < 32) {
                        throw parseError("字符串里出现未转义的控制字符", at - 1, source);
                    }
                    out += ch;
                }
            }
            throw parseError("字符串没有闭合", at, source);
        }

        function parseNumber() {
            var token = "";
            var code;
            while (ch !== "") {
                code = ch.charCodeAt(0);
                if (ch === "-" || ch === "+" || ch === "." ||
                        (code >= 48 && code <= 57) || ch === "e" || ch === "E") {
                    token += ch;
                    nextChar();
                } else {
                    break;
                }
            }
            var value = Number(token);
            if (!isFinite(value) || token === "" || token === "-") {
                throw parseError("非法数字 \"" + token + "\"", at - token.length - 1, source);
            }
            return value;
        }

        function parseWord() {
            if (ch === "t") {
                if (nextChar() === "r" && nextChar() === "u" && nextChar() === "e") {
                    nextChar();
                    return true;
                }
                throw parseError("非法字面量（期望 true）", at, source);
            }
            if (ch === "f") {
                if (nextChar() === "a" && nextChar() === "l" && nextChar() === "s" && nextChar() === "e") {
                    nextChar();
                    return false;
                }
                throw parseError("非法字面量（期望 false）", at, source);
            }
            if (ch === "n") {
                if (nextChar() === "u" && nextChar() === "l" && nextChar() === "l") {
                    nextChar();
                    return null;
                }
                throw parseError("非法字面量（期望 null）", at, source);
            }
            throw parseError("无法识别的字面量", at - 1, source);
        }

        function parseArray() {
            var out = [];
            nextChar("[");
            skipWhite();
            if (ch === "]") {
                nextChar();
                return out;
            }
            while (ch !== "") {
                out[out.length] = parseValue();
                skipWhite();
                if (ch === "]") {
                    nextChar();
                    return out;
                }
                if (ch !== ",") { throw parseError("数组元素之间缺少逗号", at - 1, source); }
                nextChar();
                skipWhite();
            }
            throw parseError("数组没有闭合", at, source);
        }

        function parseObject() {
            var out = {};
            var key;
            nextChar("{");
            skipWhite();
            if (ch === "}") {
                nextChar();
                return out;
            }
            while (ch !== "") {
                key = parseString();
                skipWhite();
                if (ch !== ":") { throw parseError("对象成员缺少冒号", at - 1, source); }
                nextChar();
                skipWhite();
                out[key] = parseValue();
                skipWhite();
                if (ch === "}") {
                    nextChar();
                    return out;
                }
                if (ch !== ",") { throw parseError("对象成员之间缺少逗号", at - 1, source); }
                nextChar();
                skipWhite();
            }
            throw parseError("对象没有闭合", at, source);
        }

        function parseValue() {
            skipWhite();
            if (ch === "{") { return parseObject(); }
            if (ch === "[") { return parseArray(); }
            if (ch === "\"") { return parseString(); }
            if (ch === "-" || (ch >= "0" && ch <= "9")) { return parseNumber(); }
            return parseWord();
        }

        if (source.length > 0 && source.charCodeAt(0) === 65279) {
            source = source.substring(1); // 去 BOM
        }
        if (source.length === 0) {
            throw parseError("输入为空", 0, source);
        }
        var result = parseValue();
        skipWhite();
        if (ch !== "") {
            throw parseError("JSON 结束后还有多余内容", at - 1, source);
        }
        return result;
    };
}());
