"use strict";
/*
 * tests/l3/jsx/harness.js —— L3 ExtendScript 逻辑自测的运行时装配
 *
 * 为什么这样做：ExtendScript 的 `#include` 语义就是**文本拼接**（没有模块系统），
 * 所以"把 dispatch.jsx 的 include 递归展开成一段源码，在带伪宿主对象的作用域里
 * 求值"与宿主内的真实载入顺序完全一致。这样就能在 Node 里直接调 DSH.dispatch(...)
 * 断言行为，而不需要真机。
 *
 * 这里刻意不模拟 ExtendScript 引擎本身，只提供契约要求的宿主全局
 * （app / $ / Time / File / Folder / qe）——它们由 fake_premiere.js 提供。
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const JSX_DIR = path.resolve(__dirname, "..", "..", "..", "cep-extension", "jsx");

/** 递归展开 `#include "x.jsx"`（相对被包含文件所在目录），返回拼接后的源码。 */
function expandIncludes(entryFile, included) {
  const absolute = path.resolve(entryFile);
  const files = included || [];
  if (files.indexOf(absolute) >= 0) {
    throw new Error("include 环：" + absolute);
  }
  files.push(absolute);
  const text = fs.readFileSync(absolute, "utf8");
  const dir = path.dirname(absolute);
  const lines = [];
  text.split(/\r?\n/).forEach((line) => {
    const match = /^\s*#include\s+["']([^"']+)["']\s*$/.exec(line);
    if (match) {
      lines.push(expandIncludes(path.resolve(dir, match[1]), files));
      return;
    }
    if (/^\s*#/.test(line)) {
      // 其它 ExtendScript 预处理指令（#target 等）在拼接时丢弃
      return;
    }
    lines.push(line);
  });
  return lines.join("\n");
}

/** 展开 dispatch.jsx 并报告参与拼接的文件清单。 */
function buildBundle() {
  const included = [];
  const source = expandIncludes(path.join(JSX_DIR, "dispatch.jsx"), included);
  return { source, files: included };
}

/**
 * 建一个宿主环境：伪宿主对象 + 展开后的 jsx 源码，在 vm 里求值。
 * 返回的 call() 就是面板侧 `CSInterface.evalScript("DSH.dispatch(...)")` 的等价物。
 */
function createRuntime(fake, options) {
  const opts = options || {};
  const bundle = opts.bundle || buildBundle();
  const sandbox = Object.assign({}, fake.globals);

  const context = vm.createContext(sandbox);
  // ExtendScript 的 $.global 指向全局对象：dispatch.jsx 用它解析处理器
  vm.runInContext(
    "if (typeof $ === 'undefined') { var $ = {}; } $.global = this;",
    context,
    { filename: "dsh-bootstrap.js" }
  );
  vm.runInContext(bundle.source, context, { filename: "dsh-jsx-bundle.js" });

  const dsh = vm.runInContext("DSH", context);
  let sequence = 0;

  function call(cmd, args, meta) {
    sequence += 1;
    const request = {
      id: (meta && meta.id) || "req-" + sequence,
      cmd: cmd,
      args: args || {},
      meta: Object.assign(
        { traceId: "trace-" + sequence, dryRun: false, idempotencyKey: null },
        meta || {}
      )
    };
    const raw = dsh.dispatch(JSON.stringify(request));
    return { request: request, raw: raw, response: JSON.parse(raw) };
  }

  return {
    context: context,
    sandbox: sandbox,
    dsh: dsh,
    fake: fake,
    bundleFiles: bundle.files,
    call: call,
    evalInContext: function (code) {
      return vm.runInContext(code, context);
    }
  };
}

module.exports = {
  JSX_DIR: JSX_DIR,
  buildBundle: buildBundle,
  expandIncludes: expandIncludes,
  createRuntime: createRuntime
};
