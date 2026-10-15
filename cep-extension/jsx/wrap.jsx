// =====================================================================
// wrap.jsx —— 异常包装与结构化错误（说明书 §6.4、契约 §5.2）
//
// ExtendScript 的错误信息极其贫瘠（往往只有一个行号），所以本层做三件事：
//   1. 提供唯一的抛错入口 DSH.fail(code, message, remedy, extra)；
//   2. 把处理器的一次调用包成统一的响应信封（成功/失败同一个形状）；
//   3. 失败时把宿主上下文补齐：code/message/remedy/line/file/command/
//      script_excerpt/args_snapshot，其中 script_excerpt 会真的去读出错文件
//      的第 line±5 行——读不到就返回 null，绝不因为"读文件失败"再抛一次异常。
//
// 另外提供 dry-run 的检查记录器（契约 §8.4 的 checks 数组就来自这里）：
//   DSH.beginChecks(ctx) / DSH.note(...) / DSH.dryRunResult(ctx, actions, payload)
// 这样"dry-run 与真实执行走同一套前置条件检查函数"是结构性保证，而不是靠自觉。
// =====================================================================

if (typeof DSH === "undefined") { var DSH = {}; }

// ---- dry-run 检查记录器 -------------------------------------------------

//: 非 null 时表示当前正处于某条命令的 dry-run 检查收集阶段。
DSH._checks = null;

DSH.beginChecks = function (ctx) {
    DSH._checks = (ctx && ctx.dryRun === true) ? [] : null;
    return DSH._checks;
};

DSH.note = function (name, ok, detail) {
    if (!DSH._checks) { return; }
    DSH._checks[DSH._checks.length] = {
        name: String(name),
        ok: (ok === true),
        detail: (typeof detail === "undefined" || detail === null) ? null : String(detail)
    };
};

DSH.endChecks = function () {
    var snapshot = DSH._checks ? DSH._checks.slice(0) : [];
    DSH._checks = null;
    return snapshot;
};

//: dry-run 的返回结构：与真实执行"同样的键"，只是值退化为 null/估算值。
DSH.dryRunResult = function (ctx, actions, payload) {
    var out = {
        dry_run: true,
        actions: actions ? actions : [],
        checks: DSH._checks ? DSH._checks.slice(0) : []
    };
    var key;
    if (payload) {
        for (key in payload) {
            if (Object.prototype.hasOwnProperty.call(payload, key)) { out[key] = payload[key]; }
        }
    }
    return out;
};

// ---- 结构化错误 ---------------------------------------------------------

//: ExtendScript 里 `$` 提供 fileName / line 等上下文；不存在时全部退化为 null。
DSH.scriptContext = function () {
    var info = { file: null, line: null };
    try {
        if (typeof $ !== "undefined" && $) {
            if ($.fileName) { info.file = String($.fileName); }
            if (typeof $.line === "number" && $.line > 0) { info.line = $.line; }
        }
    } catch (contextError) {
        // 忽略：上下文拿不到不影响主流程
    }
    return info;
};

DSH.fail = function (code, message, remedy, extra) {
    var context = DSH.scriptContext();
    var error = {
        __dshError: true,
        code: code ? String(code) : "HOST_ERROR",
        message: message ? String(message) : "未提供错误消息",
        remedy: remedy ? String(remedy) : null,
        file: context.file,
        line: context.line,
        detail: null,
        extra: null
    };
    if (extra) {
        if (typeof extra === "object") {
            if (typeof extra.detail !== "undefined") { error.detail = String(extra.detail); }
            if (typeof extra.line === "number") { error.line = extra.line; }
            if (extra.file) { error.file = String(extra.file); }
            error.extra = extra;
        } else {
            error.detail = String(extra);
        }
    }
    // dry-run 途中失败时，把已经收集到的前置条件检查一并带回客户端，
    // 否则 L1 只看到"失败了"，看不到"哪一项检查没过"。
    if (DSH._checks) { error.checks = DSH._checks.slice(0); }
    return error;
};

DSH.isDshError = function (candidate) {
    return !!(candidate && typeof candidate === "object" && candidate.__dshError === true);
};

//: 取行号：优先 DSH.fail 记录的抛出点，其次宿主错误自带的行号。
DSH.errorLine = function (error) {
    if (!error) { return null; }
    if (typeof error.line === "number" && error.line > 0) { return error.line; }
    if (typeof error.lineNumber === "number" && error.lineNumber > 0) { return error.lineNumber; }
    if (typeof error.number === "number" && error.number > 0) { return error.number; }
    var context = DSH.scriptContext();
    return context.line;
};

DSH.errorFile = function (error) {
    if (!error) { return null; }
    if (error.file) { return String(error.file); }
    if (error.fileName) { return String(error.fileName); }
    return DSH.scriptContext().file;
};

DSH.errorMessage = function (error) {
    if (!error) { return "未知错误"; }
    if (error.message) { return String(error.message); }
    try {
        return String(error);
    } catch (stringifyError) {
        return "无法序列化的宿主错误对象";
    }
};

//: 读出错文件第 line±5 行。任何失败都返回 null —— 这段代码本身绝不能再抛异常，
//: 否则"报告错误"的过程会变成新的错误源。
DSH.scriptExcerpt = function (file, line, span) {
    try {
        if (!file || !line || line < 1) { return null; }
        if (typeof File === "undefined") { return null; }
        var handle = new File(String(file));
        if (!handle.exists) { return null; }
        handle.encoding = "UTF-8";
        if (!handle.open("r")) { return null; }
        var text = handle.read();
        handle.close();
        if (!text) { return null; }
        var lines = String(text).split(/\r\n|\r|\n/);
        var width = span ? span : 5;
        var from = (line - width > 1) ? (line - width) : 1;
        var to = (line + width < lines.length) ? (line + width) : lines.length;
        var out = [];
        var i;
        for (i = from; i <= to; i += 1) {
            out[out.length] = String(i) + ": " + lines[i - 1];
        }
        return out.join("\n");
    } catch (excerptError) {
        return null;
    }
};

//: 把一个（DSH 的或宿主的）异常转成契约要求的结构化 error 对象。
DSH.formatError = function (command, error, args) {
    var isDsh = DSH.isDshError(error);
    var line = DSH.errorLine(error);
    var file = DSH.errorFile(error);
    var code = isDsh ? error.code : "HOST_ERROR";
    var message = DSH.errorMessage(error);
    var remedy = isDsh ? error.remedy : null;
    if (!remedy) {
        remedy = "这是宿主 API 抛出的异常：请确认宿主窗口没有未响应的模态对话框，"
            + "再调用 host_resync 重新对齐状态；若可复现请保留追踪号查面板日志";
    }
    if (!isDsh) {
        code = "HOST_ERROR";
        message = "宿主 API 异常：" + message;
    }
    var out = {
        code: code,
        message: message,
        remedy: remedy,
        line: line,
        file: file,
        command: command ? String(command) : null,
        script_excerpt: DSH.scriptExcerpt(file, line),
        args_snapshot: args ? args : null,
        detail: isDsh ? (error.detail ? error.detail : null) : null
    };
    if (isDsh && error.checks) { out.checks = error.checks; }
    return out;
};

// ---- 响应信封 -----------------------------------------------------------

DSH.baseResponse = function () {
    return {
        id: null,
        ok: true,
        data: null,
        error: null,
        host: null,
        warnings: [],
        timing: { host_ms: 0 }
    };
};

//: 宿主快照（契约 §5）：必须"永不抛异常"，因为它会被用在错误路径上。
DSH.hostSnapshot = function () {
    var snapshot = {
        app_version: null,
        project_path: null,
        dirty: null,
        has_project: false
    };
    try {
        if (typeof app !== "undefined" && app) {
            if (app.version) { snapshot.app_version = String(app.version); }
            var project = app.project;
            if (project && typeof project.rootItem !== "undefined" && project.rootItem) {
                snapshot.has_project = true;
                if (project.path) { snapshot.project_path = String(project.path); }
                snapshot.dirty = DSH.projectDirty();
            }
        }
    } catch (snapshotError) {
        // 快照失败不影响命令结果
    }
    return snapshot;
};

//: 工程脏标志。**公开 DOM 未文档化该属性**；拿不到就返回 null，
//: 绝不猜测（说明书 §12：让调用方看到"未知"，而不是一个假的 false）。
DSH.projectDirty = function () {
    try {
        var project = (typeof app !== "undefined" && app) ? app.project : null;
        if (!project) { return null; }
        if (typeof project.dirty === "boolean") { return project.dirty; }
        if (typeof project.isDirty === "boolean") { return project.isDirty; }
    } catch (dirtyError) {
        return null;
    }
    return null;
};

DSH.wrapError = function (command, traceId, code, message, started, remedy) {
    var response = DSH.baseResponse();
    response.ok = false;
    response.data = null;
    response.error = DSH.formatError(command, DSH.fail(code, message, remedy), null);
    response.host = DSH.hostSnapshot();
    // 说明书 §6.2：QE 启用时"每次调用返回中都带 qe_enabled 警告"。
    // dispatch.jsx 只在内层路径回填这个字段，早退路径（QE_DISABLED/UNKNOWN_COMMAND 等）
    // 由这里补齐，保证客户端在任何响应上都能看到 QE 状态。
    response.qe_enabled = DSH.qeEnabled();
    response.timing.host_ms = DSH.elapsedMs(started);
    return response;
};

DSH.wrapParseFailure = function (rawText, error, started) {
    var response = DSH.baseResponse();
    var message = DSH.errorMessage(error);
    response.ok = false;
    response.data = null;
    response.error = {
        // 契约 §5.4 的错误码表没有覆盖"请求本身解析失败"这一档，这里补一个
        // 明确的名字：它是协议层错误，不是业务错误。
        code: "PROTOCOL_PARSE_ERROR",
        message: "无法解析请求 JSON：" + message,
        remedy: "面板与 L1 的契约版本可能不一致：确认 dispatch.jsx 与 commands.json 同源"
            + "（python scripts/gen_jsx_dispatch.py --check），并检查请求是否被截断",
        line: DSH.errorLine(error),
        file: DSH.errorFile(error),
        command: null,
        script_excerpt: null,
        args_snapshot: null,
        detail: String(rawText).substring(0, 500)
    };
    response.host = DSH.hostSnapshot();
    response.timing.host_ms = DSH.elapsedMs(started);
    return response;
};

//: 处理器执行的唯一入口。dispatch.jsx 只调用这个函数。
DSH.wrap = function (command, handler, args, ctx, traceId, started) {
    var response = DSH.baseResponse();
    var data = null;

    DSH._checks = null;
    if (ctx && ctx.dryRun === true) { DSH._checks = []; }
    if (ctx && ctx.qeEnabled === true) {
        response.warnings[response.warnings.length] =
            "QE（未公开接口）已启用：相关命令可能使宿主崩溃，请先保存工程";
    }

    try {
        data = handler(args ? args : {}, ctx ? ctx : {});
        response.ok = true;
        response.data = (typeof data === "undefined") ? null : data;
        response.error = null;
    } catch (handlerError) {
        response.ok = false;
        response.data = null;
        response.error = DSH.formatError(command, handlerError, args ? args : {});
    } finally {
        DSH._checks = null;
    }

    response.host = DSH.hostSnapshot();
    response.timing.host_ms = DSH.elapsedMs(started);
    return response;
};

//: 宿主内耗时（毫秒，整数）。started 来自 dispatch.jsx 的 new Date().getTime()。
DSH.elapsedMs = function (started) {
    var now;
    try {
        now = (typeof DSH.nowMs === "function") ? DSH.nowMs() : new Date().getTime();
    } catch (nowError) {
        now = new Date().getTime();
    }
    if (typeof started !== "number" || started <= 0) { return 0; }
    var delta = now - started;
    return (delta > 0) ? Math.round(delta) : 0;
};
