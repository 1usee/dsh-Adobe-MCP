"use strict";
/*
 * =====================================================================
 * cep-extension/js/auth.js
 * ---------------------------------------------------------------------
 * 握手令牌的生成与校验。契约 §4：
 *   createToken() → 随机 hex 字符串（长度 ≥ 32 个十六进制字符）
 *   verifyToken(expected, actual) → 常量时间比较
 *
 * 安全要求（说明书 §5.1、§11）：
 *   - 令牌每次面板启动刷新，只写握手文件，**绝不写入任何日志**。
 *   - 比较使用 crypto.timingSafeEqual（常量时间），不写短路比较。
 *   - 本模块只处理令牌，不读取、不判断、不干预宿主授权状态。
 * =====================================================================
 */

var crypto = require("crypto");

// 契约 §2 要求长度 ≥ 32 个十六进制字符；这里用 32 字节 = 64 个 hex 字符。
var TOKEN_BYTES = 32;

/** 生成一个新的随机令牌（64 个十六进制字符）。 */
function createToken() {
  return crypto.randomBytes(TOKEN_BYTES).toString("hex");
}

/**
 * 常量时间比较两个令牌。
 *
 * 为什么不用 `a === b`：字符串比较会在第一个不同的字符处短路返回，攻击者可以
 * 通过测量响应时间逐字节猜出令牌。timingSafeEqual 要求两个 Buffer 等长，
 * 因此先用定长摘要（SHA-256）把任意长度的输入归一到 32 字节，再比较摘要。
 * 这样即使长度不同也不会提前返回，且比较时间与输入内容无关。
 *
 * @param {string} expected 握手文件里记录的令牌
 * @param {string} actual   连接上送来的令牌
 * @returns {boolean}
 */
function verifyToken(expected, actual) {
  if (typeof expected !== "string" || typeof actual !== "string") {
    return false;
  }
  if (expected.length === 0) {
    // 没有令牌就不可能认证成功（防止"空令牌被当作匹配"）。
    return false;
  }
  var a = digestToken(expected);
  var b = digestToken(actual);
  try {
    return crypto.timingSafeEqual(a, b);
  } catch (err) {
    return false;
  }
}

function digestToken(token) {
  return crypto.createHash("sha256").update(String(token), "utf8").digest();
}

/**
 * 令牌的脱敏指纹，用于排障时确认"两端看到的是不是同一个令牌"，
 * 而不泄漏令牌本身。日志只允许出现这个值。
 */
function tokenFingerprint(token) {
  if (typeof token !== "string" || token.length === 0) {
    return "<empty>";
  }
  return digestToken(token).toString("hex").slice(0, 8);
}

module.exports = {
  TOKEN_BYTES: TOKEN_BYTES,
  createToken: createToken,
  verifyToken: verifyToken,
  tokenFingerprint: tokenFingerprint
};
