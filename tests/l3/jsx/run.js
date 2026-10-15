"use strict";
/*
 * tests/l3/jsx/run.js —— L3（ExtendScript 侧）逻辑自测
 *
 * 运行：
 *   $env:ELECTRON_RUN_AS_NODE="1"
 *   & "F:\DeepseekHarness\DeepSeek Harness.exe" tests\l3\jsx\run.js
 *
 * 覆盖（对应任务书"必须自证的验证"第 3 条）：
 *   * DSH.json 往返（中文、引号、反斜杠、< >）
 *   * 未知命令 -> UNKNOWN_COMMAND；node 命令误发 -> WRONG_EXECUTOR；坏 JSON -> PROTOCOL_PARSE_ERROR
 *   * 处理器抛 DSH.fail -> 响应 ok:false 且含 code/remedy/command/script_excerpt
 *   * 处理器抛原生异常 -> 结构化 HOST_ERROR（不是崩栈）
 *   * dry-run 只读：伪宿主 DOM 与 mutation 计数都没变；且返回键与真实执行一致
 *   * QE 命令在 qeEnabled=false 时 -> QE_DISABLED；qeEnabled=true 时真的能切开片段
 *   * 幂等键重放：同键第二次 -> replayed:true，且伪宿主没有二次执行
 *   * DSH.parseRef / ticks / seconds / findSequence / findProjectItem / treePath 等工具函数行为
 *   * 45 个 jsx 处理器全部可解析（结构性检查）
 */

const fs = require("fs");
const path = require("path");
const os = require("os");

const harness = require("./harness");
const fakePremiere = require("./fake_premiere");

/* --------------------------------------------------------------- 测试骨架 */

const results = { pass: 0, fail: 0, failures: [] };

function test(name, fn) {
  try {
    fn();
    results.pass += 1;
    console.log("ok   - " + name);
  } catch (error) {
    results.fail += 1;
    results.failures.push({ name: name, error: error });
    const detail = (error && error.stack) ? error.stack.split("\n").slice(0, 4).join("\n       ") : String(error);
    console.log("FAIL - " + name + "\n       " + detail);
  }
}

function assertTrue(value, label) {
  if (value !== true) {
    throw new Error((label || "断言失败") + "：期望 true，实际 " + JSON.stringify(value));
  }
}

function assertFalse(value, label) {
  if (value !== false) {
    throw new Error((label || "断言失败") + "：期望 false，实际 " + JSON.stringify(value));
  }
}

function assertEqual(actual, expected, label) {
  if (actual !== expected) {
    throw new Error((label || "断言失败") + "：期望 " + JSON.stringify(expected)
      + "，实际 " + JSON.stringify(actual));
  }
}

function assertClose(actual, expected, tolerance, label) {
  if (typeof actual !== "number" || Math.abs(actual - expected) > tolerance) {
    throw new Error((label || "断言失败") + "：期望 ≈" + expected + "（容差 " + tolerance
      + "），实际 " + JSON.stringify(actual));
  }
}

function assertIncludes(haystack, needle, label) {
  if (typeof haystack !== "string" || haystack.indexOf(needle) < 0) {
    throw new Error((label || "断言失败") + "：期望包含 " + JSON.stringify(needle)
      + "，实际 " + JSON.stringify(haystack));
  }
}

function assertHasKeys(object, keys, label) {
  if (!object || typeof object !== "object") {
    throw new Error((label || "断言失败") + "：不是对象：" + JSON.stringify(object));
  }
  const missing = keys.filter((key) => !(key in object));
  if (missing.length) {
    throw new Error((label || "断言失败") + "：缺少键 " + JSON.stringify(missing)
      + "（实际键：" + JSON.stringify(Object.keys(object)) + "）");
  }
}

function keysOf(object) {
  return Object.keys(object || {}).sort();
}

function assertSameKeys(a, b, label) {
  const left = keysOf(a);
  const right = keysOf(b);
  if (left.join(",") !== right.join(",")) {
    throw new Error((label || "键集合不一致") + "：真实 " + JSON.stringify(left)
      + " vs dry-run " + JSON.stringify(right));
  }
}

/* ------------------------------------------------------------- 环境与夹具 */

const TMP_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "dsh-l3-jsx-"));
const PRESET_PATH = path.join(TMP_DIR, "preset.epr");
const OUTPUT_DIR = path.join(TMP_DIR, "out");
fs.writeFileSync(PRESET_PATH, "fake premiere preset\n");
fs.mkdirSync(OUTPUT_DIR, { recursive: true });
const OUTPUT_PATH = path.join(OUTPUT_DIR, "render.mp4");

/** 新建一套隔离环境：新伪宿主 + 新 vm 运行时 + 夹具工程。 */
function newEnv(options) {
  const opts = options || {};
  const fake = fakePremiere.createFakePremiere({ projectDir: TMP_DIR, jsxDir: harness.JSX_DIR });
  const fixture = fake.seed();
  const runtime = harness.createRuntime(fake);
  if (opts.qe === true) {
    runtime.dsh.setConfig({ qe_enabled: true });
  }
  return {
    fake: fake,
    fixture: fixture,
    runtime: runtime,
    dsh: runtime.dsh,
    call: runtime.call,
    mediaPreset: PRESET_PATH,
    outputPath: OUTPUT_PATH
  };
}

function ok(response, label) {
  if (response.ok !== true) {
    throw new Error((label || "期望成功")
      + "：ok=false code=" + (response.error && response.error.code)
      + " message=" + (response.error && response.error.message));
  }
  return response.data;
}

function failWith(response, code, label) {
  if (response.ok !== false) {
    throw new Error((label || "期望失败") + "：ok=true data=" + JSON.stringify(response.data));
  }
  assertEqual(response.error.code, code, (label || "错误码") + " code");
  return response.error;
}

/* ============================================================== 测试开始 */

console.log("== L3 ExtendScript 逻辑自测（" + harness.JSX_DIR + "）==");

/* --- 1. json 往返 ------------------------------------------------------- */

test("DSH.json 往返：中文/引号/反斜杠/尖括号/控制字符", () => {
  const env = newEnv();
  const payload = {
    zh: "中文测试：序列与素材箱",
    quotes: "他说 \"你好\" 与 '单引号'",
    backslash: "C:\\work\\media\\clip1.mp4",
    angle: "<tag> & </tag> > 1000",
    control: "换行\n制表\t回车\r结束",
    nested: { list: [1, 2.5, -3, true, false, null, "x"] },
    unicode_escape: "\\u4e2d",
    emojiFree: "★※→"
  };
  const text = env.dsh.json.stringify(payload);
  assertEqual(typeof text, "string", "stringify 返回字符串");
  // 输出必须是纯 ASCII：返回串要能安全穿过 evalScript 的字符串通道
  assertEqual(/^[\x20-\x7e]*$/.test(text), true, "输出为纯 ASCII");
  assertIncludes(text, "\\u4e2d\\u6587", "中文被转义为 \\uXXXX");
  const back = env.dsh.json.parse(text);
  assertEqual(back.zh, payload.zh, "中文还原");
  assertEqual(back.quotes, payload.quotes, "引号还原");
  assertEqual(back.backslash, payload.backslash, "反斜杠还原");
  assertEqual(back.angle, payload.angle, "尖括号还原");
  assertEqual(back.control, payload.control, "控制字符还原");
  assertEqual(back.nested.list.length, 7, "数组长度");
  assertEqual(back.nested.list[5], null, "null 还原");
  assertEqual(back.unicode_escape, payload.unicode_escape, "转义文本原样还原");
  assertEqual(env.dsh.json.stringify(undefined), "null", "undefined -> null");
  assertEqual(env.dsh.json.stringify({ a: undefined, b: 1 }), "{\"b\":1}", "对象里 undefined 被跳过");
  assertEqual(env.dsh.json.stringify([undefined, 1]), "[null,1]", "数组里 undefined -> null");
});

test("DSH.json.parse 报错路径：坏输入抛结构化错误（不是静默返回 null）", () => {
  const env = newEnv();
  let threw = false;
  try {
    env.dsh.json.parse("{\"a\":}");
  } catch (error) {
    threw = true;
    assertIncludes(String(error.message), "DSH.json.parse", "错误消息带来源");
  }
  assertTrue(threw, "坏 JSON 必须抛异常");
  threw = false;
  try {
    env.dsh.json.parse("");
  } catch (error) {
    threw = true;
  }
  assertTrue(threw, "空输入必须抛异常");
});

/* --- 2. 结构性：45 个处理器全部可解析 ---------------------------------- */

test("45 个 jsx 处理器全部能在全局作用域解析（$.global 查找）", () => {
  const env = newEnv();
  const routes = env.dsh.routes;
  const names = Object.keys(routes);
  const jsxRoutes = names.filter((name) => routes[name].handlerReady === true);
  const nodeRoutes = names.filter((name) => routes[name].handlerReady === false);
  assertEqual(jsxRoutes.length, 45, "jsx 命令数");
  assertEqual(nodeRoutes.length, 3, "node 命令数");
  const missing = jsxRoutes.filter((name) => typeof env.dsh.resolveHandler(routes[name].handler) !== "function");
  assertEqual(missing.length, 0, "未解析到处理器的命令：" + JSON.stringify(missing));
  assertEqual(env.dsh.contractSchemaVersion, 1, "契约 schema 版本");
  assertEqual(env.dsh.ticksPerSecond, 254016000000, "ticks_per_second");
});

/* --- 3. 路由与协议错误 -------------------------------------------------- */

test("未知命令 -> UNKNOWN_COMMAND（含 remedy）", () => {
  const env = newEnv();
  const { response } = env.call("no.such_command", {});
  const error = failWith(response, "UNKNOWN_COMMAND", "未知命令");
  assertIncludes(error.message, "no.such_command", "消息含命令名");
  assertTrue(typeof error.remedy === "string" && error.remedy.length > 0, "remedy 非空");
});

test("node 执行器的命令误发到 ExtendScript -> WRONG_EXECUTOR", () => {
  const env = newEnv();
  const { response } = env.call("host.ping", {});
  failWith(response, "WRONG_EXECUTOR", "node 命令");
});

test("请求 JSON 坏掉 -> PROTOCOL_PARSE_ERROR（不是崩栈）", () => {
  const env = newEnv();
  const raw = env.dsh.dispatch("{\"id\":\"x\",\"cmd\":");
  const response = JSON.parse(raw);
  assertEqual(response.ok, false, "ok=false");
  assertEqual(response.error.code, "PROTOCOL_PARSE_ERROR", "错误码");
  assertTrue(response.error.detail !== undefined, "带原始文本片段");
});

/* --- 4. 结构化错误 ------------------------------------------------------ */

test("处理器抛 DSH.fail -> ok:false 且含 code/remedy/command/script_excerpt/args_snapshot", () => {
  const env = newEnv();
  const args = { ref: "V:1:99" };
  const { response } = env.call("timeline.clip_info", args);
  const error = failWith(response, "NOT_FOUND", "不存在的片段 ref");
  assertTrue(typeof error.remedy === "string" && error.remedy.length > 0, "remedy 非空");
  assertEqual(error.command, "timeline.clip_info", "command 回填");
  assertEqual(error.args_snapshot.ref, "V:1:99", "args_snapshot 回填");
  assertEqual(error.line, 30, "行号取自 $.line");
  assertIncludes(String(error.file), "timeline.jsx", "出错文件名");
  assertIncludes(String(error.script_excerpt), "30: ", "script_excerpt 读到了出错行");
  assertIncludes(String(error.script_excerpt), "25: ", "script_excerpt 含 line-5");
  assertIncludes(String(error.script_excerpt), "35: ", "script_excerpt 含 line+5");
});

test("处理器抛原生异常 -> 结构化 HOST_ERROR（含原始消息，不是崩栈）", () => {
  const env = newEnv();
  env.runtime.evalInContext(
    "function test_native_throw(args, ctx) { var nothing = null; return nothing.boom; }"
  );
  env.dsh.routes["test.native_throw"] = {
    file: "test", handler: "test_native_throw", risk: "T0",
    dryRun: false, idempotent: true, capability: "public", handlerReady: true
  };
  const { response } = env.call("test.native_throw", { any: 1 });
  const error = failWith(response, "HOST_ERROR", "宿主异常");
  assertIncludes(error.message, "宿主 API 异常", "消息标明是宿主异常");
  assertTrue(typeof error.remedy === "string" && error.remedy.length > 0, "remedy 非空");
  assertEqual(response.host.has_project, true, "失败响应仍带宿主快照");
});

test("dry-run 中途前置条件失败 -> 错误里带回已收集的 checks", () => {
  const env = newEnv();
  const { response } = env.call("timeline.insert_clip",
    { item: "\\\\不存在.mp4", track_index: 1, at_s: 1 }, { dryRun: true });
  const error = failWith(response, "ITEM_NOT_FOUND", "dry-run 前置条件失败");
  assertTrue(Array.isArray(error.checks), "error.checks 是数组");
  const named = error.checks.filter((check) => check.name === "project_item_exists")[0];
  assertTrue(!!named, "含 project_item_exists 检查项（实际：" + JSON.stringify(error.checks) + "）");
  assertEqual(named.ok, false, "该检查项 ok=false");
  const sequenceCheck = error.checks.filter((check) => check.name === "sequence_found")[0];
  assertEqual(sequenceCheck.ok, true, "序列检查项在前面已通过");
});

/* --- 5. dry-run 只读 ---------------------------------------------------- */

test("dry-run 不触碰宿主 DOM：mutation 计数与全量快照都不变", () => {
  const env = newEnv();
  const cases = [
    ["timeline.insert_clip", { item: "\\\\demo.prproj\\Bin A\\clip2.mp4", track_type: "video", track_index: 1, at_s: 2 }],
    ["timeline.overwrite_clip", { item: "\\\\demo.prproj\\Bin A\\clip2.mp4", track_index: 1, at_s: 1 }],
    ["timeline.remove_clip", { ref: "V:1:0" }],
    ["timeline.move_clip", { ref: "V:1:0", to_s: 6 }],
    ["timeline.trim_clip", { ref: "V:1:0", start_s: 1 }],
    ["timeline.add_marker", { at_s: 3, name: "M1" }],
    ["project.bin_create", { name: "新箱" }],
    ["project.bin_move", { items: ["\\\\demo.prproj\\Bin A"], target_bin: "\\\\demo.prproj" }],
    ["project.import", { paths: [PRESET_PATH] }],
    ["sequence.set_in_out", { in_s: 1, out_s: 3 }],
    ["sequence.set_work_area", { start_s: 1, end_s: 3 }],
    ["app.set_playhead", { seconds: 2 }]
  ];
  cases.forEach(([cmd, args]) => {
    env.fake.resetMutations();
    const before = env.fake.snapshot();
    const { response } = env.call(cmd, args, { dryRun: true });
    if (response.ok !== true) {
      throw new Error(cmd + " dry-run 应当成功（只做前置校验），实际 code="
        + (response.error && response.error.code) + " message=" + (response.error && response.error.message));
    }
    assertEqual(response.data.dry_run, true, cmd + " 带 dry_run 标记");
    assertTrue(Array.isArray(response.data.actions), cmd + " 带 actions 数组");
    assertTrue(Array.isArray(response.data.checks), cmd + " 带 checks 数组");
    assertEqual(env.fake.state.mutations, 0, cmd + " 未产生任何宿主变更（mutation 计数）");
    assertEqual(env.fake.snapshot(), before, cmd + " 宿主全量快照未变");
  });

  // 需要 QE 的命令：门禁先于 dry-run，所以要在 QE 启用后再验证"dry-run 不改 DOM"
  const qeEnv = newEnv({ qe: true });
  [["timeline.razor", { at_s: 2.5 }], ["timeline.add_track", { track_type: "video" }]].forEach(([cmd, args]) => {
    qeEnv.fake.resetMutations();
    const before = qeEnv.fake.snapshot();
    const { response } = qeEnv.call(cmd, args, { dryRun: true });
    if (response.ok !== true) {
      throw new Error(cmd + "（QE 已启用）dry-run 应当成功，实际 code=" + (response.error && response.error.code));
    }
    assertEqual(response.data.dry_run, true, cmd + " 带 dry_run 标记");
    assertEqual(qeEnv.fake.state.mutations, 0, cmd + " dry-run 未触碰 DOM");
    assertEqual(qeEnv.fake.snapshot(), before, cmd + " 快照未变");
  });
});

test("dry-run 返回结构与真实执行一致（同样的业务键）", () => {
  const cases = [
    ["timeline.insert_clip", { item: "\\\\demo.prproj\\Bin A\\clip2.mp4", track_index: 1, at_s: 4 }],
    ["timeline.add_marker", { at_s: 1, name: "M", duration_s: 1 }],
    ["project.bin_create", { name: "箱X" }],
    ["app.set_playhead", { seconds: 1 }],
    ["timeline.remove_clip", { ref: "V:1:0" }],
    ["project.bin_move", { items: ["\\\\demo.prproj\\Bin A\\clip2.mp4"], target_bin: "\\\\demo.prproj" }]
  ];
  cases.forEach(([cmd, args]) => {
    const dryEnv = newEnv();
    const realEnv = newEnv();
    const dry = dryEnv.call(cmd, args, { dryRun: true }).response;
    const real = realEnv.call(cmd, args, {}).response;
    if (dry.ok !== true) {
      throw new Error(cmd + " dry-run 失败：code=" + (dry.error && dry.error.code));
    }
    if (real.ok !== true) {
      throw new Error(cmd + " 真实执行失败：code=" + (real.error && real.error.code)
        + " message=" + (real.error && real.error.message));
    }
    const dryBusiness = Object.assign({}, dry.data);
    delete dryBusiness.dry_run;
    delete dryBusiness.actions;
    delete dryBusiness.checks;
    assertSameKeys(dryBusiness, real.data, cmd + " 的 dry-run/真实键集合");
  });
});

/* --- 6. QE 护栏 -------------------------------------------------------- */

test("QE 命令在 qeEnabled=false 时 -> QE_DISABLED（且未触碰宿主）", () => {
  const env = newEnv();
  const before = env.fake.snapshot();
  const { response } = env.call("timeline.razor", { at_s: 2.5 });
  const error = failWith(response, "QE_DISABLED", "QE 未启用");
  assertIncludes(error.remedy, "enable_qe", "remedy 提示如何启用");
  assertEqual(response.qe_enabled, false, "响应标记 qe_enabled=false");
  assertEqual(env.fake.state.mutations, 0, "未产生宿主变更");
  assertEqual(env.fake.snapshot(), before, "快照未变");
});

test("public_or_qe 命令在公开路径可用时不走 QE（sequence.set_work_area）", () => {
  const env = newEnv();
  const data = ok(env.call("sequence.set_work_area", { start_s: 1, end_s: 4 }).response);
  assertEqual(data.path_used, "public", "走的是公开 DOM");
  assertClose(data.work_area_start_s, 1, 0.05, "工作区起点");
  assertClose(data.work_area_end_s, 4, 0.05, "工作区终点");
  assertEqual(data.verified, true, "读回校验");
});

test("QE 启用后 timeline.razor 真的切开片段（读回校验通过，mutation 记录 qe.razor）", () => {
  const env = newEnv({ qe: true });
  const before = env.fake.snapshot();
  const data = ok(env.call("timeline.razor", { at_s: 2.5, track_type: "video", track_index: 1 }).response);
  assertClose(data.razored_at_s, 2.5, 0.05, "切分时间");
  assertEqual(data.tracks[0], "V1", "作用轨道");
  assertTrue(env.fake.state.mutationLog.indexOf("qe.razor") >= 0, "确实走了 QE razor");
  assertTrue(env.fake.state.mutations > 0, "有宿主变更");
  assertTrue(env.fake.snapshot() !== before, "快照已改变");
  const clips = ok(env.call("timeline.list_clips", { track_type: "video", track_index: 1 }).response);
  assertEqual(clips.clips.length, 2, "切分后该轨道有 2 个片段");
});

test("QE 启用时响应带 QE 崩溃风险警告；QE 调用失败不影响已完成的公开操作", () => {
  const env = newEnv({ qe: true });
  const { response } = env.call("timeline.list_clips", {});
  const qeWarning = response.warnings.filter((text) => text.indexOf("QE") >= 0);
  assertTrue(qeWarning.length >= 1, "每次调用都带 QE 警告（说明书 §6.2）");
});

test("QE 启用后 effects.apply 施加效果（组件数读回校验）并如实标记转场校验结果", () => {
  const env = newEnv({ qe: true });
  const data = ok(env.call("effects.apply", { ref: "V:1:0", effect: "Gaussian Blur" }).response);
  assertEqual(data.effect, "Gaussian Blur", "效果名");
  assertEqual(data.component_index, 0, "组件序号");
  assertTrue(env.fake.state.mutationLog.indexOf("qe.addVideoEffect") >= 0, "走了 QE 施加效果");

  const transition = ok(env.call("effects.apply_transition", {
    ref: "V:1:0", transition: "Cross Dissolve", at: "start"
  }).response);
  assertEqual(transition.at, "start", "转场位置");
  assertEqual(transition.verified, true, "轨道 transitions 可读回 -> 已校验");
});

/* --- 7. 幂等键重放 ------------------------------------------------------ */

test("幂等键重放：第二次返回 replayed:true 且宿主没有二次执行", () => {
  const env = newEnv();
  const args = { name: "幂等箱" };
  const meta = { idempotencyKey: "key-1" };
  const first = env.call("project.bin_create", args, meta);
  ok(first.response, "第一次调用");
  const mutationsAfterFirst = env.fake.state.mutations;
  const binsAfterFirst = ok(env.call("project.bin_list", {}).response).bins.length;
  const second = env.call("project.bin_create", args, meta);
  ok(second.response, "第二次调用（重放）");
  assertEqual(second.response.replayed, true, "replayed 标记");
  assertEqual(env.fake.state.mutations, mutationsAfterFirst, "宿主未被二次执行");
  const binsAfterSecond = ok(env.call("project.bin_list", {}).response).bins.length;
  assertEqual(binsAfterSecond, binsAfterFirst, "素材箱数量没有增加");
  assertEqual(second.response.data.path, first.response.data.path, "返回上次的结果");
  // 不同幂等键应当真的执行（否则就是漏判）
  const third = env.call("project.bin_create", { name: "另一个箱" }, { idempotencyKey: "key-2" });
  ok(third.response, "不同键");
  assertEqual(!!third.response.replayed, false, "不同键不重放");
});

/* --- 8. 工具函数行为 --------------------------------------------------- */

test("DSH.parseRef 行为：合法 ref 解析、非法 ref 抛 REF_INVALID", () => {
  const env = newEnv();
  const video = env.dsh.parseRef("V:1:0");
  assertEqual(video.track_type, "video", "V -> video");
  assertEqual(video.track_index, 1, "轨道序号");
  assertEqual(video.index, 0, "片段序号");
  assertEqual(video.ref, "V:1:0", "规范化 ref");
  const audio = env.dsh.parseRef(" a : 2 : 13 ");
  assertEqual(audio.track_type, "audio", "a -> audio");
  assertEqual(audio.track_index, 2, "轨道序号");
  assertEqual(audio.index, 13, "片段序号");
  assertEqual(audio.ref, "A:2:13", "规范化 ref");
  let error = null;
  try { env.dsh.parseRef("X:1:0"); } catch (thrown) { error = thrown; }
  assertEqual(error && error.code, "REF_INVALID", "非法轨道字母");
  error = null;
  try { env.dsh.parseRef("V:1"); } catch (thrown) { error = thrown; }
  assertEqual(error && error.code, "REF_INVALID", "字段不足");
});

test("DSH.ticks / DSH.seconds / DSH.makeTime 往返一致", () => {
  const env = newEnv();
  assertEqual(env.dsh.ticks(1), 254016000000, "1 秒 -> tick");
  assertClose(env.dsh.seconds(254016000000), 1, 1e-12, "tick -> 秒");
  assertClose(env.dsh.seconds(String(127008000000)), 0.5, 1e-12, "字符串 tick -> 秒");
  const time = env.dsh.makeTime(1.5);
  assertClose(env.dsh.timeToSeconds(time), 1.5, 1e-9, "Time -> 秒");
  assertEqual(env.dsh.timeTicks(time), env.dsh.ticks(1.5), "Time -> tick");
  let code = null;
  try { env.dsh.ticks("abc"); } catch (error) { code = error.code; }
  assertEqual(code, "INVALID_ARGUMENT", "非数值秒数 -> INVALID_ARGUMENT");
});

test("DSH.findSequence / findProjectItem / treePath / findTrack / findTrackItem", () => {
  const env = newEnv();
  const active = env.dsh.findSequence(null);
  assertEqual(active.name, "Seq 1", "缺省取活动序列");
  assertEqual(env.dsh.findSequence("Seq 1").name, "Seq 1", "按名字");
  assertEqual(env.dsh.findSequence("\\\\demo.prproj\\Seq 1").name, "Seq 1", "按 treePath");
  assertEqual(env.dsh.findSequence("不存在"), null, "找不到返回 null");
  let code = null;
  try { env.dsh.requireSequence("不存在"); } catch (error) { code = error.code; }
  assertEqual(code, "NOT_FOUND", "requireSequence 抛 NOT_FOUND");

  const clip1 = env.dsh.findProjectItem("\\\\demo.prproj\\Bin A\\clip1.mp4");
  assertTrue(clip1 !== null, "按路径找条目");
  assertEqual(env.dsh.treePath(clip1), "\\\\demo.prproj\\Bin A\\clip1.mp4", "treePath");
  assertEqual(env.dsh.findProjectItem(clip1.nodeId).name, "clip1.mp4", "按 nodeId 找条目");
  assertEqual(env.dsh.findProjectItem("\\\\nope"), null, "找不到返回 null");
  assertEqual(env.dsh.itemTypeName(clip1), "clip", "素材类型");
  assertEqual(env.dsh.itemTypeName(env.dsh.findSequence("Seq 1").projectItem), "sequence",
    "序列条目类型（type 字段是 CLIP，必须靠能力探测纠正）");

  const sequence = env.dsh.requireSequence(null);
  const track = env.dsh.findTrack(sequence, "video", 1);
  assertTrue(track !== null, "取到 V1");
  assertEqual(env.dsh.findTrack(sequence, "video", 9), null, "越界返回 null");
  code = null;
  try { env.dsh.requireTrack(sequence, "video", 9); } catch (error) { code = error.code; }
  assertEqual(code, "NOT_FOUND", "requireTrack 越界");
  const located = env.dsh.requireTrackItem(sequence, "V:1:0");
  assertEqual(located.item.name, "clip1.mp4", "ref -> TrackItem");
  assertEqual(located.track_type, "video", "轨道类型");
});

/* --- 9. 真实执行：主要链路 -------------------------------------------- */

test("timeline.insert_clip 真实插入（多参数形式尝试 + 读回校验）", () => {
  const env = newEnv();
  const data = ok(env.call("timeline.insert_clip", {
    item: "\\\\demo.prproj\\Bin A\\clip2.mp4", track_type: "video", track_index: 2, at_s: 3
  }).response);
  assertEqual(data.ref, "V:2:0", "新片段 ref");
  assertClose(data.start_s, 3, 0.05, "起点");
  assertClose(data.end_s, 11, 0.05, "终点（clip2 时长 8s）");
  assertTrue(env.fake.state.mutationLog.indexOf("track.insertClip") >= 0, "真的调用了 insertClip");
});

test("timeline.overwrite_clip 真实覆盖 + list_clips/clip_info 读回一致", () => {
  const env = newEnv();
  const data = ok(env.call("timeline.overwrite_clip", {
    item: "\\\\demo.prproj\\Bin A\\clip2.mp4", track_type: "video", track_index: 1, at_s: 0
  }).response);
  assertEqual(data.ref, "V:1:0", "覆盖后仍是第 0 个片段");
  const info = ok(env.call("timeline.clip_info", { ref: "V:1:0" }).response);
  assertEqual(info.name, "clip2.mp4", "片段名");
  assertEqual(info.type, "video", "媒体类型");
  assertClose(info.start_s, 0, 0.05, "起点");
  assertClose(info.end_s, 8, 0.05, "终点");
  assertTrue(info.media_path.indexOf("clip2.mp4") >= 0, "媒体路径");
  assertEqual(typeof info.component_count, "number", "组件数可读");
  assertEqual(info.enabled, true, "enabled 可读（disabled=false）");
  const list = ok(env.call("timeline.list_clips", { track_type: "all" }).response);
  assertEqual(list.clips.length, 1, "覆盖后总片段数");
});

test("timeline.remove_clip 真实删除（波纹）+ 校验片段数减少", () => {
  const env = newEnv();
  env.call("timeline.insert_clip", {
    item: "\\\\demo.prproj\\Bin A\\clip2.mp4", track_index: 1, at_s: 10
  });
  const before = ok(env.call("timeline.list_clips", { track_index: 1 }).response).clips.length;
  const data = ok(env.call("timeline.remove_clip", { ref: "V:1:0", ripple: true }).response);
  assertEqual(data.removed, true, "removed");
  const after = ok(env.call("timeline.list_clips", { track_index: 1 }).response).clips.length;
  assertEqual(after, before - 1, "片段数减少");
});

test("timeline.move_clip 同轨道移动（TrackItem.move 位移量语义 + 读回校验）", () => {
  const env = newEnv();
  const data = ok(env.call("timeline.move_clip", { ref: "V:1:0", to_s: 7 }).response);
  assertEqual(data.path_used, "trackitem_move", "走公开的 TrackItem.move");
  assertClose(data.start_s, 7, 0.05, "新起点");
  assertClose(data.end_s, 12, 0.05, "新终点");
});

test("timeline.move_clip 跨轨道移动（摘除+重写复合路径）", () => {
  const env = newEnv();
  const data = ok(env.call("timeline.move_clip", {
    ref: "V:1:0", to_track_type: "video", to_track_index: 2, to_s: 2
  }).response);
  assertEqual(data.path_used, "remove_then_overwrite", "复合路径");
  assertEqual(data.ref, "V:2:0", "落在 V2 第 0 位");
  assertEqual(data.trim_restored, true, "源 in/out 已恢复");
  const v1 = ok(env.call("timeline.list_clips", { track_index: 1, track_type: "video" }).response);
  assertEqual(v1.clips.length, 0, "V1 已空");
});

test("timeline.trim_clip 真实改起止（写后读回校验）", () => {
  const env = newEnv();
  const data = ok(env.call("timeline.trim_clip", { ref: "V:1:0", start_s: 1, end_s: 4 }).response);
  assertClose(data.start_s, 1, 0.05, "新起点");
  assertClose(data.end_s, 4, 0.05, "新终点");
  assertEqual(data.path_used, "public", "公开路径");
  const invalid = env.call("timeline.trim_clip", { ref: "V:1:0", start_s: 9, end_s: 2 });
  failWith(invalid.response, "INVALID_ARGUMENT", "起止倒置");
});

test("timeline.add_marker / list_markers 真实往返（含时长与类型）", () => {
  const env = newEnv();
  const data = ok(env.call("timeline.add_marker", {
    at_s: 2, name: "章节一", comment: "备注", duration_s: 1.5, type: "chapter"
  }).response);
  assertEqual(data.name, "章节一", "标记名");
  assertEqual(data.type, "chapter", "标记类型");
  assertClose(data.at_s, 2, 0.05, "标记时间");
  assertClose(data.duration_s, 1.5, 0.05, "标记时长");
  assertEqual(data.index, 0, "序号");
  const list = ok(env.call("timeline.list_markers", {}).response);
  assertEqual(list.markers.length, 1, "标记数");
  assertEqual(list.markers[0].comment, "备注", "注释");
  assertEqual(list.markers[0].type, "chapter", "类型读回");
});

test("app.set_playhead：setPlayerPosition 只吃 tick 字符串时能自动落到第二形式", () => {
  const env = newEnv();
  const data = ok(env.call("app.set_playhead", { seconds: 3.5 }).response);
  assertClose(data.playhead_s, 3.5, 0.05, "播放头位置");
  // 状态读回
  const state = ok(env.call("app.get_state", {}).response);
  assertEqual(state.active_sequence, "Seq 1", "活动序列");
  assertClose(state.playhead_s, 3.5, 0.05, "get_state 播放头");
});

test("sequence.set_in_out：写后读回校验（含入点晚于出点的拒绝）", () => {
  const env = newEnv();
  const data = ok(env.call("sequence.set_in_out", { in_s: 1, out_s: 6 }).response);
  assertClose(data.in_point_s, 1, 0.05, "入点");
  assertClose(data.out_point_s, 6, 0.05, "出点");
  failWith(env.call("sequence.set_in_out", { in_s: 5, out_s: 2 }).response,
    "INVALID_ARGUMENT", "入点晚于出点");
  failWith(env.call("sequence.set_in_out", {}).response, "INVALID_ARGUMENT", "两个都没给");
});

test("app.get_state：选中项走公开 getSelection()（不依赖 QE）", () => {
  const env = newEnv();
  env.fake.clipAt("video", 1, 0).setSelected(1);
  const data = ok(env.call("app.get_state", { include_selection: true }).response);
  assertEqual(data.selection.length, 1, "选中项数量");
  assertEqual(data.selection[0].ref, "V:1:0", "选中项 ref");
  assertEqual(data.selection[0].track_type, "video", "轨道类型");
  assertEqual(data.warnings.length, 0, "无警告");
  const withoutSelection = ok(env.call("app.get_state", { include_selection: false }).response);
  assertEqual(withoutSelection.selection.length, 0, "显式不读选中项");
});

test("project.info / bin_list / bin_create / bin_move / item_find 真实往返", () => {
  const env = newEnv();
  const info = ok(env.call("project.info", {}).response);
  assertEqual(info.has_project, true, "有工程");
  assertEqual(info.name, "demo.prproj", "工程名");
  assertEqual(info.sequences.length, 1, "序列数");
  assertEqual(info.bin_count, 1, "素材箱数（不含根、不含序列条目）");
  assertEqual(info.item_count, 4, "条目数（Bin A + 2 素材 + 序列条目）");

  const created = ok(env.call("project.bin_create", { name: "素材箱B", parent: "\\\\demo.prproj\\Bin A" }).response);
  assertEqual(created.name, "素材箱B", "新素材箱名");
  assertIncludes(created.path, "Bin A", "路径在父箱下");
  failWith(env.call("project.bin_create", { name: "素材箱B", parent: "\\\\demo.prproj\\Bin A" }).response,
    "DUPLICATE_NAME", "重名拒绝");

  const bins = ok(env.call("project.bin_list", {}).response);
  const paths = bins.bins.map((bin) => bin.path);
  assertTrue(paths.indexOf("\\\\demo.prproj") >= 0, "含工程根");
  assertTrue(paths.indexOf("\\\\demo.prproj\\Bin A") >= 0, "含 Bin A");

  const moved = ok(env.call("project.bin_move", {
    items: ["\\\\demo.prproj\\Bin A\\clip2.mp4"], target_bin: "\\\\demo.prproj"
  }).response);
  assertEqual(moved.moved, 1, "移动 1 个");
  assertEqual(moved.failed.length, 0, "无失败");
  assertEqual(env.dsh.treePath(env.dsh.findProjectItem("\\\\demo.prproj\\clip2.mp4")),
    "\\\\demo.prproj\\clip2.mp4", "移动后路径");

  const found = ok(env.call("project.item_find", { query: "clip", limit: 10 }).response);
  assertEqual(found.items.length, 2, "找到 2 个 clip");
  assertEqual(found.truncated, false, "未截断");
  const limited = ok(env.call("project.item_find", { query: "clip", limit: 1 }).response);
  assertEqual(limited.items.length, 1, "limit 生效");
  assertEqual(limited.truncated, true, "截断标记");
  const sequences = ok(env.call("project.item_find", { query: "seq", kind: "sequence" }).response);
  assertEqual(sequences.items.length, 1, "按 kind 过滤序列");
});

test("sequence.create：无预设但有宽高 -> UNSUPPORTED_API；有预设 -> 成功", () => {
  const env = newEnv();
  const unsupported = env.call("sequence.create", { name: "S2", width: 1920, height: 1080, frame_rate: 25 });
  const error = failWith(unsupported.response, "UNSUPPORTED_API", "无预设无法设定分辨率");
  assertIncludes(error.remedy, "preset_path", "remedy 给出替代路径");

  const created = ok(env.call("sequence.create", { name: "S，带预设", preset_path: PRESET_PATH }).response);
  assertEqual(created.name, "S，带预设", "序列名");
  assertEqual(created.width, 1920, "读回宽度");
  assertEqual(created.height, 1080, "读回高度");
  assertClose(created.frame_rate, 25, 0.01, "读回帧率");

  const listed = ok(env.call("sequence.list", {}).response);
  assertEqual(listed.sequences.length, 2, "序列数");
  const sequenceInfo = ok(env.call("sequence.info", { sequence: "Seq 1" }).response);
  assertEqual(sequenceInfo.name, "Seq 1", "序列信息");
  assertEqual(sequenceInfo.video_tracks, 2, "视频轨数");
  assertEqual(sequenceInfo.audio_tracks, 1, "音频轨数");
  assertClose(sequenceInfo.duration_s, 5, 0.01, "序列时长");
  assertClose(sequenceInfo.frame_rate, 25, 0.01, "帧率");
});

test("sequence.create_from_clips 由素材建序列", () => {
  const env = newEnv();
  const data = ok(env.call("sequence.create_from_clips", {
    name: "拼起来的序列",
    items: ["\\\\demo.prproj\\Bin A\\clip1.mp4", "\\\\demo.prproj\\Bin A\\clip2.mp4"]
  }).response);
  assertEqual(data.name, "拼起来的序列", "序列名");
  assertEqual(data.clip_count, 2, "片段数（读回）");
});

test("sequence.set_active 真切换（读回活动序列 ID）", () => {
  const env = newEnv();
  const other = ok(env.call("sequence.create", { name: "另一个序列" }).response);
  const data = ok(env.call("sequence.set_active", { sequence: "Seq 1" }).response);
  assertEqual(data.active_sequence, "Seq 1", "活动序列名");
  assertTrue(data.id !== other.id, "ID 与另一个序列不同");
});

test("media.probe：读出可得字段，不可得字段进 unavailable（不猜）", () => {
  const env = newEnv();
  const data = ok(env.call("media.probe", { item: "\\\\demo.prproj\\Bin A\\clip1.mp4" }).response);
  assertEqual(data.name, "clip1.mp4", "名字");
  assertEqual(data.type, "clip", "类型");
  assertEqual(data.offline, false, "离线状态");
  assertClose(data.frame_rate, 25, 0.01, "帧率（来自 getFootageInterpretation）");
  assertEqual(data.has_video, true, "has_video 可读");
  assertEqual(data.width, null, "宽度不可得 -> null");
  assertTrue(data.unavailable.indexOf("width") >= 0, "width 在 unavailable 里");
  assertTrue(data.unavailable.indexOf("duration_s") >= 0, "duration_s 在 unavailable 里");
});

test("media.relink：改路径 + 读回校验；文件不存在时提前拒绝", () => {
  const env = newEnv();
  const mediaFile = path.join(TMP_DIR, "relinked.mp4");
  fs.writeFileSync(mediaFile, "x");
  const data = ok(env.call("media.relink", {
    item: "\\\\demo.prproj\\Bin A\\clip1.mp4", new_path: mediaFile
  }).response);
  assertIncludes(data.media_path, "relinked.mp4", "新路径读回");
  assertIncludes(String(data.previous_path), "clip1.mp4", "旧路径");
  failWith(env.call("media.relink", {
    item: "\\\\demo.prproj\\Bin A\\clip1.mp4", new_path: path.join(TMP_DIR, "nope.mp4")
  }).response, "INVALID_ARGUMENT", "目标文件不存在");
});

test("media.generate_proxies：无脚本化接口时如实报 UNSUPPORTED_API（并列出待办）", () => {
  const env = newEnv();
  const { response } = env.call("media.generate_proxies", {
    items: ["\\\\demo.prproj\\Bin A\\clip1.mp4"]
  });
  const error = failWith(response, "UNSUPPORTED_API", "无代理生成接口");
  assertIncludes(error.detail, "node-", "detail 里列出待生成代理的条目");
  assertIncludes(error.remedy, "生成代理", "remedy 给出可执行替代路径");
});

test("effects.list_available：QE 未启用 -> UNSUPPORTED_API；启用 -> QE 枚举 + 过滤", () => {
  const off = newEnv();
  failWith(off.call("effects.list_available", {}).response, "UNSUPPORTED_API", "QE 关闭");

  const on = newEnv({ qe: true });
  const data = ok(on.call("effects.list_available", { target: "clip", query: "blur" }).response);
  assertEqual(data.effects.length, 1, "按 query 过滤");
  assertEqual(data.effects[0].name, "Gaussian Blur", "效果名");
  assertEqual(data.effects[0].media_type, "video", "媒体类型");
  assertIncludes(data.note, "QE", "说明清单来源与局限");
});

test("effects.get_params / set_param：参数读写 + 时间变化参数如实标记", () => {
  const env = newEnv({ qe: true });
  ok(env.call("effects.apply", { ref: "V:1:0", effect: "Opacity" }).response);
  // 追加一个时间变化参数，验证 getValue 失败时不会编造值
  const clip = env.fake.clipAt("video", 1, 0);
  clip._components[0]._params.push(
    new fakePremiere.FakeComponentParam("时间变化参数", "TimeVarying", 1, true)
  );

  const params = ok(env.call("effects.get_params", { ref: "V:1:0" }).response);
  assertEqual(params.components.length, 1, "组件数");
  assertEqual(params.components[0].name, "Opacity", "组件名");
  const amount = params.components[0].properties.filter((p) => p.name === "Amount")[0];
  assertEqual(amount.value, 50, "参数字值");
  assertEqual(amount.type, "number", "参数类型");
  assertEqual(amount.value_readable, true, "值可读");
  const varying = params.components[0].properties.filter((p) => p.name === "时间变化参数")[0];
  assertEqual(varying.value, null, "时间变化参数值 -> null");
  assertEqual(varying.value_readable, false, "明确标记不可读");
  assertEqual(varying.time_varying, true, "time_varying 标记");

  const set = ok(env.call("effects.set_param", {
    ref: "V:1:0", component: "Opacity", param: "Amount", value: 80
  }).response);
  assertEqual(set.previous_value, 50, "旧值");
  assertEqual(set.value, 80, "新值（读回）");
  assertEqual(set.verified, true, "已校验");

  const badParam = env.call("effects.set_param", { ref: "V:1:0", param: "不存在的参数", value: 1 });
  failWith(badParam.response, "NOT_FOUND", "参数不存在");
});

test("export.encode_sequence / encode_file / job_status：入队拿 job id，状态如实 unknown", () => {
  const env = newEnv();
  const data = ok(env.call("export.encode_sequence", {
    sequence: "Seq 1", output_path: OUTPUT_PATH, preset_path: PRESET_PATH, work_area: "entire"
  }).response);
  assertEqual(data.queued, true, "已入队");
  assertIncludes(data.job_id, "job-", "job id");
  assertEqual(data.output_path, OUTPUT_PATH, "输出路径");
  assertEqual(data.sequence, "Seq 1", "序列名");

  const fileData = ok(env.call("export.encode_file", {
    item: "\\\\demo.prproj\\Bin A\\clip1.mp4", output_path: OUTPUT_PATH, preset_path: PRESET_PATH
  }).response);
  assertIncludes(fileData.job_id, "job-", "条目导出的 job id");

  const status = ok(env.call("export.job_status", { job_id: data.job_id }).response);
  assertEqual(status.state, "unknown", "宿主无状态接口 -> unknown");
  assertIncludes(status.message, "未提供", "说明为什么 unknown");

  failWith(env.call("export.encode_sequence", {
    sequence: "Seq 1", output_path: OUTPUT_PATH, preset_path: path.join(TMP_DIR, "nope.epr")
  }).response, "INVALID_ARGUMENT", "预设不存在");
});

test("project.save / save_as / close：脏标志不可知时拒绝可能弹框的关闭", () => {
  const env = newEnv();
  const saved = ok(env.call("project.save", {}).response);
  assertEqual(saved.saved, true, "保存成功");
  assertIncludes(saved.path, "demo.prproj", "路径");
  const savedAs = ok(env.call("project.save_as", { path: path.join(TMP_DIR, "copy.prproj") }).response);
  assertIncludes(savedAs.path, "copy.prproj", "另存路径");

  // 伪宿主没有 dirty 属性 -> 宿主未提供脏标志 -> 按"有改动"处理，拒绝关闭
  failWith(env.call("project.close", {}).response, "DIALOG_RISK", "脏标志不可知时拒绝");
  const closed = ok(env.call("project.close", { confirm_discard: true }).response);
  assertEqual(closed.closed, true, "确认丢弃后关闭");
  assertEqual(closed.discarded, true, "discarded 标记");
  assertEqual(env.fake.app.isDocumentOpen(), false, "工程已关闭");
});

test("project.import：真实导入后按媒体路径找回条目；不存在的路径进 missing", () => {
  const env = newEnv();
  const mediaA = path.join(TMP_DIR, "import_a.mp4");
  fs.writeFileSync(mediaA, "x");
  const data = ok(env.call("project.import", {
    paths: [mediaA, path.join(TMP_DIR, "missing.mp4")], bin: "\\\\demo.prproj\\Bin A"
  }).response);
  assertEqual(data.imported.length, 1, "导入 1 个");
  assertIncludes(data.imported[0].media_path, "import_a.mp4", "导入条目路径");
  assertEqual(data.missing.length, 1, "missing 1 个");
  assertIncludes(data.missing[0], "missing.mp4", "missing 路径");
  const allMissing = env.call("project.import", { paths: [path.join(TMP_DIR, "nope.mp4")] });
  failWith(allMissing.response, "INVALID_ARGUMENT", "全部路径不存在");
});

test("timeline.add_track / remove_track：QE 关闭时 QE_DISABLED，启用时按数量校验", () => {
  const off = newEnv();
  failWith(off.call("timeline.add_track", { track_type: "video" }).response, "QE_DISABLED", "QE 关闭");
  failWith(off.call("timeline.remove_track", { track_type: "video", track_index: 2 }).response,
    "QE_DISABLED", "QE 关闭");

  const on = newEnv({ qe: true });
  // 伪宿主的 QE 序列没有 addVideoTrack/removeVideoTrack：应如实报 UNSUPPORTED_API
  const added = on.call("timeline.add_track", { track_type: "video" });
  failWith(added.response, "UNSUPPORTED_API", "QE 无 addVideoTrack");
  const removed = on.call("timeline.remove_track", { track_type: "video", track_index: 2 });
  failWith(removed.response, "UNSUPPORTED_API", "QE 无 removeVideoTrack");
  // 且未产生任何宿主变更
  assertEqual(on.fake.state.mutations, 0, "失败的 QE 调用没有改动宿主");
});

test("UNSUPPORTED_API 的通用约束：不存在的 API 一律报错，不假装成功", () => {
  const env = newEnv();
  // 删掉伪宿主的 setInPoint，模拟旧宿主缺失该 API
  const sequenceProto = Object.getPrototypeOf(env.fixture.sequence);
  const originalSetInPoint = sequenceProto.setInPoint;
  delete sequenceProto.setInPoint;
  try {
    const { response } = env.call("sequence.set_in_out", { in_s: 2 });
    const error = failWith(response, "UNSUPPORTED_API", "缺失公开 API");
    assertIncludes(error.message, "setInPoint", "消息指名缺失的 API");
    assertTrue(typeof error.remedy === "string" && error.remedy.length > 0, "remedy 非空");
  } finally {
    sequenceProto.setInPoint = originalSetInPoint;
  }
});

/* --- 10. 处理器不可缺失的负向检查（防"悄悄少了处理器"） ---------------- */

test("每个 jsx 路由的处理器都必须能被调用（逐个空参调用不崩栈）", () => {
  const env = newEnv();
  const routes = env.dsh.routes;
  const problems = [];
  Object.keys(routes).forEach((cmd) => {
    const route = routes[cmd];
    if (route.handlerReady !== true) { return; }
    let response = null;
    try {
      response = env.call(cmd, {}, {}).response;
    } catch (error) {
      problems.push(cmd + " 抛出未捕获异常：" + error.message);
      return;
    }
    if (typeof response.ok !== "boolean") { problems.push(cmd + " 响应没有 ok 字段"); }
    if (response.ok === false && !response.error) { problems.push(cmd + " 失败但没有 error 对象"); }
    if (response.ok === false && response.error.code === "HANDLER_MISSING") {
      problems.push(cmd + " HANDLER_MISSING");
    }
  });
  assertEqual(problems.length, 0, "有问题的命令：" + JSON.stringify(problems));
});

/* ---------------------------------------------------------------- 收尾 */

console.log("");
console.log("== 结果 ==");
console.log("通过：" + results.pass + "，失败：" + results.fail);
results.failures.forEach((failure) => {
  console.log("  - " + failure.name);
});
try {
  fs.rmSync(TMP_DIR, { recursive: true, force: true });
} catch (error) {
  // 清理失败不影响结论
}
process.exit(results.fail === 0 ? 0 : 1);
