// =====================================================================
// ops/app.jsx —— 宿主与应用级处理器（契约 §5.3）
//   host.state          -> host_state
//   app.info            -> app_info
//   app.get_state       -> app_get_state
//   app.set_playhead    -> app_set_playhead
//
// 处理器签名统一为 function (args, ctx)，由 dispatch.jsx 通过 $.global 解析。
// =====================================================================

if (typeof DSH === "undefined") { var DSH = {}; }

//: 宿主界面语言。公开 DOM 没有文档化的语言接口，所以按"能问到就问、
//: 问不到就退回 ExtendScript 的 locale、再不行返回 null"处理（不猜）。
DSH.appLanguage = function () {
    try {
        if (typeof app !== "undefined" && app && app.properties) {
            var getProperty = DSH.api(app.properties, "getProperty");
            if (getProperty) {
                var keys = ["BE.Prefs.Language", "Language", "lang"];
                var i;
                for (i = 0; i < keys.length; i += 1) {
                    try {
                        var value = getProperty.call(app.properties, keys[i]);
                        if (value !== null && typeof value !== "undefined" && value !== "") {
                            return String(value);
                        }
                    } catch (propertyError) {
                        // 该 key 不存在就试下一个
                    }
                }
            }
        }
    } catch (propertiesError) {
        // 落到 locale 兜底
    }
    try {
        if (typeof $ !== "undefined" && $.locale) { return String($.locale); }
    } catch (localeError) {
        return null;
    }
    return null;
};

//: 当前时间线选中项。**优先公开 DOM**：Sequence.getSelection() 是文档化接口；
//: 只有在它不可用时才考虑 QE，且 QE 未启用时返回空数组 + warning（契约要求）。
DSH.sequenceSelection = function (sequence) {
    var result = { items: [], warning: null };
    var getSelection = DSH.api(sequence, "getSelection");
    if (getSelection) {
        try {
            var raw = getSelection.call(sequence);
            var count = DSH.collectionCount(raw);
            var i;
            for (i = 0; i < count; i += 1) {
                var item = DSH.collectionAt(raw, i);
                if (!item) { continue; }
                var located = DSH.locateTrackItem(sequence, item);
                result.items[result.items.length] = {
                    ref: located ? located.ref : null,
                    name: DSH.hasProperty(item, "name") ? String(item.name) : null,
                    track_type: located ? located.track_type : null,
                    track_index: located ? located.track_index : null,
                    index: located ? located.index : null,
                    start_s: DSH.trackItemStartSeconds(item),
                    end_s: DSH.trackItemEndSeconds(item)
                };
            }
            if (result.items.length === 0) {
                result.warning = "当前时间线没有选中项";
            }
            return result;
        } catch (selectionError) {
            result.items = [];
            result.warning = "公开接口 getSelection() 不可用：" + DSH.errorMessage(selectionError);
        }
    }
    if (!DSH.qeEnabled()) {
        result.warning = (result.warning ? result.warning + "；" : "")
            + "宿主未提供可用的序列选中项接口，且 QE 未启用：selection 返回空数组";
        return result;
    }
    try {
        var names = DSH.qe.selectionNames(sequence);
        var n;
        for (n = 0; n < names.length; n += 1) {
            result.items[result.items.length] = {
                ref: null, name: names[n], track_type: null, track_index: null,
                index: null, start_s: null, end_s: null
            };
        }
        result.warning = "选中项来自未公开的 QE 接口，只有名称可用（ref 等字段为 null）";
    } catch (qeError) {
        result.items = [];
        result.warning = "QE 读取选中项失败：" + DSH.errorMessage(qeError);
    }
    return result;
};

// ---- host.state ---------------------------------------------------------

function host_state(args, ctx) {
    var snapshot = DSH.hostSnapshot();
    var activeName = null;
    var playhead = null;
    if (snapshot.has_project === true) {
        try {
            var sequence = DSH.findSequence(null);
            if (sequence) {
                activeName = DSH.hasProperty(sequence, "name") ? String(sequence.name) : null;
                playhead = DSH.sequencePlayheadSeconds(sequence);
            }
        } catch (stateError) {
            // 状态读取是诊断用途：读不到就留 null，不把整条命令弄失败
            activeName = null;
            playhead = null;
        }
    }
    return {
        has_project: snapshot.has_project === true,
        project_path: snapshot.project_path,
        dirty: snapshot.dirty,
        active_sequence: activeName,
        playhead_s: playhead,
        app_version: snapshot.app_version
    };
}

// ---- app.info -----------------------------------------------------------

function app_info(args, ctx) {
    var snapshot = DSH.hostSnapshot();
    var projectName = null;
    var projectId = null;
    var build = null;
    try {
        if (typeof app !== "undefined" && app && app.build) { build = String(app.build); }
    } catch (buildError) {
        build = null;
    }
    if (snapshot.has_project === true) {
        try {
            projectName = app.project.name ? String(app.project.name) : null;
        } catch (nameError) {
            projectName = null;
        }
        try {
            projectId = DSH.hasProperty(app.project, "documentID") ? String(app.project.documentID) : null;
        } catch (idError) {
            projectId = null;
        }
    }
    return {
        app_version: snapshot.app_version,
        app_language: DSH.appLanguage(),
        has_project: snapshot.has_project === true,
        project_name: projectName,
        project_path: snapshot.project_path,
        dirty: snapshot.dirty,
        project_id: projectId,
        // 契约未要求，但排查多版本问题时极有用（多一个键不破坏客户端解析）
        app_build: build
    };
}

// ---- app.get_state ------------------------------------------------------

function app_get_state(args, ctx) {
    var includeSelection = DSH.argBool(args, "include_selection", true);
    var sequence = DSH.requireSequence(null);
    var inOut = DSH.sequenceInOut(sequence);
    var playhead = DSH.sequencePlayheadSeconds(sequence);
    var selection = [];
    var warnings = [];
    if (includeSelection) {
        var collected = DSH.sequenceSelection(sequence);
        selection = collected.items;
        if (collected.warning) { warnings[warnings.length] = collected.warning; }
    }
    return {
        active_sequence: DSH.hasProperty(sequence, "name") ? String(sequence.name) : null,
        playhead_s: playhead,
        in_point_s: inOut.in_s,
        out_point_s: inOut.out_s,
        selection: selection,
        warnings: warnings
    };
}

// ---- app.set_playhead ---------------------------------------------------

function app_set_playhead(args, ctx) {
    DSH.beginChecks(ctx);
    var seconds = DSH.argNumber(args, "seconds", true, null);
    if (seconds < 0) {
        throw DSH.argReport("seconds", "不能是负数（时间相对序列起点）");
    }
    var sequence = DSH.requireSequence(null);
    var name = DSH.hasProperty(sequence, "name") ? String(sequence.name) : "(活动序列)";
    var before = DSH.sequencePlayheadSeconds(sequence);
    var tolerance = DSH.frameToleranceSeconds(sequence);
    DSH.note("playhead_readable", before !== null,
        before !== null ? null : "宿主未提供可读的播放头位置（写入后无法读回校验）");

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["把序列 " + name + " 的播放头移动到 " + seconds + "s"],
            { playhead_s: seconds, verified: null });
    }

    var target = seconds;
    var toleranceValue = tolerance;
    DSH.applyWithVariants(sequence, "setPlayerPosition",
        DSH.timeArgVariants(seconds),
        function () {
            var after = DSH.sequencePlayheadSeconds(sequence);
            if (after === null) { return before === null; }  // 读不回来时无从校验
            return DSH.nearlyEqual(after, target, toleranceValue);
        },
        "该宿主版本无法脚本化设置播放头位置：请在时间线面板手动移动 CTI",
        "Sequence");

    var finalPosition = DSH.sequencePlayheadSeconds(sequence);
    return {
        playhead_s: finalPosition,
        // 宿主读不回播放头时明确标"未校验"，不让客户端误以为已验证
        verified: finalPosition !== null
    };
}
