// =====================================================================
// ops/qe.jsx —— QE（未公开接口）护栏层（说明书 §6.2）
//
// 为什么单独一个文件：QE 是本项目唯一"可能把宿主搞崩"的能力，
// 它必须被集中收口，不能散落在各业务处理器里。本文件提供：
//   * 统一门禁：DSH.qeEnabled() 为假 -> 一律 QE_DISABLED，先于任何 QE 调用；
//   * 统一入口：DSH.qe.* 每个方法内部的调用都包 try/catch；
//   * 统一检测：QE 的成员名没有官方文档，所以全部走"候选名 + 结果校验"，
//     校验不过就报错，绝不把"调用了"当成"成功了"。
//
// ⚠ 风险提示：QE 是未公开接口，可能导致宿主崩溃或工程损坏。
//   仅在 extendscript.enable_qe=true 且公开 DOM 无路可走时使用。
// =====================================================================

if (typeof DSH === "undefined") { var DSH = {}; }
if (typeof DSH.qe === "undefined") { DSH.qe = {}; }

DSH.qe._rootObject = null;
DSH.qe._enabledOnce = false;

//: 门禁：任何 QE 调用的第一步。
DSH.qe.ensure = function () {
    if (!DSH.qeEnabled()) {
        throw DSH.fail("QE_DISABLED",
            "该命令依赖未公开的 QE 接口，当前配置未启用 QE",
            "在配置中设置 extendscript.enable_qe = true 并重启宿主面板；"
                + "注意 QE 是未公开接口，可能导致宿主崩溃");
    }
    if (!DSH.qe._enabledOnce) {
        var enable = (typeof app !== "undefined" && app) ? DSH.api(app, "enableQE") : null;
        if (!enable) {
            throw DSH.fail("UNSUPPORTED_API", "当前宿主没有 app.enableQE()",
                "确认宿主为 Premiere Pro；若该版本不提供 QE，则相关命令无法实现");
        }
        try {
            enable.call(app);
        } catch (enableError) {
            throw DSH.fail("HOST_ERROR", "app.enableQE() 调用失败：" + DSH.errorMessage(enableError),
                "关闭 extendscript.enable_qe 后重试；QE 启用失败通常是宿主版本不支持");
        }
        DSH.qe._enabledOnce = true;
    }
    return true;
};

//: QE 根对象：enableQE() 之后宿主会暴露一个全局 `qe`。
DSH.qe.root = function () {
    DSH.qe.ensure();
    if (DSH.qe._rootObject) { return DSH.qe._rootObject; }
    var root = null;
    try {
        if (typeof qe !== "undefined" && qe) { root = qe; }
    } catch (qeGlobalError) {
        root = null;
    }
    if (!root) {
        try {
            if (typeof $ !== "undefined" && $.global && $.global.qe) { root = $.global.qe; }
        } catch (qeGlobalScopeError) {
            root = null;
        }
    }
    if (!root) {
        try {
            if (typeof app !== "undefined" && app && app.qe) { root = app.qe; }
        } catch (qeAppError) {
            root = null;
        }
    }
    if (!root) {
        throw DSH.fail("UNSUPPORTED_API",
            "app.enableQE() 之后找不到全局 qe 对象",
            "该宿主版本的 QE 入口与预期不同；请关闭 QE 并使用公开 DOM 路径，或上报宿主版本");
    }
    DSH.qe._rootObject = root;
    return root;
};

DSH.qe.project = function () {
    var root = DSH.qe.root();
    var project = root.project;
    if (!project) {
        throw DSH.fail("UNSUPPORTED_API", "QE 根对象没有 project 成员",
            "该宿主版本的 QE 结构与预期不同；请关闭 QE 并改用公开 DOM");
    }
    return project;
};

//: 候选方法名逐个尝试：存在且不抛异常的第一个胜出。
//: 全都不存在 -> UNSUPPORTED_API；存在但全部抛异常 -> HOST_ERROR（带原始信息）。
DSH.qe.call = function (target, names, args, remedy) {
    if (!target) {
        throw DSH.fail("UNSUPPORTED_API", "QE 目标对象不存在",
            remedy ? remedy : "QE 返回了空对象；请在真机上确认该 QE 路径是否可用");
    }
    var tried = [];
    var failures = [];
    var i;
    var fn;
    for (i = 0; i < names.length; i += 1) {
        fn = DSH.api(target, names[i]);
        tried[tried.length] = names[i];
        if (!fn) { continue; }
        try {
            return fn.apply(target, args ? args : []);
        } catch (callError) {
            failures[failures.length] = names[i] + ": " + DSH.errorMessage(callError);
        }
    }
    if (failures.length === 0) {
        throw DSH.fail("UNSUPPORTED_API",
            "QE 对象没有以下任何方法：" + tried.join(" / "),
            remedy ? remedy : "该宿主版本的 QE 未提供此能力；请关闭 QE 并改用公开 DOM 路径");
    }
    throw DSH.fail("HOST_ERROR",
        "QE 方法调用全部失败：" + failures.join(" | "),
        remedy ? remedy : "QE 是未公开接口，行为随版本变化；请关闭 extendscript.enable_qe 后改用公开路径");
};

//: 时间参数在 QE 里可能是 Time 对象、tick 字符串或秒数，逐一尝试。
DSH.qe.callWithTime = function (target, names, timeSeconds, prefixArgs, suffixArgs, remedy) {
    var variants = [];
    try {
        variants[variants.length] = { kind: "time", value: DSH.makeTime(timeSeconds) };
    } catch (timeError) {
        // Time 构造不可用，跳过该变体
    }
    variants[variants.length] = { kind: "ticks", value: String(DSH.ticks(timeSeconds)) };
    variants[variants.length] = { kind: "seconds", value: Number(timeSeconds) };

    var errors = [];
    var v;
    var i;
    for (v = 0; v < variants.length; v += 1) {
        var args = [];
        var p;
        for (p = 0; p < (prefixArgs ? prefixArgs.length : 0); p += 1) {
            args[args.length] = prefixArgs[p];
        }
        args[args.length] = variants[v].value;
        for (p = 0; p < (suffixArgs ? suffixArgs.length : 0); p += 1) {
            args[args.length] = suffixArgs[p];
        }
        try {
            return DSH.qe.call(target, names,
                args, remedy + "（已尝试时间参数形式：" + variants[v].kind + "）");
        } catch (callError) {
            errors[errors.length] = variants[v].kind + " -> " + DSH.errorMessage(callError);
        }
    }
    throw DSH.fail("UNSUPPORTED_API",
        "QE 时间类调用没有可用的参数形式：" + errors.join(" | "),
        remedy ? remedy : "该宿主版本的 QE 时间参数约定与预期不同，请在真机上确认");
};

//: 找到某个公开 Sequence 对应的 QE 序列。
DSH.qe.sequence = function (sequence) {
    var qeProject = DSH.qe.project();
    var active = null;
    var getActive = DSH.api(qeProject, "getActiveSequence");
    if (getActive) {
        try {
            active = getActive.call(qeProject);
        } catch (activeError) {
            active = null;
        }
    }
    var wanted = sequence && sequence.name ? String(sequence.name) : null;
    if (active && wanted) {
        try {
            if (String(active.name) === wanted) { return active; }
        } catch (nameError) {
            // 名字读不到就继续找别的入口
        }
    } else if (active && !wanted) {
        return active;
    }

    var getSequenceAt = DSH.api(qeProject, "getSequenceAt");
    if (getSequenceAt && wanted) {
        var index = 0;
        var missed = 0;
        while (index < 256 && missed < 4) {
            var candidate = null;
            try {
                candidate = getSequenceAt.call(qeProject, index);
            } catch (atError) {
                candidate = null;
            }
            if (!candidate) {
                missed += 1;
            } else {
                missed = 0;
                try {
                    if (String(candidate.name) === wanted) { return candidate; }
                } catch (candidateNameError) {
                    // 继续
                }
            }
            index += 1;
        }
    }
    if (!wanted && active) { return active; }
    throw DSH.fail("UNSUPPORTED_API",
        "无法把序列 " + (wanted ? wanted : "(活动序列)") + " 映射到 QE 序列对象",
        "QE 只保证能取到活动序列；请先 sequence.set_active 把目标序列设为活动序列再重试");
};

DSH.qe.track = function (qeSequence, trackType, trackIndex) {
    var names = (trackType === "audio")
        ? ["getAudioTrackAt", "getAudioTrack"]
        : ["getVideoTrackAt", "getVideoTrack"];
    var result = DSH.qe.call(qeSequence, names, [trackIndex - 1],
        "QE 序列没有 getVideoTrackAt/getAudioTrackAt；请在真机上确认该版本 QE 的轨道访问方式");
    if (!result) {
        throw DSH.fail("NOT_FOUND", "QE 里找不到 " + trackType + " 轨道 " + trackIndex,
            "轨道序号是 1 基；先用 ppro_sequence_info 确认真实轨道数");
    }
    return result;
};

//: 在 QE 轨道里按 0 基序号取片段。
DSH.qe.item = function (qeTrack, index) {
    var names = ["getItemAt", "getClipAt", "getTrackItemAt"];
    var result = DSH.qe.call(qeTrack, names, [index],
        "QE 轨道没有 getItemAt/getClipAt；请在真机上确认该版本 QE 的片段访问方式");
    if (!result) {
        throw DSH.fail("NOT_FOUND", "QE 里找不到该轨道上第 " + index + " 个片段",
            "片段序号是 0 基；用 ppro_timeline_list_clips 取回最新 ref 后重试");
    }
    return result;
};

//: 公开 ref -> QE 片段对象。
DSH.qe.trackItem = function (sequence, ref) {
    var parsed = DSH.parseRef(ref);
    var qeSequence = DSH.qe.sequence(sequence);
    var qeTrack = DSH.qe.track(qeSequence, parsed.track_type, parsed.track_index);
    return {
        parsed: parsed,
        sequence: qeSequence,
        track: qeTrack,
        item: DSH.qe.item(qeTrack, parsed.index)
    };
};

//: 统计公开序列上某类轨道的片段总数（用于 QE 操作后的"是否真的发生了"校验）。
DSH.qe.publicClipCount = function (sequence, trackType) {
    var total = 0;
    var count = 0;
    try {
        count = DSH.trackCount(sequence, trackType);
    } catch (countError) {
        return null;
    }
    var i;
    for (i = 1; i <= count; i += 1) {
        var track = DSH.findTrack(sequence, trackType, i);
        if (track) { total += DSH.trackItems(track).length; }
    }
    return total;
};

//: 在指定时间点切分（QE）。只负责"发起调用"，是否真的切开了由调用方
//: 对比操作前后的公开片段数来校验——QE 静默失败是常态，不能把返回值当结论。
DSH.qe.razor = function (sequence, atSeconds, trackType, trackIndex) {
    DSH.qe.ensure();
    var qeSequence = DSH.qe.sequence(sequence);
    var targets = [];
    var types = [];
    if (trackIndex) {
        types[types.length] = trackType;
        targets[targets.length] = DSH.qe.track(qeSequence, trackType, trackIndex);
    } else {
        types = (trackType === "all") ? ["video", "audio"] : [trackType];
        var t;
        for (t = 0; t < types.length; t += 1) {
            var count = 0;
            try {
                count = DSH.trackCount(sequence, types[t]);
            } catch (countError) {
                count = 0;
            }
            var i;
            for (i = 1; i <= count; i += 1) {
                try {
                    targets[targets.length] = DSH.qe.track(qeSequence, types[t], i);
                } catch (trackError) {
                    // 该轨道在 QE 里取不到就跳过：不给整体增加失败面
                }
            }
        }
    }
    if (targets.length === 0) {
        throw DSH.fail("NOT_FOUND", "没有可切分的轨道",
            "传入 track_index 限定单条轨道，或确认序列上确实有轨道");
    }
    var names = ["razor", "razorAt", "split"];
    var i2;
    for (i2 = 0; i2 < targets.length; i2 += 1) {
        DSH.qe.callWithTime(targets[i2], names, atSeconds, null, null,
            "QE 轨道没有 razor/razorAt 方法，无法在指定时间点切分");
    }
    return targets.length;
};

//: 增加轨道（QE）。返回变化后的轨道数，由调用方校验。
DSH.qe.addTracks = function (sequence, trackType, position, name) {
    DSH.qe.ensure();
    var qeSequence = DSH.qe.sequence(sequence);
    var names = (trackType === "audio")
        ? ["addAudioTracks", "addAudioTrack", "addTracks"]
        : ["addVideoTracks", "addVideoTrack", "addTracks"];
    var args = [];
    if (position) { args[args.length] = position; }
    if (name) { args[args.length] = name; }
    DSH.qe.call(qeSequence, names, args,
        "QE 序列没有 addVideoTrack(s)/addAudioTrack(s)；该宿主版本无法脚本化新增轨道");
    return DSH.trackCount(sequence, trackType);
};

//: 删除轨道（QE）。
DSH.qe.removeTracks = function (sequence, trackType, trackIndex) {
    DSH.qe.ensure();
    var qeSequence = DSH.qe.sequence(sequence);
    var names = (trackType === "audio")
        ? ["removeAudioTracks", "removeAudioTrack", "removeTracks", "deleteTrack", "deleteAudioTrack"]
        : ["removeVideoTracks", "removeVideoTrack", "removeTracks", "deleteTrack", "deleteVideoTrack"];
    DSH.qe.callPositional(qeSequence, names, [trackIndex],
        "QE 序列没有 removeVideoTrack(s)/removeAudioTrack(s)/deleteTrack；"
            + "该宿主版本无法脚本化删除轨道");
    return DSH.trackCount(sequence, trackType);
};

//: 按位置参数直接调用（不需要时间参数时用它，如删除轨道）。
DSH.qe.callPositional = function (target, names, args, remedy) {
    var errors = [];
    var i;
    for (i = 0; i < names.length; i += 1) {
        var fn = DSH.api(target, names[i]);
        if (!fn) { continue; }
        try {
            return fn.apply(target, args ? args : []);
        } catch (callError) {
            errors[errors.length] = names[i] + ": " + DSH.errorMessage(callError);
        }
    }
    if (errors.length === 0) {
        throw DSH.fail("UNSUPPORTED_API", "QE 对象没有以下任何方法：" + names.join(" / "),
            remedy ? remedy : "该宿主版本的 QE 未提供此能力");
    }
    throw DSH.fail("HOST_ERROR", "QE 方法调用失败：" + errors.join(" | "),
        remedy ? remedy : "QE 行为随版本变化；请在真机上确认后重试");
};

//: 给 QE 片段施加效果（QE）。调用方需校验组件数变化。
DSH.qe.addVideoEffect = function (qeItem, effectName) {
    DSH.qe.ensure();
    return DSH.qe.call(qeItem, ["addVideoEffect", "addEffect", "applyEffect", "addVideoEffectByName"],
        [effectName],
        "QE 片段没有 addVideoEffect 等效果施加方法；该宿主版本无法脚本化施加效果");
};

DSH.qe.addTransition = function (qeItem, transitionName, atStart) {
    DSH.qe.ensure();
    var args = atStart
        ? [transitionName, 0]
        : [transitionName, 1];
    return DSH.qe.call(qeItem,
        ["addTransition", "addVideoTransition", "applyTransition"],
        args,
        "QE 片段没有 addTransition 等方法；该宿主版本无法脚本化添加转场");
};

//: QE 侧的效果/转场清单（候选名，无官方文档）。
DSH.qe.effectList = function (mediaType) {
    DSH.qe.ensure();
    var qeProject = DSH.qe.project();
    var names = (mediaType === "audio")
        ? ["getAudioEffectList", "getAudioEffects", "getAudioEffectNames"]
        : ["getVideoEffectList", "getVideoEffects", "getVideoEffectNames"];
    var result = DSH.qe.call(qeProject, names, null,
        "QE 工程对象没有 getVideoEffectList/getAudioEffectList；"
            + "该宿主版本无法通过脚本枚举效果");
    var out = [];
    var i;
    if (result && typeof result.length === "number") {
        for (i = 0; i < result.length; i += 1) {
            out[out.length] = String(result[i]);
        }
    }
    return out;
};

//: 工作区（QE 兜底路径）：公开 DOM 的 setWorkArea* 不可用时才用。
DSH.qe.setWorkArea = function (sequence, startSeconds, endSeconds) {
    DSH.qe.ensure();
    var qeSequence = DSH.qe.sequence(sequence);
    var inNames = ["setWorkAreaInPoint", "setWorkAreaInPointAsTime"];
    var outNames = ["setWorkAreaOutPoint", "setWorkAreaOutPointAsTime"];
    DSH.qe.callWithTime(qeSequence, inNames, startSeconds, null, null,
        "QE 序列没有 setWorkAreaInPoint");
    DSH.qe.callWithTime(qeSequence, outNames, endSeconds, null, null,
        "QE 序列没有 setWorkAreaOutPoint");
    return true;
};

//: QE 侧时间线选中项（只拿得到名字；公开 getSelection() 才是首选路径）。
DSH.qe.selectionNames = function (sequence) {
    DSH.qe.ensure();
    var qeSequence = DSH.qe.sequence(sequence);
    var result = DSH.qe.call(qeSequence, ["getSelection", "getSelectedItems"], null,
        "QE 序列没有 getSelection；无法读取当前选中项");
    var out = [];
    var i;
    if (result && typeof result.length === "number") {
        for (i = 0; i < result.length; i += 1) {
            var name = null;
            try {
                name = String(result[i].name);
            } catch (nameError) {
                name = null;
            }
            out[out.length] = name;
        }
    }
    return out;
};

//: 移动片段（QE 兜底路径）。
DSH.qe.moveClip = function (sequence, ref, toSeconds) {
    DSH.qe.ensure();
    var located = DSH.qe.trackItem(sequence, ref);
    var names = ["moveClipToTime", "moveToTime", "move", "setStartTime"];
    DSH.qe.callWithTime(located.item, names, toSeconds, null, null,
        "QE 片段没有 moveClipToTime/move 等方法；该宿主版本无法脚本化移动片段");
    return located;
};

//: 裁切（QE 兜底路径）。
DSH.qe.trimClip = function (sequence, ref, startSeconds, endSeconds) {
    DSH.qe.ensure();
    var located = DSH.qe.trackItem(sequence, ref);
    if (startSeconds !== null && typeof startSeconds !== "undefined") {
        DSH.qe.callWithTime(located.item,
            ["setStartTime", "trimStart", "setInPoint"], startSeconds, null, null,
            "QE 片段没有 setStartTime/trimStart；无法用 QE 调整片段起点");
    }
    if (endSeconds !== null && typeof endSeconds !== "undefined") {
        DSH.qe.callWithTime(located.item,
            ["setEndTime", "trimEnd", "setOutPoint"], endSeconds, null, null,
            "QE 片段没有 setEndTime/trimEnd；无法用 QE 调整片段终点");
    }
    return located;
};
