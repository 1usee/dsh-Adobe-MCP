// =====================================================================
// 自动生成文件 —— 请勿手工编辑。
// 由 scripts/gen_jsx_dispatch.py 从 cep-extension/jsx/commands.json 生成；
// 任何手工改动都会在下一次生成时被覆盖，并被契约测试判为失败。
// 重新生成：python scripts/gen_jsx_dispatch.py
// =====================================================================

// 契约 schema_version: 1
// 命令总数: 48（jsx 45 / node 3）

#include "json2.jsx"
#include "es3_compat.jsx"
#include "wrap.jsx"
#include "util.jsx"
#include "ops/app.jsx"
#include "ops/project.jsx"
#include "ops/sequence.jsx"
#include "ops/timeline.jsx"
#include "ops/effects.jsx"
#include "ops/media.jsx"
#include "ops/export.jsx"
#include "ops/qe.jsx"

if (typeof DSH === "undefined") { var DSH = {}; }

DSH.contractSchemaVersion = 1;
DSH.ticksPerSecond = 254016000000;

// 命令路由表：由 commands.json 生成，必须与契约逐字一致。
DSH.routes = {
    "host.ping": { file: "", handler: "handlePing", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: false },
    "host.capabilities": { file: "", handler: "handleCapabilities", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: false },
    "panel.reload": { file: "", handler: "handleReload", risk: "T0", dryRun: false, idempotent: false, capability: "public", handlerReady: false },
    "host.state": { file: "app.jsx", handler: "host_state", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "app.info": { file: "app.jsx", handler: "app_info", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "app.get_state": { file: "app.jsx", handler: "app_get_state", risk: "T0", dryRun: false, idempotent: true, capability: "public_or_qe", handlerReady: true },
    "app.set_playhead": { file: "app.jsx", handler: "app_set_playhead", risk: "T1", dryRun: true, idempotent: true, capability: "public", handlerReady: true },
    "project.info": { file: "project.jsx", handler: "project_info", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "project.new": { file: "project.jsx", handler: "project_new", risk: "T2", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "project.open": { file: "project.jsx", handler: "project_open", risk: "T2", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "project.save": { file: "project.jsx", handler: "project_save", risk: "T1", dryRun: true, idempotent: true, capability: "public", handlerReady: true },
    "project.save_as": { file: "project.jsx", handler: "project_save_as", risk: "T2", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "project.close": { file: "project.jsx", handler: "project_close", risk: "T2", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "project.import": { file: "project.jsx", handler: "project_import", risk: "T1", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "project.bin_create": { file: "project.jsx", handler: "project_bin_create", risk: "T1", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "project.bin_list": { file: "project.jsx", handler: "project_bin_list", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "project.bin_move": { file: "project.jsx", handler: "project_bin_move", risk: "T1", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "project.item_find": { file: "project.jsx", handler: "project_item_find", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "sequence.create": { file: "sequence.jsx", handler: "sequence_create", risk: "T1", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "sequence.create_from_clips": { file: "sequence.jsx", handler: "sequence_create_from_clips", risk: "T1", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "sequence.list": { file: "sequence.jsx", handler: "sequence_list", risk: "T1", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "sequence.info": { file: "sequence.jsx", handler: "sequence_info", risk: "T1", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "sequence.set_active": { file: "sequence.jsx", handler: "sequence_set_active", risk: "T1", dryRun: true, idempotent: true, capability: "public", handlerReady: true },
    "sequence.set_in_out": { file: "sequence.jsx", handler: "sequence_set_in_out", risk: "T1", dryRun: true, idempotent: true, capability: "public", handlerReady: true },
    "sequence.set_work_area": { file: "sequence.jsx", handler: "sequence_set_work_area", risk: "T1", dryRun: true, idempotent: true, capability: "public_or_qe", handlerReady: true },
    "timeline.list_clips": { file: "timeline.jsx", handler: "timeline_list_clips", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "timeline.clip_info": { file: "timeline.jsx", handler: "timeline_clip_info", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "timeline.insert_clip": { file: "timeline.jsx", handler: "timeline_insert_clip", risk: "T1", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "timeline.overwrite_clip": { file: "timeline.jsx", handler: "timeline_overwrite_clip", risk: "T1", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "timeline.move_clip": { file: "timeline.jsx", handler: "timeline_move_clip", risk: "T1", dryRun: true, idempotent: false, capability: "public_or_qe", handlerReady: true },
    "timeline.trim_clip": { file: "timeline.jsx", handler: "timeline_trim_clip", risk: "T1", dryRun: true, idempotent: false, capability: "public_or_qe", handlerReady: true },
    "timeline.remove_clip": { file: "timeline.jsx", handler: "timeline_remove_clip", risk: "T2", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "timeline.razor": { file: "timeline.jsx", handler: "timeline_razor", risk: "T2", dryRun: true, idempotent: false, capability: "qe", handlerReady: true },
    "timeline.add_track": { file: "timeline.jsx", handler: "timeline_add_track", risk: "T1", dryRun: true, idempotent: false, capability: "public_or_qe", handlerReady: true },
    "timeline.remove_track": { file: "timeline.jsx", handler: "timeline_remove_track", risk: "T2", dryRun: true, idempotent: false, capability: "public_or_qe", handlerReady: true },
    "timeline.add_marker": { file: "timeline.jsx", handler: "timeline_add_marker", risk: "T1", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "timeline.list_markers": { file: "timeline.jsx", handler: "timeline_list_markers", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "effects.list_available": { file: "effects.jsx", handler: "effects_list_available", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "effects.apply": { file: "effects.jsx", handler: "effects_apply", risk: "T1", dryRun: true, idempotent: false, capability: "qe", handlerReady: true },
    "effects.get_params": { file: "effects.jsx", handler: "effects_get_params", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "effects.set_param": { file: "effects.jsx", handler: "effects_set_param", risk: "T1", dryRun: true, idempotent: true, capability: "public", handlerReady: true },
    "effects.apply_transition": { file: "effects.jsx", handler: "effects_apply_transition", risk: "T1", dryRun: true, idempotent: false, capability: "qe", handlerReady: true },
    "media.probe": { file: "media.jsx", handler: "media_probe", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: true },
    "media.relink": { file: "media.jsx", handler: "media_relink", risk: "T1", dryRun: true, idempotent: true, capability: "public", handlerReady: true },
    "media.generate_proxies": { file: "media.jsx", handler: "media_generate_proxies", risk: "T1", dryRun: true, idempotent: false, capability: "public_or_qe", handlerReady: true },
    "export.encode_sequence": { file: "export.jsx", handler: "export_encode_sequence", risk: "T2", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "export.encode_file": { file: "export.jsx", handler: "export_encode_file", risk: "T2", dryRun: true, idempotent: false, capability: "public", handlerReady: true },
    "export.job_status": { file: "export.jsx", handler: "export_job_status", risk: "T0", dryRun: false, idempotent: true, capability: "public", handlerReady: true }
};

// 路由表在宿主内的落点：jsx 命令走全局函数，node 命令由面板 Node 侧处理。
DSH.resolveHandler = function (name) {
    var scope = (typeof $.global !== "undefined") ? $.global : null;
    if (scope && typeof scope[name] === "function") { return scope[name]; }
    if (typeof this !== "undefined" && this && typeof this[name] === "function") {
        return this[name];
    }
    return null;
};

// 幂等键记录（说明书 §5.3.5）：只记录**执行成功**的命令，重放同键返回上次结果。
DSH._idempotencyOrder = [];
DSH._idempotencyResults = {};
DSH.IDEMPOTENCY_LIMIT = 200;

DSH.rememberIdempotent = function (key, response) {
    if (!key || !response || response.ok !== true) { return; }
    if (typeof DSH._idempotencyResults[key] === "undefined") {
        DSH._idempotencyOrder.push(key);
    }
    DSH._idempotencyResults[key] = response;
    while (DSH._idempotencyOrder.length > DSH.IDEMPOTENCY_LIMIT) {
        var oldest = DSH._idempotencyOrder.shift();
        delete DSH._idempotencyResults[oldest];
    }
};

DSH.recallIdempotent = function (key) {
    if (!key) { return null; }
    if (typeof DSH._idempotencyResults[key] === "undefined") { return null; }
    return DSH._idempotencyResults[key];
};

// 命令总入口：面板通过 CSInterface.evalScript("DSH.dispatch(<json 字符串>)") 调用。
// 入参：{ id, cmd, args, meta: { traceId, dryRun, idempotencyKey } }
// 出参：{ id, ok, data, error, host, warnings, timing: { host_ms } }
DSH.finish = function (response, request) {
    response.id = (request && request.id) ? request.id : null;
    return DSH.json.stringify(response);
};

DSH.dispatch = function (jsonText) {
    var started = new Date().getTime();
    var request = null;
    var command = "";
    var traceId = "";

    try {
        request = DSH.json.parse(String(jsonText));
    } catch (parseError) {
        return DSH.finish(DSH.wrapParseFailure(String(jsonText), parseError, started), null);
    }

    command = (request && request.cmd) ? String(request.cmd) : "";
    traceId = (request && request.meta && request.meta.traceId)
        ? String(request.meta.traceId) : "";

    var args = (request && request.args) ? request.args : {};
    var meta = (request && request.meta) ? request.meta : {};
    var idempotencyKey = meta.idempotencyKey ? String(meta.idempotencyKey) : "";
    var route = DSH.routes[command];

    if (!route) {
        return DSH.finish(DSH.wrapError(command, traceId,
            "UNKNOWN_COMMAND", "面板未注册命令 " + command, started,
            "升级/重新部署 CEP 面板后再试；当前面板契约版本 " + DSH.contractSchemaVersion),
            request);
    }

    if (route.handlerReady === false) {
        return DSH.finish(DSH.wrapError(command, traceId,
            "WRONG_EXECUTOR", "命令 " + command + " 由面板 Node 侧处理，不应到达 ExtendScript",
            started, "这是桥接实现错误，请上报；L1 侧不应把该命令发往 ExtendScript"), request);
    }

    // QE 门禁（说明书 §6.2）：L1 已按配置拦截，这里是第二道防线。
    if (route.capability === "qe" && !DSH.qeEnabled()) {
        return DSH.finish(DSH.wrapError(command, traceId,
            "QE_DISABLED", "命令 " + command + " 依赖未公开的 QE 接口，当前未启用",
            started, "在配置中设置 extendscript.enable_qe = true 后重启宿主面板"), request);
    }

    // 非幂等命令重放：直接返回上次结果，不重复执行（说明书 §5.3.5）。
    if (idempotencyKey && route.idempotent === false) {
        var remembered = DSH.recallIdempotent(idempotencyKey);
        if (remembered) {
            remembered.replayed = true;
            return DSH.finish(remembered, request);
        }
    }

    var handler = DSH.resolveHandler(route.handler);
    if (!handler) {
        return DSH.finish(DSH.wrapError(command, traceId,
            "HANDLER_MISSING", "命令 " + command + " 的处理器 " + route.handler
                + " 未在面板中定义（" + route.file + "）", started,
            "确认 CEP 面板部署完整且版本与 L1 契约一致：python scripts/gen_jsx_dispatch.py --check"),
            request);
    }

    var ctx = {
        command: command,
        traceId: traceId,
        dryRun: meta.dryRun ? true : false,
        qeEnabled: DSH.qeEnabled(),
        idempotencyKey: idempotencyKey ? idempotencyKey : null,
        startedAt: started
    };

    var response = DSH.wrap(command, handler, args, ctx, traceId, started);

    // 失败时附带契约元数据，便于 L1 侧做一致性诊断。
    response.capability = route.capability;
    if (route.capability === "qe" || route.capability === "public_or_qe") {
        response.qe_enabled = DSH.qeEnabled();
    }
    if (idempotencyKey && route.idempotent === false) {
        DSH.rememberIdempotent(idempotencyKey, response);
    }
    return DSH.finish(response, request);
};
