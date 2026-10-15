// =====================================================================
// ops/sequence.jsx —— 序列处理器（契约 §5.3）
//   sequence.create            -> sequence_create
//   sequence.create_from_clips -> sequence_create_from_clips
//   sequence.list              -> sequence_list
//   sequence.info              -> sequence_info
//   sequence.set_active        -> sequence_set_active
//   sequence.set_in_out        -> sequence_set_in_out
//   sequence.set_work_area     -> sequence_set_work_area
//
// 公开 API 依据（ppro-scripting 文档）：
//   project.newSequence(name, presetPath)、project.createNewSequence(name, id)、
//   project.createNewSequenceFromClips(name, items, [bin])、project.openSequence(id)、
//   sequence.getInPoint()/getOutPoint()（返回秒字符串）、setInPoint/setOutPoint、
//   sequence.getWorkAreaInPoint()/getWorkAreaOutPoint()/setWorkAreaInPoint/OutPoint、
//   sequence.frameSizeHorizontal/Vertical、sequence.timebase、sequence.end、
//   sequence.getPlayerPosition()、sequence.getSettings()
//
// 注意（与契约文字的一处差异）：契约说 sequence.set_work_area"公开 DOM 无此能力"，
// 但文档里 Sequence.setWorkAreaInPoint/OutPoint 是公开接口。按硬性约束 3
// （必须先走公开 DOM），这里实现为"公开优先，缺失才走 QE"。
// =====================================================================

if (typeof DSH === "undefined") { var DSH = {}; }

//: 读取序列的宽/高/帧率。逐项探测，拿不到就 null（不猜）。
function dsh_sequenceVideoSpec(sequence) {
    var spec = { width: null, height: null, frame_rate: null };
    if (!sequence) { return spec; }
    if (DSH.hasProperty(sequence, "frameSizeHorizontal")) {
        var width = Number(sequence.frameSizeHorizontal);
        if (isFinite(width) && width > 0) { spec.width = width; }
    }
    if (DSH.hasProperty(sequence, "frameSizeVertical")) {
        var height = Number(sequence.frameSizeVertical);
        if (isFinite(height) && height > 0) { spec.height = height; }
    }
    spec.frame_rate = DSH.sequenceFrameRate(sequence);
    var getSettings = DSH.api(sequence, "getSettings");
    if (getSettings && (spec.width === null || spec.height === null || spec.frame_rate === null)) {
        try {
            var settings = getSettings.call(sequence);
            if (settings) {
                if (spec.width === null && typeof settings.videoFrameWidth !== "undefined") {
                    spec.width = Number(settings.videoFrameWidth);
                }
                if (spec.height === null && typeof settings.videoFrameHeight !== "undefined") {
                    spec.height = Number(settings.videoFrameHeight);
                }
                if (spec.frame_rate === null && typeof settings.videoFrameRate !== "undefined") {
                    var frameRate = Number(settings.videoFrameRate);
                    if (isFinite(frameRate) && frameRate > 0) { spec.frame_rate = frameRate; }
                }
            }
        } catch (settingsError) {
            // 保持 null
        }
    }
    return spec;
}

function dsh_sequenceSummary(sequence) {
    var spec = dsh_sequenceVideoSpec(sequence);
    return {
        id: DSH.hasProperty(sequence, "sequenceID") ? String(sequence.sequenceID) : null,
        name: DSH.hasProperty(sequence, "name") ? String(sequence.name) : null,
        path: sequence.projectItem ? DSH.treePathSafe(sequence.projectItem) : null,
        video_tracks: dsh_trackCountSafe(sequence, "video"),
        audio_tracks: dsh_trackCountSafe(sequence, "audio"),
        duration_s: DSH.sequenceDurationSeconds(sequence),
        frame_rate: spec.frame_rate
    };
}

function dsh_trackCountSafe(sequence, trackType) {
    try {
        return DSH.trackCount(sequence, trackType);
    } catch (countError) {
        return null;
    }
}

// ---- sequence.create ---------------------------------------------------

function sequence_create(args, ctx) {
    DSH.beginChecks(ctx);
    var name = DSH.argString(args, "name", true, null);
    var presetPath = DSH.argString(args, "preset_path", false, null);
    var width = DSH.argInt(args, "width", false, null);
    var height = DSH.argInt(args, "height", false, null);
    var frameRate = DSH.argNumber(args, "frame_rate", false, null);
    var project = DSH.requireProject();

    var hasExplicitSpec = (width !== null) || (height !== null) || (frameRate !== null);
    if (presetPath !== null) {
        var presetExists = DSH.fileExists(presetPath);
        DSH.note("preset_file_exists", presetExists, presetPath);
        if (!presetExists) {
            throw DSH.fail("INVALID_ARGUMENT", "序列预设文件不存在：" + presetPath,
                "传一个真实存在的 .sqpreset 绝对路径（宿主自带预设在程序目录的 Settings 下）");
        }
        DSH.requireApi(project, "newSequence",
            "该宿主版本无法脚本化新建序列；请在宿主界面新建", "Project");
    } else if (hasExplicitSpec) {
        // 宿主公开 API 只能"按预设"建序列，没有在无预设时设定分辨率/帧率的接口。
        throw DSH.fail("UNSUPPORTED_API",
            "只给了 width/height/frame_rate 但没有 preset_path：宿主公开 DOM 无法在无预设时"
                + "设定这些值（Sequence.setSettings 只作用于已存在的序列）",
            "改为传入 preset_path（.sqpreset）；或先用 default 预设建序列，"
                + "再用宿主界面调整设置（本工具不猜分辨率/帧率是否生效）");
    } else {
        DSH.requireApi(project, "createNewSequence",
            "该宿主版本无法脚本化新建序列；请在宿主界面新建", "Project");
    }

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            [presetPath !== null
                ? ("用预设 " + presetPath + " 新建序列 " + name)
                : ("用宿主默认序列设置新建序列 " + name)],
            {
                id: null, name: name, path: null,
                width: width, height: height, frame_rate: frameRate
            });
    }

    var sequence = null;
    try {
        if (presetPath !== null) {
            sequence = project.newSequence(name, presetPath);
        } else {
            sequence = project.createNewSequence(name, DSH.guid());
        }
    } catch (createError) {
        throw DSH.fail("HOST_ERROR", "新建序列调用失败：" + DSH.errorMessage(createError),
            "确认预设文件与宿主版本匹配、磁盘可写；宿主事件面板可能有更详细的原因");
    }
    if (!sequence || sequence === 0) {
        throw DSH.fail("HOST_ERROR", "宿主未返回新建的序列（name=" + name + "）",
            "可能预设不兼容或名称被宿主拒绝；请在宿主界面确认");
    }
    var spec = dsh_sequenceVideoSpec(sequence);
    return {
        id: DSH.hasProperty(sequence, "sequenceID") ? String(sequence.sequenceID) : null,
        name: DSH.hasProperty(sequence, "name") ? String(sequence.name) : name,
        path: sequence.projectItem ? DSH.treePathSafe(sequence.projectItem) : null,
        width: spec.width,
        height: spec.height,
        frame_rate: spec.frame_rate
    };
}

// ---- sequence.create_from_clips ----------------------------------------

function sequence_create_from_clips(args, ctx) {
    DSH.beginChecks(ctx);
    var name = DSH.argString(args, "name", true, null);
    var itemRefs = DSH.argStringArray(args, "items", true);
    var project = DSH.requireProject();
    var items = [];
    var i;
    for (i = 0; i < itemRefs.length; i += 1) {
        items[items.length] = DSH.requireProjectItem(itemRefs[i]);
    }
    DSH.requireApi(project, "createNewSequenceFromClips",
        "该宿主版本无法由素材创建序列；请先用 ppro_sequence_create 建序列再插入素材", "Project");

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["用 " + items.length + " 个素材创建序列 " + name + "（顺序即时间线顺序）"],
            { id: null, name: name, path: null, clip_count: items.length });
    }

    var sequence = null;
    try {
        sequence = project.createNewSequenceFromClips(name, items);
    } catch (createError) {
        throw DSH.fail("HOST_ERROR", "createNewSequenceFromClips() 调用失败：" + DSH.errorMessage(createError),
            "确认传入的条目都是可上时间线的素材（素材箱/序列不能直接进时间线）");
    }
    if (!sequence || sequence === 0) {
        throw DSH.fail("HOST_ERROR", "宿主未返回新建的序列：" + name,
            "素材可能不含可用的音视频轨；请在宿主界面确认");
    }
    var clipCount = 0;
    var types = ["video", "audio"];
    var t;
    for (t = 0; t < types.length; t += 1) {
        var count = dsh_trackCountSafe(sequence, types[t]);
        var trackIndex;
        for (trackIndex = 1; count !== null && trackIndex <= count; trackIndex += 1) {
            var track = DSH.findTrack(sequence, types[t], trackIndex);
            if (track) { clipCount += DSH.trackItems(track).length; }
        }
    }
    return {
        id: DSH.hasProperty(sequence, "sequenceID") ? String(sequence.sequenceID) : null,
        name: DSH.hasProperty(sequence, "name") ? String(sequence.name) : name,
        path: sequence.projectItem ? DSH.treePathSafe(sequence.projectItem) : null,
        clip_count: clipCount
    };
}

// ---- sequence.list ----------------------------------------------------

function sequence_list(args, ctx) {
    var sequences = DSH.sequenceList();
    var out = [];
    var i;
    for (i = 0; i < sequences.length; i += 1) {
        out[out.length] = dsh_sequenceSummary(sequences[i]);
    }
    return { sequences: out };
}

// ---- sequence.info ----------------------------------------------------

function sequence_info(args, ctx) {
    var selector = DSH.argString(args, "sequence", false, null);
    var sequence = DSH.requireSequence(selector);
    var summary = dsh_sequenceSummary(sequence);
    var inOut = DSH.sequenceInOut(sequence);
    var spec = dsh_sequenceVideoSpec(sequence);
    return {
        id: summary.id,
        name: summary.name,
        path: summary.path,
        video_tracks: summary.video_tracks,
        audio_tracks: summary.audio_tracks,
        duration_s: summary.duration_s,
        in_point_s: inOut.in_s,
        out_point_s: inOut.out_s,
        playhead_s: DSH.sequencePlayheadSeconds(sequence),
        frame_rate: spec.frame_rate,
        width: spec.width,
        height: spec.height
    };
}

// ---- sequence.set_active ----------------------------------------------

function sequence_set_active(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", true, null);
    var sequence = DSH.requireSequence(selector);
    var project = DSH.requireProject();
    var sequenceId = DSH.hasProperty(sequence, "sequenceID") ? String(sequence.sequenceID) : "";
    DSH.note("sequence_id_readable", sequenceId !== "",
        sequenceId !== "" ? null : "序列没有可读的 sequenceID");
    if (sequenceId === "") {
        throw DSH.fail("UNSUPPORTED_API", "序列没有可读的 sequenceID，无法用公开 API 设为活动序列",
            "请在宿主界面双击该序列把它设为活动序列后再调用其它工具");
    }
    DSH.requireApi(project, "openSequence",
        "该宿主版本无法脚本化切换活动序列；请在宿主界面双击序列", "Project");

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx, ["把序列 " + sequence.name + " 设为活动序列（在时间线打开）"],
            { active_sequence: String(sequence.name), id: sequenceId });
    }

    var opened = false;
    try {
        opened = project.openSequence(sequenceId) === true;
    } catch (openError) {
        throw DSH.fail("HOST_ERROR", "project.openSequence() 调用失败：" + DSH.errorMessage(openError),
            "确认该序列仍存在（没有被删除）；必要时刷新工程");
    }
    if (!opened) {
        throw DSH.fail("HOST_ERROR", "project.openSequence() 返回失败：" + sequenceId,
            "序列可能已被删除；用 ppro_sequence_list 取回最新列表");
    }
    var active = null;
    try {
        active = project.activeSequence;
    } catch (activeError) {
        active = null;
    }
    var activeId = (active && typeof active !== "number" && DSH.hasProperty(active, "sequenceID"))
        ? String(active.sequenceID) : null;
    if (activeId !== sequenceId) {
        throw DSH.fail("HOST_ERROR",
            "切换后活动序列的 sequenceID 与目标不一致：期望 " + sequenceId + "，实际 " + activeId,
            "宿主可能同时打开了多个序列；请用 ppro_app_get_state 确认当前活动序列");
    }
    return { active_sequence: String(sequence.name), id: sequenceId };
}

// ---- sequence.set_in_out ----------------------------------------------

function sequence_set_in_out(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var inSeconds = DSH.argNumber(args, "in_s", false, null);
    var outSeconds = DSH.argNumber(args, "out_s", false, null);
    if (inSeconds === null && outSeconds === null) {
        throw DSH.argReport("in_s/out_s", "至少要给出其中一个", "传 in_s、out_s 或两者都给");
    }
    if (inSeconds !== null && inSeconds < 0) { throw DSH.argReport("in_s", "不能是负数"); }
    if (outSeconds !== null && outSeconds < 0) { throw DSH.argReport("out_s", "不能是负数"); }
    if (inSeconds !== null && outSeconds !== null && inSeconds >= outSeconds) {
        throw DSH.argReport("in_s/out_s", "入点必须早于出点（in_s=" + inSeconds + "，out_s=" + outSeconds + "）");
    }
    var sequence = DSH.requireSequence(selector);
    var tolerance = DSH.frameToleranceSeconds(sequence);
    var actions = [];
    if (inSeconds !== null) { actions[actions.length] = "把入点设为 " + inSeconds + "s"; }
    if (outSeconds !== null) { actions[actions.length] = "把出点设为 " + outSeconds + "s"; }

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx, actions, { in_point_s: inSeconds, out_point_s: outSeconds });
    }

    if (inSeconds !== null) {
        var targetIn = inSeconds;
        DSH.applyWithVariants(sequence, "setInPoint", DSH.timeArgVariants(inSeconds),
            function () {
                var current = DSH.sequenceInOut(sequence).in_s;
                if (current === null) { return true; }
                return DSH.nearlyEqual(current, targetIn, tolerance);
            },
            "该宿主版本无法脚本化设置序列入点；请在时间线面板手动设置", "Sequence");
    }
    if (outSeconds !== null) {
        var targetOut = outSeconds;
        DSH.applyWithVariants(sequence, "setOutPoint", DSH.timeArgVariants(outSeconds),
            function () {
                var current = DSH.sequenceInOut(sequence).out_s;
                if (current === null) { return true; }
                return DSH.nearlyEqual(current, targetOut, tolerance);
            },
            "该宿主版本无法脚本化设置序列出点；请在时间线面板手动设置", "Sequence");
    }
    var final = DSH.sequenceInOut(sequence);
    return { in_point_s: final.in_s, out_point_s: final.out_s };
}

// ---- sequence.set_work_area -------------------------------------------

//: 工作区读回（秒）。公开 getWorkAreaInPoint/OutPoint 返回**秒字符串**，
//: 所以用 secondsFromValue（用 timeToSeconds 会把 1 秒读成 1 tick）。
function dsh_readWorkArea(sequence) {
    var result = { start_s: null, end_s: null, readable: false };
    var getStart = DSH.api(sequence, "getWorkAreaInPoint");
    var getEnd = DSH.api(sequence, "getWorkAreaOutPoint");
    if (getStart) {
        try {
            result.start_s = DSH.secondsFromValue(getStart.call(sequence));
            result.readable = true;
        } catch (startError) {
            result.start_s = null;
        }
    }
    if (getEnd) {
        try {
            result.end_s = DSH.secondsFromValue(getEnd.call(sequence));
            result.readable = true;
        } catch (endError) {
            result.end_s = null;
        }
    }
    return result;
}

function sequence_set_work_area(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var startSeconds = DSH.argNumber(args, "start_s", true, null);
    var endSeconds = DSH.argNumber(args, "end_s", true, null);
    if (startSeconds < 0 || endSeconds < 0) {
        throw DSH.argReport("start_s/end_s", "不能是负数");
    }
    if (startSeconds >= endSeconds) {
        throw DSH.argReport("start_s/end_s",
            "工作区起点必须早于终点（start_s=" + startSeconds + "，end_s=" + endSeconds + "）");
    }
    var sequence = DSH.requireSequence(selector);

    var setStart = DSH.api(sequence, "setWorkAreaInPoint");
    var setEnd = DSH.api(sequence, "setWorkAreaOutPoint");
    var publicAvailable = !!(setStart && setEnd);
    DSH.note("public_work_area_api", publicAvailable,
        publicAvailable ? null : "公开 DOM 没有 setWorkAreaInPoint/OutPoint");
    if (!publicAvailable && !DSH.qeEnabled()) {
        throw DSH.fail("QE_DISABLED",
            "公开 DOM 没有 setWorkAreaInPoint/OutPoint，且 QE 未启用，无法设置工作区",
            "在配置中设置 extendscript.enable_qe = true 后重启面板；"
                + "注意 QE 是未公开接口，可能导致宿主崩溃");
    }

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["把序列 " + sequence.name + " 的工作区设为 " + startSeconds + "s - " + endSeconds + "s"],
            { work_area_start_s: startSeconds, work_area_end_s: endSeconds });
    }

    var usage = null;
    if (publicAvailable) {
        var toleranceForVerify = DSH.frameToleranceSeconds(sequence);
        // 只有读回接口存在时才做校验；缺失的字段不能当成"不一致"（否则会误报失败）。
        var verifyStart = dsh_readWorkArea(sequence).start_s === null ? null : function () {
            var now = dsh_readWorkArea(sequence).start_s;
            return now === null ? true : DSH.nearlyEqual(now, startSeconds, toleranceForVerify);
        };
        var verifyEnd = dsh_readWorkArea(sequence).end_s === null ? null : function () {
            var now = dsh_readWorkArea(sequence).end_s;
            return now === null ? true : DSH.nearlyEqual(now, endSeconds, toleranceForVerify);
        };
        DSH.applyWithVariants(sequence, "setWorkAreaInPoint", DSH.timeArgVariants(startSeconds),
            verifyStart, "该宿主版本的 setWorkAreaInPoint 不可用", "Sequence");
        DSH.applyWithVariants(sequence, "setWorkAreaOutPoint", DSH.timeArgVariants(endSeconds),
            verifyEnd, "该宿主版本的 setWorkAreaOutPoint 不可用", "Sequence");
        usage = "public";
    } else {
        DSH.qe.setWorkArea(sequence, startSeconds, endSeconds);
        usage = "qe";
    }

    var readBack = dsh_readWorkArea(sequence);
    var tolerance = DSH.frameToleranceSeconds(sequence);
    if (readBack.readable) {
        // 读不到的字段（null）不参与比较：只校验真正读回来的值。
        var startOk = (readBack.start_s === null)
            || DSH.nearlyEqual(readBack.start_s, startSeconds, tolerance);
        var endOk = (readBack.end_s === null)
            || DSH.nearlyEqual(readBack.end_s, endSeconds, tolerance);
        if (!startOk || !endOk) {
            throw DSH.fail("HOST_ERROR",
                "工作区写入后读回不一致：期望 " + startSeconds + "s - " + endSeconds + "s，实际 "
                    + readBack.start_s + "s - " + readBack.end_s + "s",
                "宿主可能按帧对齐或拒绝了该范围；请在时间线面板确认工作区");
        }
    }
    return {
        work_area_start_s: readBack.start_s,
        work_area_end_s: readBack.end_s,
        // 读回接口缺失时明确告知"未校验"，不假装已验证
        verified: readBack.readable,
        path_used: usage
    };
}
