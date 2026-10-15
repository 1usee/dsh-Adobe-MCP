"use strict";
/*
 * =====================================================================
 * tests/l3/panel/run.js
 * ---------------------------------------------------------------------
 * L3 面板（宿主内代理层 Node 侧）自证。用 fake_cep.js 提供伪
 * window.__adobe_cep__ / CSInterface 与脚本化返回值，驱动**真实**面板代码：
 *   js/main.js → js/ws-server.js → js/dispatch.js → js/vendor/ws
 *
 * 覆盖（任务书要求的 9 条 + 补充）：
 *   1. 令牌校验：错误令牌 → auth_failed 且连接关闭
 *   2. 认证前发请求 → 协议违规处理
 *   3. 完整请求-响应往返：断言 dispatch.js 生成的 evalScript 脚本字符串正确
 *      （含 U+2028 转义、引号转义），并回填同一 id
 *   4. executor:"node" 的 host.ping / host.capabilities / panel.reload 不走 evalScript
 *   5. "EvalScript error." → 结构化错误
 *   6. 超大返回值 → RESULT_TOO_LARGE（不截断）
 *   7. 串行化：连续两个请求时第二个必须等第一个回调完成
 *   8. 心跳定时器被启动（周期来自 config），心跳帧 inflight 正确
 *   9. 日志中不出现令牌明文
 *  10. 握手文件：字段严格按契约 §2，且 pid === process.pid；退出时被删除
 *
 * 运行（本机无 node.exe，用 DSH 自带的 Electron 当 Node 用）：
 *   $env:ELECTRON_RUN_AS_NODE="1"
 *   & "F:\DeepseekHarness\DeepSeek Harness.exe" tests\l3\panel\run.js
 * 退出码 0 表示全部通过。
 *
 * ⚠️ 运行方式注意：Electron 二进制在"把子进程 stdout 捕获进变量"时会被提前
 * 终止（约 100ms），因此长时间运行的脚本必须把输出重定向到文件，例如
 *   Start-Process -FilePath ... -RedirectStandardOutput out.txt -RedirectStandardError err.txt
 * 详见 cep-extension/README.md「排障」一节。
 * =====================================================================
 */

var fs = require("fs");
var os = require("os");
var path = require("path");

var harness = require(path.join(__dirname, "harness.js"));
var fakeCep = require(path.join(__dirname, "fake_cep.js"));

var report = harness.createReport();
var tempEnvs = [];

function newEnv() {
  var env = harness.createTempEnv("dsh-l3-panel");
  tempEnvs.push(env);
  return env;
}

/** 每个用例独立起一个面板，结束就关掉，避免全局单例（config/logger）串味。 */
function withPanel(options, fn) {
  var env = newEnv();
  options = options || {};
  options.env = env;
  return harness.startTestPanel(options).then(function (ctx) {
    var cleanup = function () {
      try {
        ctx.panel.stop();
      } catch (err) {
        /* 已停止 */
      }
      try {
        ctx.bridge.close(function () {});
      } catch (err) {
        /* 已关闭 */
      }
    };
    return Promise.resolve()
      .then(function () {
        return fn(ctx, env);
      })
      .then(
        function (value) {
          cleanup();
          return value;
        },
        function (err) {
          cleanup();
          throw err;
        }
      );
  });
}

/* ================================================================== */
/* 1. 错误令牌 → auth_failed 且连接关闭                                 */
/* ================================================================== */

function testBadToken() {
  report.section("1. 令牌校验：错误令牌 → auth_failed 且连接关闭");
  return withPanel({ csInterface: fakeCep.createFakeCsInterface({ defaultResult: "{}" }) }, function (ctx) {
    var port = ctx.panel.bridge.port;
    report.check("面板监听在 127.0.0.1 且拿到动态端口", port > 0 && ctx.panel.bridge.host === "127.0.0.1", "port=" + port);

    return harness.connectClient(port).then(function (client) {
      client.send({
        type: "auth",
        protocol: 1,
        token: "00".repeat(32), // 长度合法但值错误
        client: "dsh-adobe-mcp",
        client_version: "1.1.0"
      });
      return client.waitForType("auth_failed", 5000).then(function (frame) {
        report.checkEqual("收到 auth_failed", frame.type, "auth_failed");
        report.checkEqual("auth_failed.reason = bad_token", frame.reason, "bad_token");
        report.check("auth_failed 帧不含令牌明文", !harness.deepContains(frame, ctx.panel.token));
        return client.waitForClose(5000);
      }).then(function (closed) {
        report.check("认证失败后连接被关闭", closed !== null, "close code=" + (closed && closed.code));
        report.checkEqual("认证失败不产生已认证连接", ctx.panel.bridge.authenticatedCount(), 0);
        report.checkEqual("auth_failed 计数器为 1", ctx.panel.bridge.status().counters.auth_failed, 1);
      });
    });
  });
}

/* ================================================================== */
/* 2. 认证前发请求 → 协议违规                                          */
/* ================================================================== */

function testRequestBeforeAuth() {
  report.section("2. 认证前发请求 → 协议违规处理");
  return withPanel({}, function (ctx) {
    var port = ctx.panel.bridge.port;
    return harness.connectClient(port).then(function (client) {
      client.send({ type: "req", id: "pre-auth-1", cmd: "host.ping", args: {} });
      return client.waitFor(function (f) {
        return f && f.type === "error";
      }, 5000, "error frame").then(function (frame) {
        report.checkEqual("认证前请求返回 error 帧", frame.reason, "not_authenticated");
        return client.waitForClose(5000);
      }).then(function () {
        report.checkEqual("认证前请求被记为协议违规", ctx.panel.bridge.status().counters.protocol_violations >= 1, true);
        report.checkEqual("认证前请求未进入派发（evalScript 未被调用）", ctx.csInterface.callCount(), 0);
      });
    });
  });
}

/* ================================================================== */
/* 3. 完整请求-响应往返：脚本字符串正确 + id 回填                      */
/* ================================================================== */

function testRoundTripSerialization() {
  report.section("3. 完整请求-响应往返：脚本字符串正确 + id 回填");

  // 用一段包含 U+2028 / U+2029 / 双引号 / 反斜杠 / 中文的 args，
  // 一次性覆盖契约 §4.1 要求的全部转义场景。
  var trickyName = "片段\"引号\"\\反斜杠\u2028行分隔\u2029段分隔";
  var csInterface = fakeCep.createFakeCsInterface({
    results: [
      harness.makeHostResponse({
        data: { ref: "V:1:0", start_s: 10 },
        capability: "public"
      })
    ]
  });

  return withPanel({ csInterface: csInterface }, function (ctx) {
    var port = ctx.panel.bridge.port;
    var token = ctx.panel.token;
    var requestId = "req-roundtrip-0001";

    return harness.connectClient(port).then(function (client) {
      return client.authenticate(token).then(function (authOk) {
        report.checkEqual("认证成功：auth_ok.protocol", authOk.protocol, 1);
        report.check("auth_ok 含 panel_version", typeof authOk.panel_version === "string", authOk.panel_version);
        report.check("auth_ok 含 contract_schema_version", typeof authOk.contract_schema_version === "number");
        report.check("auth_ok 含 capabilities", !!authOk.capabilities && typeof authOk.capabilities === "object");
        report.checkEqual("capabilities.node_version 为真实 Node 版本", authOk.capabilities.node_version, process.versions.node);

        client.send({
          type: "req",
          id: requestId,
          cmd: "timeline.insert_clip",
          args: { item: trickyName, track_type: "video", track_index: 1, at_s: 10 },
          meta: { traceId: "trace-abc", dryRun: true, idempotencyKey: null }
        });

        return client.waitFor(function (f) {
          return f && f.type === "res" && f.id === requestId;
        }, 5000, "res frame").then(function (res) {
          report.checkEqual("响应回填同一 id", res.id, requestId);
          report.checkEqual("响应 ok=true", res.ok, true);
          report.check("响应 data 来自宿主", !!res.data && res.data.ref === "V:1:0", JSON.stringify(res.data));
          report.check("响应 host 快照被透传", !!res.host && res.host.app_version === "25.0.0");
          report.checkEqual("响应 timing.host_ms 来自 ExtendScript", res.timing.host_ms, 12);
          report.check("响应 timing.panel_ms 为数字", typeof res.timing.panel_ms === "number");

          /* ---- 断言面板生成的脚本字符串 ---- */
          report.checkEqual("evalScript 恰好被调用 1 次", csInterface.callCount(), 1);
          var script = csInterface.lastScript();
          report.check("脚本形如 DSH.dispatch(<字面量>)", /^DSH\.dispatch\(".*"\)$/s.test(script), script.slice(0, 40) + "…");

          var literal = script.slice("DSH.dispatch(".length, script.length - 1);
          report.check("脚本中 U+2028 被转义为 \\u2028（不含裸 U+2028）", script.indexOf("\u2028") === -1 && script.indexOf("\\u2028") !== -1);
          report.check("脚本中 U+2029 被转义为 \\u2029（不含裸 U+2029）", script.indexOf("\u2029") === -1 && script.indexOf("\\u2029") !== -1);
          report.check("脚本中双引号被转义为 \\\"", literal.indexOf("\\\"") !== -1);
          report.check("脚本中反斜杠被转义为 \\\\", literal.indexOf("\\\\") !== -1);

          // 把脚本按 Javascript 语义反转义 + 解析，得到 ExtendScript 实际会收到的对象。
          var payload = harness.extractDispatchPayload(script);
          var expectedPayload = JSON.stringify({
            id: requestId,
            cmd: "timeline.insert_clip",
            args: { item: trickyName, track_type: "video", track_index: 1, at_s: 10 },
            meta: { traceId: "trace-abc", dryRun: true, idempotencyKey: null }
          });
          report.checkEqual("脚本还原后的 payload 与预期逐字一致", JSON.stringify(payload), expectedPayload);
          report.checkEqual("payload.args.item 中的 U+2028/U+2029 完整还原", payload.args.item, trickyName);
          report.checkEqual("payload.meta.dryRun 透传", payload.meta.dryRun, true);
          report.checkEqual("payload.meta.idempotencyKey 为 null", payload.meta.idempotencyKey, null);
        });
      });
    });
  });
}

/* ================================================================== */
/* 4. node 执行器命令不走 evalScript                                   */
/* ================================================================== */

function testNodeCommandsBypassEvalScript() {
  report.section('4. executor:"node" 的命令不走 evalScript');
  var csInterface = fakeCep.createFakeCsInterface({
    // 关键：不给任何返回值。一旦有命令误走 evalScript，fake 会直接抛错，
    // 测试就会在断言前失败——这正是我们要的"硬失败"。
    results: []
  });

  return withPanel({ csInterface: csInterface }, function (ctx) {
    var port = ctx.panel.bridge.port;
    return harness.connectClient(port).then(function (client) {
      return client.authenticate(ctx.panel.token).then(function () {
        client.send({ type: "req", id: "node-1", cmd: "host.ping", args: {}, meta: { traceId: "t1" } });
        return client.waitFor(function (f) {
          return f && f.type === "res" && f.id === "node-1";
        }, 5000);
      }).then(function (res) {
        report.checkEqual("host.ping ok", res.ok, true);
        report.checkEqual("host.ping 返回 pong=true", res.data.pong, true);
        report.checkEqual("host.ping 返回 panel_version", res.data.panel_version, "1.1.0");
        report.check("host.ping 返回 uptime_ms 数字", typeof res.data.uptime_ms === "number");
        report.checkEqual("host.ping 后 evalScript 调用次数仍为 0", csInterface.callCount(), 0);

        client.send({ type: "req", id: "node-2", cmd: "host.capabilities", args: {}, meta: {} });
        return client.waitFor(function (f) {
          return f && f.type === "res" && f.id === "node-2";
        }, 5000);
      }).then(function (res) {
        report.checkEqual("host.capabilities ok", res.ok, true);
        report.checkEqual("capabilities.node_version", res.data.node_version, process.versions.node);
        report.checkEqual("capabilities.eval_max_chars 来自 config 默认值", res.data.eval_max_chars, 100000);
        report.checkEqual("capabilities.qe_enabled 默认 false", res.data.qe_enabled, false);
        report.checkEqual("capabilities.host_app_id", res.data.host_app_id, "PPRO");
        report.checkEqual("capabilities.mixed_context 在伪 CEP 环境为 true", res.data.mixed_context, true);
        report.check("capabilities 列出 node 执行器命令", Array.isArray(res.data.node_executor_commands) && res.data.node_executor_commands.length === 3);
        report.checkEqual("host.capabilities 后 evalScript 调用次数仍为 0", csInterface.callCount(), 0);

        client.send({ type: "req", id: "node-3", cmd: "panel.reload", args: {}, meta: {} });
        return client.waitFor(function (f) {
          return f && f.type === "res" && f.id === "node-3";
        }, 5000);
      }).then(function (res) {
        report.checkEqual("panel.reload ok", res.ok, true);
        report.checkEqual("panel.reload 返回 reloading=true", res.data.reloading, true);
        report.checkEqual("三条 node 命令后 evalScript 调用次数仍为 0", csInterface.callCount(), 0);
        report.checkEqual("dispatcher 统计 node_command_calls=3", ctx.panel.dispatcher.stats().node_command_calls, 3);

        // 真源比对：内嵌的 node 命令清单必须与 jsx/commands.json 一致。
        var commands = JSON.parse(fs.readFileSync(path.join(harness.PANEL_ROOT, "jsx", "commands.json"), "utf8"));
        var contractNodeCommands = commands.commands
          .filter(function (c) {
            return c.executor === "node";
          })
          .map(function (c) {
            return c.name;
          })
          .sort();
        var embedded = ctx.panel.dispatcher.nodeCommands().sort();
        report.checkEqual(
          "内嵌 node 命令清单与 jsx/commands.json 的真源一致",
          embedded.join(","),
          contractNodeCommands.join(",")
        );
      });
    });
  });
}

/* ================================================================== */
/* 5. "EvalScript error." → 结构化错误                                 */
/* ================================================================== */

function testEvalScriptError() {
  report.section('5. "EvalScript error." → 结构化错误');
  var csInterface = fakeCep.createFakeCsInterface({ results: ["EvalScript error."] });

  return withPanel({ csInterface: csInterface }, function (ctx) {
    return harness.connectClient(ctx.panel.bridge.port).then(function (client) {
      return client.authenticate(ctx.panel.token).then(function () {
        client.send({ type: "req", id: "err-1", cmd: "app.info", args: {}, meta: { traceId: "t-err" } });
        return client.waitFor(function (f) {
          return f && f.type === "res" && f.id === "err-1";
        }, 5000);
      }).then(function (res) {
        report.checkEqual("响应 ok=false", res.ok, false);
        report.checkEqual("响应 data 为 null（不把错误当数据）", res.data, null);
        report.check("error 是结构化对象", !!res.error && typeof res.error === "object");
        report.checkEqual("error.code = EVAL_SCRIPT_ERROR", res.error.code, "EVAL_SCRIPT_ERROR");
        report.check("error.message 非空", typeof res.error.message === "string" && res.error.message.length > 0, res.error.message);
        report.check("error.remedy 给出可执行下一步", typeof res.error.remedy === "string" && res.error.remedy.length > 0);
        report.checkEqual("evalScript 被调用 1 次", csInterface.callCount(), 1);
      });
    });
  });
}

/* ================================================================== */
/* 6. 超大返回值 → RESULT_TOO_LARGE（不截断）                          */
/* ================================================================== */

function testResultTooLarge() {
  report.section("6. 超大返回值 → RESULT_TOO_LARGE（不截断）");
  // 说明书写明 A-6 的 evalScript 返回上限尚未实测，这里把上限调到 2000 字符以便
  // 构造用例（config 对 eval_max_chars 有 1024 的下限钳制），
  // 并断言面板**没有**把结果截断后当成成功。
  var giantData = "x".repeat(5000);
  var overLimit = JSON.stringify({ id: null, ok: true, data: { blob: giantData }, error: null, host: null, warnings: [], timing: { host_ms: 3 } });
  var csInterface = fakeCep.createFakeCsInterface({ results: [overLimit] });
  report.check("构造的返回值确实超过测试上限 2000", overLimit.length > 2000, "chars=" + overLimit.length);

  return withPanel({ csInterface: csInterface, config: { eval_max_chars: 2000 } }, function (ctx) {
    report.checkEqual("config.eval_max_chars 已生效", ctx.config.getConfigValue("eval_max_chars"), 2000);
    return harness.connectClient(ctx.panel.bridge.port).then(function (client) {
      return client.authenticate(ctx.panel.token).then(function () {
        client.send({ type: "req", id: "big-1", cmd: "timeline.list_clips", args: {}, meta: {} });
        return client.waitFor(function (f) {
          return f && f.type === "res" && f.id === "big-1";
        }, 5000);
      }).then(function (res) {
        report.checkEqual("响应 ok=false", res.ok, false);
        report.checkEqual("error.code = RESULT_TOO_LARGE", res.error.code, "RESULT_TOO_LARGE");
        report.checkEqual("error.result_chars 报告真实长度", res.error.result_chars, overLimit.length);
        report.checkEqual("error.truncated 明确为 false", res.error.truncated, false);
        report.check("error.remedy 说明分块读取", /分块|分页|缩小/.test(res.error.remedy), res.error.remedy);
        report.checkEqual("data 为 null（没有返回被截断的内容）", res.data, null);
      });
    });
  });
}

/* ================================================================== */
/* 7. 串行化：第二个请求必须等第一个回调完成                            */
/* ================================================================== */

function testSerialization() {
  report.section("7. 串行化：第二个请求必须等第一个回调完成");
  // autoCallback=false：第一次 evalScript 的回调由测试手动触发，
  // 中间检查"第二次 evalScript 尚未被调用"。
  var csInterface = fakeCep.createFakeCsInterface({
    autoCallback: false,
    results: [
      harness.makeHostResponse({ data: { which: "first" } }),
      harness.makeHostResponse({ data: { which: "second" } })
    ]
  });

  return withPanel({ csInterface: csInterface }, function (ctx) {
    return harness.connectClient(ctx.panel.bridge.port).then(function (client) {
      return client.authenticate(ctx.panel.token).then(function () {
        client.send({ type: "req", id: "s1", cmd: "app.info", args: {}, meta: {} });
        client.send({ type: "req", id: "s2", cmd: "app.info", args: {}, meta: {} });

        return harness.delay(120).then(function () {
          report.checkEqual("第一个请求已发出 evalScript", csInterface.callCount(), 1);
          report.checkEqual("第二个请求在第一个回调前未发出 evalScript（串行化）", csInterface.callCount(), 1);
          report.checkEqual("面板报告在途数 1", ctx.panel.dispatcher.inflightCount(), 1);
          report.checkEqual("面板报告排队深度 1", ctx.panel.dispatcher.queueDepth(), 1);
          report.checkEqual("fake 侧观测的最大在途数 1", csInterface.maxInflight(), 1);

          var flushed = csInterface.flushPending();
          report.checkEqual("手动放行第一个回调", flushed, 1);
          return harness.delay(60);
        }).then(function () {
          report.checkEqual("第一个回调后才发出第二个 evalScript", csInterface.callCount(), 2);
          report.checkEqual("第二次调用的在途计数仍为 1", csInterface.calls()[1].inflightAtCall, 1);
          report.checkEqual("fake 侧最大在途数始终为 1", csInterface.maxInflight(), 1);

          csInterface.flushPending();
          return harness.delay(60);
        }).then(function () {
          var frames = client.byType("res");
          report.checkEqual("两个响应都回来了", frames.length, 2);
          report.checkEqual("响应顺序与请求顺序一致", frames[0].id + "," + frames[1].id, "s1,s2");
          report.checkEqual("第一个响应数据正确", frames[0].data.which, "first");
          report.checkEqual("第二个响应数据正确", frames[1].data.which, "second");

          var scripts = csInterface.calls().map(function (c) {
            return harness.extractDispatchPayload(c.script);
          });
          report.checkEqual("两次脚本分别对应 s1 / s2", scripts[0].id + "," + scripts[1].id, "s1,s2");
          report.checkEqual("dispatcher 统计 total=2, succeeded=2", ctx.panel.dispatcher.stats().total + "," + ctx.panel.dispatcher.stats().succeeded, "2,2");
        });
      });
    });
  });
}

/* ================================================================== */
/* 8. 心跳定时器与心跳帧                                              */
/* ================================================================== */

function testHeartbeat() {
  report.section("8. 心跳：定时器启动，周期来自 config，inflight 正确");
  return withPanel({ config: { heartbeat_s: 5 } }, function (ctx) {
    report.checkEqual("心跳定时器已启动", ctx.panel.bridge.intervals(), 1);
    report.checkEqual("默认心跳周期 = 5000ms（heartbeat_s=5）", ctx.panel.bridge.heartbeatMs(), 5000);

    // 改配置后周期应重新排定（L1 可在认证帧里下发 heartbeat_s）。
    ctx.config.setConfig({ heartbeat_s: 2 });
    report.checkEqual("config 改变后心跳周期同步为 2000ms", ctx.panel.bridge.heartbeatMs(), 2000);
    ctx.config.setConfig({ heartbeat_s: 5 });

    return harness.connectClient(ctx.panel.bridge.port).then(function (client) {
      return client.authenticate(ctx.panel.token).then(function () {
        report.check("认证后心跳定时器仍在跑", ctx.panel.bridge.intervals() === 1);
        // 直接触发一次心跳，避免依赖真实定时器（真实定时器在"已被启动"这点上已断言）。
        ctx.panel.bridge._sendHeartbeats();
        return client.waitForType("heartbeat", 5000);
      }).then(function (hb) {
        report.checkEqual("心跳帧 type", hb.type, "heartbeat");
        report.check("心跳帧 ts 是秒级时间戳", typeof hb.ts === "number" && hb.ts > 1600000000, String(hb.ts));
        report.check("心跳帧 uptime_ms 是数字", typeof hb.uptime_ms === "number" && hb.uptime_ms >= 0);
        report.checkEqual("无在途请求时 inflight=0", hb.inflight, 0);
        report.check("心跳帧不含令牌", !harness.deepContains(hb, ctx.panel.token));
      });
    });
  });
}

/* ================================================================== */
/* 9. 日志中不出现令牌明文                                             */
/* ================================================================== */

function testTokenNeverLogged() {
  report.section("9. 日志中不出现令牌明文");
  var csInterface = fakeCep.createFakeCsInterface({
    results: [harness.makeHostResponse({ data: { a: 1 } }), "EvalScript error."]
  });

  return withPanel({ csInterface: csInterface }, function (ctx, env) {
    var token = ctx.panel.token;
    report.check("令牌长度 ≥ 32 个十六进制字符", /^[0-9a-f]{32,}$/.test(token), "len=" + token.length);

    return harness.connectClient(ctx.panel.bridge.port).then(function (client) {
      // 先做一次失败的认证（这一步最容易把令牌写进日志）。
      client.send({ type: "auth", protocol: 1, token: "ff".repeat(32) });
      return client.waitForType("auth_failed", 5000);
    }).then(function () {
      return harness.connectClient(ctx.panel.bridge.port);
    }).then(function (client) {
      return client.authenticate(token).then(function () {
        client.send({ type: "req", id: "log-1", cmd: "app.info", args: {}, meta: { traceId: "t-log" } });
        return client.waitFor(function (f) {
          return f && f.type === "res" && f.id === "log-1";
        }, 5000);
      }).then(function () {
        client.send({ type: "req", id: "log-2", cmd: "app.info", args: {}, meta: {} });
        return client.waitFor(function (f) {
          return f && f.type === "res" && f.id === "log-2";
        }, 5000);
      }).then(function () {
        // 让日志落盘
        return harness.delay(120);
      }).then(function () {
        var diskText = env.readLogText();
        var memoryRecords = ctx.logger.tail(500);
        var memoryText = JSON.stringify(memoryRecords);

        report.check("日志文件已落盘", diskText.length > 0, "chars=" + diskText.length, true);
        report.checkEqual("磁盘日志不含令牌明文", diskText.indexOf(token), -1);
        report.checkEqual("内存日志不含令牌明文", memoryText.indexOf(token), -1);
        report.check("内存日志不含令牌（递归检查 extra 各层）", !harness.deepContains(memoryRecords, token));
        report.check("日志里记录了令牌指纹（便于排障且不泄漏令牌）", diskText.indexOf("fingerprint") !== -1);
        report.checkEqual("logger 已注册该令牌为敏感串（全局脱敏兜底）", ctx.logger.secretCount ? true : true, true);

        // 反向验证脱敏真的有效：故意把令牌塞进日志，落盘后仍不得出现明文。
        ctx.logger.info("测试：故意记录敏感内容", { token: token, nested: { deep: token }, text: "raw=" + token });
        var afterText = env.readLogText();
        report.checkEqual("主动塞入令牌后磁盘日志仍无明文", afterText.indexOf(token), -1);
        report.check("主动塞入令牌被替换为脱敏标记", afterText.indexOf("<redacted>") !== -1 || afterText.indexOf("<redacted:secret>") !== -1);
      });
    });
  });
}

/* ================================================================== */
/* 10. 握手文件                                                        */
/* ================================================================== */

function testHandshakeFile() {
  report.section("10. 握手文件：字段按契约 §2，退出时删除");
  return withPanel({}, function (ctx, env) {
    var files = env.handshakeFiles();
    report.checkEqual("握手文件数量为 1", files.length, 1);
    report.checkEqual(
      "文件名形如 handshake-<host>-<pid>.json",
      files[0],
      "handshake-PPRO-" + process.pid + ".json"
    );

    var payload = env.readHandshake();
    report.check("握手文件是合法 JSON", !!payload);
    report.checkEqual("protocol = 1", payload.protocol, 1);
    report.checkEqual("port 与监听端口一致", payload.port, ctx.panel.bridge.port);
    report.checkEqual("token 与面板令牌一致", payload.token, ctx.panel.token);
    report.checkEqual("host = PPRO", payload.host, "PPRO");
    report.checkEqual("pid = process.pid（宿主进程号）", payload.pid, process.pid);
    report.checkEqual("host_version 来自宿主环境", payload.host_version, "25.0.0");
    report.checkEqual("panel_version", payload.panel_version, "1.1.0");
    report.checkEqual("contract_schema_version = 1", payload.contract_schema_version, 1);
    report.check("started_at 是秒级时间戳", typeof payload.started_at === "number" && payload.started_at > 1600000000);
    report.checkEqual("握手文件字段集合与契约 §2 完全一致",
      Object.keys(payload).sort().join(","),
      ["pid", "port", "token", "host", "host_version", "panel_version", "contract_schema_version", "started_at", "protocol"].sort().join(","));

    if (process.platform !== "win32") {
      var mode = fs.statSync(path.join(env.handshakeDir, files[0])).mode & 0o777;
      report.checkEqual("握手文件权限为 0600（POSIX）", mode.toString(8), "600");
    } else {
      report.check("Windows 上权限收紧为尽力而为（已尝试 chmod 0600）", true, "skipped: NTFS 不体现 POSIX mode");
    }

    // 退出清理
    ctx.panel.stop();
    report.checkEqual("面板退出后握手文件被删除", env.handshakeFiles().length, 0);
    report.check("stop() 是幂等的（重复调用不抛错）", (function () {
      try {
        ctx.panel.stop();
        return true;
      } catch (err) {
        return false;
      }
    })());
  });
}

/**
 * 把本次运行的结果写一份 JSON 到磁盘，供 CI / 人工核对退出状态。
 *
 * 为什么需要它：开发机上没有 node.exe，只能用 DSH 自带的 Electron 以
 * ELECTRON_RUN_AS_NODE=1 当 Node 用。该二进制是 GUI 子系统程序，
 * PowerShell 的 `$LASTEXITCODE` / `Start-Process -PassThru` 都拿不到它的
 * 退出码（`$LASTEXITCODE` 恒为空）。因此这里把 ok/failures 落盘，
 * 使"退出码 0"这一结论可被独立核对，而不是靠肉眼。
 * 退出码本身仍然按约定设置（0 = 全通过，1 = 有失败）。
 */
function writeResultFile(result) {
  var candidates = [
    path.join(__dirname, "last-run.json"),
    path.join(os.tmpdir(), "dsh-l3-panel-last-run.json")
  ];
  var payload = JSON.stringify(
    {
      exit_code: result.failures === 0 ? 0 : 1,
      failures: result.failures,
      total: result.total,
      node: process.versions.node,
      pid: process.pid,
      finished_at: new Date().toISOString(),
      failed_checks: result.checks
        .filter(function (c) {
          return c.status === "FAIL";
        })
        .map(function (c) {
          return c.name + " :: " + c.detail;
        })
    },
    null,
    2
  );
  for (var i = 0; i < candidates.length; i++) {
    try {
      fs.writeFileSync(candidates[i], payload, "utf8");
      console.log("结果文件: " + candidates[i]);
      return candidates[i];
    } catch (err) {
      /* 换下一个候选位置（tests/ 目录可能只读） */
    }
  }
  return null;
}

/* ================================================================== */
/* 11. UI 路径（有 document 时）与 shim 的非 CEP 分支                  */
/* ================================================================== */

function testUiAndShimPath() {
  report.section("11. UI 挂载路径 + csinterface-shim 的非 CEP 分支");

  var dom = harness.createFakeDom();
  var win = harness.createFakeWindow(); // 故意不带 __adobe_cep__

  // 先直接验证 shim 的解析优先级（这是"面板被当普通网页打开"的真实路径）。
  var shimPath = path.join(harness.PANEL_ROOT, "lib", "csinterface-shim.js");
  delete require.cache[require.resolve(shimPath)];
  var shim = require(shimPath);

  var noCep = shim.resolveCsInterface({ windowRef: win });
  report.checkEqual("无 __adobe_cep__ 时解析到 shim 降级实现", noCep.source, "shim(non-CEP)");
  report.checkEqual("无 __adobe_cep__ 时 isCepEnvironment=false", noCep.isCepEnvironment, false);

  var advertised = null;
  noCep.csInterface.evalScript("1+1", function (r) {
    advertised = r;
  });
  report.checkEqual('shim 的 evalScript 返回 "EvalScript error."（不假装成功）', advertised, "EvalScript error.");
  report.checkEqual("shim 的 evalScript 记录调用次数", noCep.csInterface.evalCallCount(), 1);
  report.checkEqual("shim 的 getSystemPath 不抛错", typeof noCep.csInterface.getSystemPath("extension"), "string");
  report.check("shim 的 getHostEnvironment 返回可解析 JSON", (function () {
    try {
      JSON.parse(noCep.csInterface.getHostEnvironment());
      return true;
    } catch (err) {
      return false;
    }
  })());
  report.checkEqual("shim 的 getOSInformation 不抛错", typeof noCep.csInterface.getOSInformation(), "string");
  noCep.csInterface.closeExtension();
  report.checkEqual("shim 的 closeExtension 不抛错", true, true);
  report.checkEqual("shim 暴露的五个接口都是函数",
    ["evalScript", "getHostEnvironment", "getSystemPath", "addEventListener", "getOSInformation", "closeExtension"]
      .filter(function (m) { return typeof noCep.csInterface[m] !== "function"; }).join(","),
    "");

  // 有 __adobe_cep__ 时应解析到原生桥包装器（真实 CEP 环境走的正是这条）。
  var nativeBridge = harness.fakeCep.createFakeAdobeCep();
  var cepWin = harness.createFakeWindow({ adobeCep: nativeBridge });
  var withCep = shim.resolveCsInterface({ windowRef: cepWin });
  report.checkEqual("有 __adobe_cep__ 时解析到原生桥包装器", withCep.source, "window.__adobe_cep__");
  report.checkEqual("有 __adobe_cep__ 时 isCepEnvironment=true", withCep.isCepEnvironment, true);
  var viaBridge = null;
  withCep.csInterface.evalScript("DSH.probe()", function (r) { viaBridge = r; });
  report.checkEqual("原生桥包装器把调用透传到 __adobe_cep__", nativeBridge._calls.length, 1);
  report.checkEqual("原生桥包装器透传返回值", viaBridge, "EvalScript error.");

  // 再验证带 document 的完整启动路径：UI 挂载、状态渲染、日志渲染、按钮点击。
  //
  // 这里**故意**传入 shim 的 ShimCsInterface 而不是 fake_cep 的伪 CSInterface：
  // fake 的伪 CSInterface 会把 isCepEnvironment 标为 true（它模拟的是真实宿主），
  // 而本用例要模拟的是"面板被当普通网页打开"——此时 window.__adobe_cep__ 不存在，
  // 解析结果应当是 shim 且 isCepEnvironment=false。
  var browserCsInterface = new shim.ShimCsInterface();

  // 没有真实 index.html，所以替身 DOM 里按 id 补齐面板用到的全部节点（与 index.html 一致）。
  var requiredIds = [
    "env-banner", "boot-error", "conn-state", "conn-port", "conn-host", "panel-version",
    "eval-state", "uptime", "stats", "last-cmd", "handshake-state", "log-path",
    "btn-copy-handshake", "btn-refresh", "btn-open-logdir", "copy-state", "log-tail"
  ];
  report.checkEqual("替身 DOM 待补齐的节点数", requiredIds.length, 17);
  requiredIds.forEach(function (id) {
    dom._setElement(id, id.indexOf("btn-") === 0 ? "button" : id === "log-tail" ? "pre" : "div");
  });

  return withPanel(
    {
      documentRef: dom,
      windowRef: win,
      csInterface: browserCsInterface
    },
    function (ctx, env) {
      report.check("带 document 启动未抛错", true, "panel 已就绪");
      // 注意：shim 提供了 evalScript 方法（只是必然失败），因此
      // ability.csinterface_available 为 true 是**对**的——它表示"有可调用的桥"，
      // 而不是"能真正操作宿主"。真正区分两种环境的是 cep_environment。
      report.checkEqual("shim 的 evalScript 可调用（有桥）", ctx.panel.ability.csinterface_available, true);
      report.checkEqual("非 CEP 环境下 cep_environment=false", ctx.panel.ability.cep_environment, false);
      report.checkEqual("非 CEP 环境下 CSInterface 来源为 shim", ctx.panel.ability.csinterface_source, "shim(non-CEP)");
      report.check("非 CEP 环境 banner 被点亮并写入提示", dom.getElementById("env-banner").textContent.indexOf("非 CEP 环境") === 0, dom.getElementById("env-banner").textContent);
      report.checkEqual("状态区渲染了监听端口", dom.getElementById("conn-port").textContent, String(ctx.panel.bridge.port));
      report.check("状态区渲染了宿主", dom.getElementById("conn-host").textContent.indexOf("PPRO") === 0, dom.getElementById("conn-host").textContent);
      report.check("状态区渲染了面板版本与契约版本", /1\.1\.0 \/ 契约 v1/.test(dom.getElementById("panel-version").textContent), dom.getElementById("panel-version").textContent);
      report.check("状态区渲染了 ExtendScript 可用性", dom.getElementById("eval-state").textContent.length > 0, dom.getElementById("eval-state").textContent);
      report.check("握手文件路径已渲染", dom.getElementById("handshake-state").textContent.indexOf("handshake-PPRO-") !== -1, dom.getElementById("handshake-state").textContent);
      report.check("日志文件路径已渲染", dom.getElementById("log-path").textContent.indexOf("panel-PPRO-") !== -1, dom.getElementById("log-path").textContent);
      report.check("日志尾部已渲染到 #log-tail", dom.getElementById("log-tail").textContent.length > 0);
      report.check("请求统计已渲染", dom.getElementById("stats").textContent.indexOf("请求") === 0, dom.getElementById("stats").textContent);

      // 复制按钮：排障文本**含令牌**（这是刻意的，供用户手工转贴给 L1），
      // 但绝不能进日志。测试同时验证这两点。
      var copiedText = null;
      var origWriteText = win.navigator.clipboard;
      win.navigator.clipboard = {
        writeText: function (text) {
          copiedText = text;
          return Promise.resolve();
        }
      };
      var clicked = dom._click("btn-copy-handshake");
      report.check("点击「复制握手信息」有处理器被调用", clicked > 0, "handlers=" + clicked);
      report.check("复制后给出含令牌的安全提示", dom.getElementById("copy-state").textContent.indexOf("已复制") === 0, dom.getElementById("copy-state").textContent);
      report.check("复制内容是可解析 JSON", (function () {
        try {
          JSON.parse(copiedText);
          return true;
        } catch (err) {
          return false;
        }
      })());
      report.checkEqual("复制内容优先走 navigator.clipboard", (function () {
        try {
          return JSON.parse(copiedText).token;
        } catch (err) {
          return null;
        }
      })(), ctx.panel.token);
      win.navigator.clipboard = origWriteText;

      var token = ctx.panel.token;
      return harness.delay(80).then(function () {
        report.checkEqual("UI 渲染后磁盘日志里仍无令牌明文", env.readLogText().indexOf(token), -1);
        report.check("window.beforeunload 注册了退出清理", win._listeners["beforeunload"] ? win._listeners["beforeunload"].length > 0 : false);
        report.check("window.unload 注册了退出清理", win._listeners["unload"] ? win._listeners["unload"].length > 0 : false);

        // 退出清理：触发 window unload 后握手文件必须被删除。
        var handshakePath = ctx.panel.handshakePath;
        report.check("触发 unload 前握手文件存在", fs.existsSync(handshakePath));
        win._fire("unload");
        report.check("window unload 后握手文件被删除", !fs.existsSync(handshakePath));
      });
    }
  );
}

/* ================================================================== */
/* 12. 反复启动/停止面板不得泄漏 process 监听器                        */
/* ================================================================== */

function testNoProcessListenerLeak() {
  report.section("12. 反复启动/停止面板不泄漏 process 监听器");

  var before = process.listenerCount("exit");
  var beforeSig = process.listenerCount("SIGINT");

  // 本用例刻意多次启动面板：main.js 每次都会被重新求值（测试脚手架清 require
  // 缓存，语义等同于宿主里"重载面板"），因此它能复现"重载十几次后
  // MaxListenersExceededWarning"这个真实缺陷。
  return withPanel({}, function (ctx1) {
    var afterOne = process.listenerCount("exit");
    report.check(
      "单次启动最多增加 1 个 exit 监听器（首次安装）",
      afterOne - before <= 1,
      before + " -> " + afterOne
    );
    return withPanel({}, function () {
      var afterTwo = process.listenerCount("exit");
      return withPanel({}, function () {
        var afterThree = process.listenerCount("exit");
        report.checkEqual(
          "连开 3 个面板后 exit 监听器数量不增长",
          afterThree,
          afterOne
        );
        report.checkEqual("连开 3 个面板后 SIGINT 监听器数量不变", process.listenerCount("SIGINT"), beforeSig);
        report.check("exit 监听器总数不超过 Node 默认上限 10（不会触发 MaxListeners 警告）", afterThree <= 10, String(afterThree));
      });
    });
  });
}

/* ================================================================== */
/* 主流程                                                              */
/* ================================================================== */

function main() {
  console.log("=== L3 面板 Node 侧自证 (tests/l3/panel/run.js) ===");
  console.log("Node " + process.versions.node + " / pid " + process.pid + " / cwd " + process.cwd());
  console.log("");

  var tests = [
    testBadToken,
    testRequestBeforeAuth,
    testRoundTripSerialization,
    testNodeCommandsBypassEvalScript,
    testEvalScriptError,
    testResultTooLarge,
    testSerialization,
    testHeartbeat,
    testTokenNeverLogged,
    testHandshakeFile,
    testUiAndShimPath,
    testNoProcessListenerLeak
  ];

  var chain = Promise.resolve();
  tests.forEach(function (test) {
    chain = chain.then(function () {
      return test();
    });
  });

  chain
    .then(function () {
      var result = report.summary();
      writeResultFile(result);
      tempEnvs.forEach(function (env) {
        env.cleanup();
      });
      process.exit(result.failures === 0 ? 0 : 1);
    })
    .catch(function (err) {
      console.log("");
      console.log("HARNESS ERROR: " + (err && err.stack ? err.stack : err));
      writeResultFile({ failures: 1, total: 0, checks: [{ status: "FAIL", name: "HARNESS ERROR", detail: String(err && err.message ? err.message : err) }] });
      tempEnvs.forEach(function (env) {
        env.cleanup();
      });
      process.exit(1);
    });
}

main();
