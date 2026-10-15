// =====================================================================
// ops/timeline.jsx —— 时间线处理器（契约 §5.3）
//   timeline.list_clips    -> timeline_list_clips
//   timeline.clip_info     -> timeline_clip_info
//   timeline.insert_clip   -> timeline_insert_clip
//   timeline.overwrite_clip-> timeline_overwrite_clip
//   timeline.move_clip     -> timeline_move_clip
//   timeline.trim_clip     -> timeline_trim_clip
//   timeline.remove_clip   -> timeline_remove_clip
//   timeline.razor         -> timeline_razor          （qe：未公开接口）
//   timeline.add_track     -> timeline_add_track      （public_or_qe）
//   timeline.remove_track  -> timeline_remove_track   （public_or_qe）
//   timeline.add_marker    -> timeline_add_marker
//   timeline.list_markers  -> timeline_list_markers
//
// 公开 API 依据（ppro-scripting 文档）：
//   Track.insertClip(item, timeInTicks, vTrackIndex, aTrackIndex)、
//   Track.overwriteClip(item, timeInTicks)、Track.clips、
//   TrackItem.start/end/inPoint/outPoint（Time 对象，read/write）、
//   TrackItem.remove(inRipple, inAlignToVideo)、TrackItem.move(newInPoint)、
//   TrackItem.components/getSpeed/isSelected/isAdjustmentLayer、
//   MarkerCollection.createMarker(timeInSeconds)/getFirstMarker/getNextMarker、
//   Marker.name/comments/start/end/type + setTypeAs* 方法
//
// 关于"写后读回"：宿主会按帧对齐时间，所以所有写入都用 DSH.applyWithVariants /
// DSH.setPropertyWithVariants 校验（容差一帧），校验不过就报错，不假装成功。
// =====================================================================

if (typeof DSH === "undefined") { var DSH = {}; }

// ---- 公共小工具 ---------------------------------------------------------

//: 契约的 track_type 过滤：video / audio / all。
function dsh_trackTypes(trackType) {
    if (trackType === "audio") { return ["audio"]; }
    if (trackType === "video") { return ["video"]; }
    return ["video", "audio"];
}

function dsh_itemMediaPath(projectItem) {
    if (!projectItem) { return null; }
    var getMediaPath = DSH.api(projectItem, "getMediaPath");
    if (!getMediaPath) { return null; }
    try {
        var path = getMediaPath.call(projectItem);
        return (path && path !== 0) ? String(path) : null;
    } catch (mediaPathError) {
        return null;
    }
}

function dsh_clipMediaPath(item) {
    var projectItem = null;
    try {
        projectItem = item ? item.projectItem : null;
    } catch (projectItemError) {
        projectItem = null;
    }
    return dsh_itemMediaPath(projectItem);
}

function dsh_clipEntry(item, trackType, trackIndex, index) {
    return {
        ref: DSH.makeRef(trackType, trackIndex, index),
        track_type: trackType,
        track_index: trackIndex,
        index: index,
        name: DSH.hasProperty(item, "name") ? String(item.name) : null,
        start_s: DSH.trackItemStartSeconds(item),
        end_s: DSH.trackItemEndSeconds(item),
        duration_s: DSH.timeToSeconds(item.duration),
        in_s: DSH.timeToSeconds(item.inPoint),
        out_s: DSH.timeToSeconds(item.outPoint),
        media_path: dsh_clipMediaPath(item),
        type: DSH.trackItemTypeName(item),
        enabled: DSH.trackItemEnabled(item),
        selected: DSH.trackItemSelected(item)
    };
}

//: 在指定轨道上按起点时间找片段（容差一帧）。用于"写后读回"确认落点。
function dsh_findClipAt(track, seconds, tolerance) {
    var items = DSH.trackItems(track);
    var i;
    for (i = 0; i < items.length; i += 1) {
        var start = DSH.trackItemStartSeconds(items[i]);
        if (DSH.nearlyEqual(start, seconds, tolerance)) { return items[i]; }
    }
    return null;
}

function dsh_trackLabel(trackType, trackIndex) {
    return DSH.trackLetter(trackType) + trackIndex;
}

//: 序列上全部轨道的片段总数（用于 QE 操作前后对比）。
function dsh_totalClipCount(sequence, trackType) {
    var types = dsh_trackTypes(trackType);
    var total = 0;
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
            var track = DSH.findTrack(sequence, types[t], i);
            if (track) { total += DSH.trackItems(track).length; }
        }
    }
    return total;
}

// ---- timeline.list_clips -----------------------------------------------

function timeline_list_clips(args, ctx) {
    var selector = DSH.argString(args, "sequence", false, null);
    var trackType = DSH.argEnum(args, "track_type", ["video", "audio", "all"], "all");
    var trackIndex = DSH.argInt(args, "track_index", false, null);
    if (trackIndex !== null && trackIndex < 1) {
        throw DSH.argReport("track_index", "轨道序号是 1 基，不能小于 1");
    }
    var sequence = DSH.requireSequence(selector);
    var types = dsh_trackTypes(trackType);
    var clips = [];
    var t;
    for (t = 0; t < types.length; t += 1) {
        var count = DSH.trackCount(sequence, types[t]);
        var i;
        for (i = 1; i <= count; i += 1) {
            if (trackIndex !== null && i !== trackIndex) { continue; }
            var track = DSH.findTrack(sequence, types[t], i);
            if (!track) { continue; }
            var items = DSH.trackItems(track);
            var j;
            for (j = 0; j < items.length; j += 1) {
                clips[clips.length] = dsh_clipEntry(items[j], types[t], i, j);
            }
        }
    }
    return { clips: clips };
}

// ---- timeline.clip_info -----------------------------------------------

function timeline_clip_info(args, ctx) {
    var selector = DSH.argString(args, "sequence", false, null);
    var ref = DSH.argString(args, "ref", true, null);
    var sequence = DSH.requireSequence(selector);
    var located = DSH.requireTrackItem(sequence, ref);
    var item = located.item;
    var entry = dsh_clipEntry(item, located.track_type, located.track_index, located.index);
    var componentCount = null;
    try {
        if (item.components) { componentCount = DSH.collectionCount(item.components); }
    } catch (componentsError) {
        componentCount = null;
    }
    var getSpeed = DSH.api(item, "getSpeed");
    var speed = null;
    if (getSpeed) {
        try {
            speed = Number(getSpeed.call(item));
            if (!isFinite(speed)) { speed = null; }
        } catch (speedError) {
            speed = null;
        }
    }
    return {
        ref: located.ref,
        name: entry.name,
        type: entry.type,
        media_path: entry.media_path,
        start_s: entry.start_s,
        end_s: entry.end_s,
        duration_s: entry.duration_s,
        in_s: entry.in_s,
        out_s: entry.out_s,
        speed: speed,
        enabled: entry.enabled,
        selected: entry.selected,
        component_count: componentCount
    };
}

// ---- timeline.insert_clip ---------------------------------------------

function timeline_insert_clip(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var itemRef = DSH.argString(args, "item", true, null);
    var trackType = DSH.argEnum(args, "track_type", ["video", "audio"], "video");
    var trackIndex = DSH.argInt(args, "track_index", false, 1);
    var atSeconds = DSH.argNumber(args, "at_s", false, 0);
    if (atSeconds < 0) { throw DSH.argReport("at_s", "不能是负数"); }
    if (trackIndex < 1) { throw DSH.argReport("track_index", "轨道序号是 1 基，不能小于 1"); }

    var sequence = DSH.requireSequence(selector);
    var projectItem = DSH.requireProjectItem(itemRef);
    var track = DSH.requireTrack(sequence, trackType, trackIndex);
    var insertClip = DSH.requireApi(track, "insertClip",
        "该宿主版本的 Track 没有 insertClip；无法脚本化插入片段", "Track");
    DSH.note("insert_target_time_free", dsh_findClipAt(track, atSeconds, 0) === null,
        "同一轨道上是否已有同起点片段（只看起点是否相同）");
    DSH.note("project_item_has_media_path", dsh_itemMediaPath(projectItem) !== null,
        DSH.hasProperty(projectItem, "name") ? String(projectItem.name) : null);
    var tolerance = DSH.frameToleranceSeconds(sequence);
    var itemName = DSH.hasProperty(projectItem, "name") ? String(projectItem.name) : itemRef;

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["在 " + dsh_trackLabel(trackType, trackIndex) + " 轨道的 " + atSeconds
                + "s 处插入片段 " + itemName],
            { ref: null, start_s: atSeconds, end_s: null });
    }

    var ticksText = String(DSH.ticks(atSeconds));
    var variants = [
        { kind: "四参数(ticks, vTrack, aTrack)", args: [projectItem, ticksText, trackIndex - 1, trackIndex - 1] },
        { kind: "两参数(ticks)", args: [projectItem, ticksText] },
        { kind: "两参数(Time 对象)", args: [projectItem, DSH.makeTime(atSeconds)] },
        { kind: "两参数(秒数)", args: [projectItem, Number(atSeconds)] }
    ];
    DSH.applyWithVariants(track, "insertClip", variants,
        function () { return dsh_findClipAt(track, atSeconds, tolerance) !== null; },
        "宿主没有接受任何一种 insertClip 参数形式；请在时间线面板手动插入", "Track");

    var placed = dsh_findClipAt(track, atSeconds, tolerance);
    if (!placed) {
        throw DSH.fail("HOST_ERROR", "insertClip 之后在该轨道上找不到起点为 " + atSeconds + "s 的片段",
            "宿主可能把片段放到了别的时间点（吸附/插入点规则）；用 ppro_timeline_list_clips 查看实际结果");
    }
    var located = DSH.locateTrackItem(sequence, placed);
    var entry = dsh_clipEntry(placed, trackType, trackIndex, located ? located.index : 0);
    return { ref: entry.ref, start_s: entry.start_s, end_s: entry.end_s };
}

// ---- timeline.overwrite_clip ------------------------------------------

function timeline_overwrite_clip(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var itemRef = DSH.argString(args, "item", true, null);
    var trackType = DSH.argEnum(args, "track_type", ["video", "audio"], "video");
    var trackIndex = DSH.argInt(args, "track_index", false, 1);
    var atSeconds = DSH.argNumber(args, "at_s", false, 0);
    if (atSeconds < 0) { throw DSH.argReport("at_s", "不能是负数"); }
    if (trackIndex < 1) { throw DSH.argReport("track_index", "轨道序号是 1 基，不能小于 1"); }

    var sequence = DSH.requireSequence(selector);
    var projectItem = DSH.requireProjectItem(itemRef);
    var track = DSH.requireTrack(sequence, trackType, trackIndex);
    DSH.requireApi(track, "overwriteClip",
        "该宿主版本的 Track 没有 overwriteClip；无法脚本化覆盖写入", "Track");
    var tolerance = DSH.frameToleranceSeconds(sequence);
    var itemName = DSH.hasProperty(projectItem, "name") ? String(projectItem.name) : itemRef;

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["在 " + dsh_trackLabel(trackType, trackIndex) + " 轨道的 " + atSeconds
                + "s 处覆盖写入片段 " + itemName + "（该范围内的原有内容会被替换）"],
            { ref: null, start_s: atSeconds, end_s: null });
    }

    var ticksText = String(DSH.ticks(atSeconds));
    var variants = [
        { kind: "两参数(ticks)", args: [projectItem, ticksText] },
        { kind: "两参数(Time 对象)", args: [projectItem, DSH.makeTime(atSeconds)] },
        { kind: "两参数(秒数)", args: [projectItem, Number(atSeconds)] }
    ];
    DSH.applyWithVariants(track, "overwriteClip", variants,
        function () { return dsh_findClipAt(track, atSeconds, tolerance) !== null; },
        "宿主没有接受任何一种 overwriteClip 参数形式；请在时间线面板手动覆盖写入", "Track");

    var placed = dsh_findClipAt(track, atSeconds, tolerance);
    if (!placed) {
        throw DSH.fail("HOST_ERROR", "overwriteClip 之后在该轨道上找不到起点为 " + atSeconds + "s 的片段",
            "用 ppro_timeline_list_clips 查看实际结果（宿主可能吸附到了别的帧）");
    }
    var located = DSH.locateTrackItem(sequence, placed);
    var entry = dsh_clipEntry(placed, trackType, trackIndex, located ? located.index : 0);
    return { ref: entry.ref, start_s: entry.start_s, end_s: entry.end_s };
}

// ---- timeline.move_clip -----------------------------------------------

//: 公开 DOM 的复合移动：摘下源片段 -> 在目标轨道/时间点覆盖写入 -> 尽量恢复源 in/out。
//: 为什么用复合路径：公开 API 没有"把片段移动到另一条轨道"的原语，
//: 而 TrackItem.move() 的语义（新时间点 vs 位移量）在不同版本上不一致。
function dsh_moveClipByReinsert(sequence, item, destType, destIndex, toSeconds, tolerance) {
    var projectItem = null;
    try {
        projectItem = item.projectItem;
    } catch (projectItemError) {
        projectItem = null;
    }
    if (!projectItem) {
        throw DSH.fail("UNSUPPORTED_API", "该片段没有可用的 projectItem，无法用公开 API 移动",
            "改用 QE 路径（需 extendscript.enable_qe=true），或在线时间线面板手动移动");
    }
    var inSeconds = DSH.timeToSeconds(item.inPoint);
    var outSeconds = DSH.timeToSeconds(item.outPoint);
    var remove = DSH.api(item, "remove");
    if (!remove) {
        throw DSH.fail("UNSUPPORTED_API", "该 TrackItem 没有 remove()，无法用公开 API 移动",
            "改用 QE 路径（需 extendscript.enable_qe=true），或在时间线面板手动移动");
    }
    var destTrack = DSH.requireTrack(sequence, destType, destIndex);
    DSH.requireApi(destTrack, "overwriteClip",
        "目标轨道没有 overwriteClip，无法用公开 API 移动", "Track");

    remove.call(item, 0, 0);
    DSH.applyWithVariants(destTrack, "overwriteClip",
        [
            { kind: "两参数(ticks)", args: [projectItem, String(DSH.ticks(toSeconds))] },
            { kind: "两参数(Time 对象)", args: [projectItem, DSH.makeTime(toSeconds)] },
            { kind: "两参数(秒数)", args: [projectItem, Number(toSeconds)] }
        ],
        function () { return dsh_findClipAt(destTrack, toSeconds, tolerance) !== null; },
        "目标轨道拒绝了 overwriteClip；片段已从原轨道摘除，请用 ppro_timeline_list_clips 检查现状",
        "Track");

    var placed = dsh_findClipAt(destTrack, toSeconds, tolerance);
    if (!placed) {
        throw DSH.fail("HOST_ERROR", "移动后找不到落点片段（期望 " + toSeconds + "s）",
            "片段可能已被摘除但未重新写入：请用 ppro_timeline_list_clips 检查并手动恢复");
    }
    var result = { item: placed, trim_restored: true, trim_note: null };
    if (inSeconds !== null) {
        var targetIn = inSeconds;
        try {
            DSH.setPropertyWithVariants(placed, "inPoint", DSH.timeArgVariants(inSeconds),
                function () {
                    return DSH.nearlyEqual(DSH.timeToSeconds(placed.inPoint), targetIn, tolerance);
                }, "恢复源入点", "TrackItem");
        } catch (inPointError) {
            result.trim_restored = false;
            result.trim_note = "片段已移动，但源入点未能恢复：" + DSH.errorMessage(inPointError);
        }
    }
    if (outSeconds !== null) {
        var targetOut = outSeconds;
        try {
            DSH.setPropertyWithVariants(placed, "outPoint", DSH.timeArgVariants(outSeconds),
                function () {
                    return DSH.nearlyEqual(DSH.timeToSeconds(placed.outPoint), targetOut, tolerance);
                }, "恢复源出点", "TrackItem");
        } catch (outPointError) {
            result.trim_restored = false;
            result.trim_note = (result.trim_note ? result.trim_note + "；" : "")
                + "片段已移动，但源出点未能恢复：" + DSH.errorMessage(outPointError);
        }
    }
    return result;
}

function timeline_move_clip(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var ref = DSH.argString(args, "ref", true, null);
    var toType = DSH.argEnum(args, "to_track_type", ["video", "audio"], null);
    var toIndex = DSH.argInt(args, "to_track_index", false, null);
    var toSeconds = DSH.argNumber(args, "to_s", true, null);
    if (toSeconds < 0) { throw DSH.argReport("to_s", "不能是负数"); }
    if (toIndex !== null && toIndex < 1) { throw DSH.argReport("to_track_index", "轨道序号是 1 基"); }

    var sequence = DSH.requireSequence(selector);
    var located = DSH.requireTrackItem(sequence, ref);
    var destType = toType ? toType : located.track_type;
    var destIndex = toIndex ? toIndex : located.track_index;
    DSH.requireTrack(sequence, destType, destIndex);
    var tolerance = DSH.frameToleranceSeconds(sequence);
    var currentStart = DSH.trackItemStartSeconds(located.item);
    var sameTrack = (destType === located.track_type) && (destIndex === located.track_index);
    DSH.note("target_time_differs", !DSH.nearlyEqual(currentStart, toSeconds, tolerance),
        "当前起点 " + currentStart + "s -> 目标 " + toSeconds + "s");

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["把片段 " + located.ref + " 从 " + dsh_trackLabel(located.track_type, located.track_index)
                + " 移动到 " + dsh_trackLabel(destType, destIndex) + " 的 " + toSeconds + "s"],
            { ref: located.ref, start_s: toSeconds, end_s: null });
    }

    var failures = [];

    // 路径 1：同轨道且宿主提供 TrackItem.move()（文档化，按位移量解释）
    if (sameTrack) {
        var moveFn = DSH.api(located.item, "move");
        if (moveFn) {
            var delta = toSeconds - (currentStart === null ? 0 : currentStart);
            try {
                DSH.applyWithVariants(located.item, "move",
                    [
                        { kind: "Time 对象(位移量)", args: [DSH.makeTime(delta)] },
                        { kind: "秒数(位移量)", args: [Number(delta)] },
                        { kind: "tick 字符串(位移量)", args: [String(DSH.ticks(delta))] }
                    ],
                    function () {
                        var now = DSH.trackItemStartSeconds(located.item);
                        return DSH.nearlyEqual(now, toSeconds, tolerance);
                    },
                    "TrackItem.move() 不接受位移量语义", "TrackItem");
                return {
                    ref: located.ref,
                    start_s: DSH.trackItemStartSeconds(located.item),
                    end_s: DSH.trackItemEndSeconds(located.item),
                    path_used: "trackitem_move"
                };
            } catch (moveError) {
                failures[failures.length] = "TrackItem.move：" + DSH.errorMessage(moveError);
            }
        } else {
            failures[failures.length] = "TrackItem.move：宿主未提供该 API";
        }
    } else {
        failures[failures.length] = "跨轨道移动：公开 DOM 没有原语，走摘除+重写";
    }

    // 路径 2：公开 DOM 复合路径（摘除 + 目标轨道覆盖写入 + 恢复 in/out）
    try {
        var placed = dsh_moveClipByReinsert(sequence, located.item, destType, destIndex,
            toSeconds, tolerance);
        var newRef = DSH.locateTrackItem(sequence, placed.item);
        return {
            ref: newRef ? newRef.ref : null,
            start_s: DSH.trackItemStartSeconds(placed.item),
            end_s: DSH.trackItemEndSeconds(placed.item),
            path_used: "remove_then_overwrite",
            trim_restored: placed.trim_restored,
            warnings: placed.trim_restored ? [] : [placed.trim_note]
        };
    } catch (reinsertError) {
        failures[failures.length] = "摘除+重写：" + DSH.errorMessage(reinsertError);
    }

    // 路径 3：QE 兜底（仅在 enable_qe 下）
    if (DSH.qeEnabled()) {
        try {
            DSH.qe.moveClip(sequence, located.ref, toSeconds);
            var reread = DSH.locateTrackItem(sequence, located.item);
            var after = reread ? DSH.trackItemStartSeconds(located.item) : null;
            if (!DSH.nearlyEqual(after, toSeconds, tolerance)) {
                throw DSH.fail("HOST_ERROR", "QE 移动后起点仍是 " + after + "s（未生效）",
                    "QE 静默失败很常见；请在时间线面板手动移动");
            }
            return {
                ref: located.ref, start_s: after,
                end_s: DSH.trackItemEndSeconds(located.item), path_used: "qe"
            };
        } catch (qeError) {
            failures[failures.length] = "QE：" + DSH.errorMessage(qeError);
        }
    } else {
        failures[failures.length] = "QE：未启用（extendscript.enable_qe=false）";
    }

    throw DSH.fail("UNSUPPORTED_API", "无法移动片段 " + located.ref,
        "公开 DOM 与（可用的）QE 路径都没能完成移动；请在时间线面板手动移动，"
            + "或启用 extendscript.enable_qe 后重试",
        { detail: failures.join(" | ") });
}

// ---- timeline.trim_clip -----------------------------------------------

function timeline_trim_clip(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var ref = DSH.argString(args, "ref", true, null);
    var startSeconds = DSH.argNumber(args, "start_s", false, null);
    var endSeconds = DSH.argNumber(args, "end_s", false, null);
    if (startSeconds === null && endSeconds === null) {
        throw DSH.argReport("start_s/end_s", "至少要给出其中一个");
    }
    if (startSeconds !== null && startSeconds < 0) { throw DSH.argReport("start_s", "不能是负数"); }
    if (endSeconds !== null && endSeconds < 0) { throw DSH.argReport("end_s", "不能是负数"); }

    var sequence = DSH.requireSequence(selector);
    var located = DSH.requireTrackItem(sequence, ref);
    var currentStart = DSH.trackItemStartSeconds(located.item);
    var currentEnd = DSH.trackItemEndSeconds(located.item);
    var targetStart = (startSeconds === null) ? currentStart : startSeconds;
    var targetEnd = (endSeconds === null) ? currentEnd : endSeconds;
    if (targetStart === null || targetEnd === null) {
        throw DSH.fail("UNSUPPORTED_API", "无法读取该片段的现有起止时间，不能安全地裁切",
            "改用 ref 重新列出一遍（ppro_timeline_list_clips）后再试；或在时间线面板手动裁切");
    }
    if (targetStart >= targetEnd) {
        throw DSH.argReport("start_s/end_s",
            "裁切后的起点必须早于终点（" + targetStart + " >= " + targetEnd + "）");
    }
    var tolerance = DSH.frameToleranceSeconds(sequence);
    DSH.note("trim_range_valid", true,
        "现有 " + currentStart + "s - " + currentEnd + "s -> 目标 " + targetStart + "s - " + targetEnd + "s");

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["把片段 " + located.ref + " 的起止时间裁切为 " + targetStart + "s - " + targetEnd + "s"],
            { ref: located.ref, start_s: targetStart, end_s: targetEnd });
    }

    var failures = [];
    if (startSeconds !== null) {
        try {
            DSH.setPropertyWithVariants(located.item, "start", DSH.timeArgVariants(startSeconds),
                function () {
                    return DSH.nearlyEqual(DSH.trackItemStartSeconds(located.item), startSeconds, tolerance);
                }, "该宿主不接受写 TrackItem.start", "TrackItem");
        } catch (startError) {
            failures[failures.length] = "start：" + DSH.errorMessage(startError);
        }
    }
    if (endSeconds !== null) {
        try {
            DSH.setPropertyWithVariants(located.item, "end", DSH.timeArgVariants(endSeconds),
                function () {
                    return DSH.nearlyEqual(DSH.trackItemEndSeconds(located.item), endSeconds, tolerance);
                }, "该宿主不接受写 TrackItem.end", "TrackItem");
        } catch (endError) {
            failures[failures.length] = "end：" + DSH.errorMessage(endError);
        }
    }

    var measuredStart = DSH.trackItemStartSeconds(located.item);
    var measuredEnd = DSH.trackItemEndSeconds(located.item);
    var okStart = (startSeconds === null) || DSH.nearlyEqual(measuredStart, startSeconds, tolerance);
    var okEnd = (endSeconds === null) || DSH.nearlyEqual(measuredEnd, endSeconds, tolerance);

    if (!okStart || !okEnd) {
        if (DSH.qeEnabled()) {
            try {
                DSH.qe.trimClip(sequence, located.ref, startSeconds, endSeconds);
                measuredStart = DSH.trackItemStartSeconds(located.item);
                measuredEnd = DSH.trackItemEndSeconds(located.item);
                okStart = (startSeconds === null) || DSH.nearlyEqual(measuredStart, startSeconds, tolerance);
                okEnd = (endSeconds === null) || DSH.nearlyEqual(measuredEnd, endSeconds, tolerance);
                if (okStart && okEnd) {
                    return { ref: located.ref, start_s: measuredStart, end_s: measuredEnd, path_used: "qe" };
                }
                failures[failures.length] = "QE：读回值仍不一致";
            } catch (qeError) {
                failures[failures.length] = "QE：" + DSH.errorMessage(qeError);
            }
        } else {
            failures[failures.length] = "QE：未启用（extendscript.enable_qe=false）";
        }
        throw DSH.fail("UNSUPPORTED_API",
            "裁切未生效：期望 " + targetStart + "s - " + targetEnd + "s，实际 "
                + measuredStart + "s - " + measuredEnd + "s",
            "宿主可能按帧对齐或拒绝了该范围；请用 ppro_timeline_list_clips 确认现状，"
                + "或启用 extendscript.enable_qe 后重试",
            { detail: failures.join(" | ") });
    }
    return {
        ref: located.ref, start_s: measuredStart, end_s: measuredEnd, path_used: "public",
        in_s: DSH.timeToSeconds(located.item.inPoint),
        out_s: DSH.timeToSeconds(located.item.outPoint)
    };
}

// ---- timeline.remove_clip ---------------------------------------------

function timeline_remove_clip(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var ref = DSH.argString(args, "ref", true, null);
    var ripple = DSH.argBool(args, "ripple", false);
    var sequence = DSH.requireSequence(selector);
    var located = DSH.requireTrackItem(sequence, ref);
    var removeFn = DSH.api(located.item, "remove");
    DSH.note("track_item_removable", !!removeFn,
        removeFn ? null : "TrackItem 没有 remove()");
    if (!removeFn) {
        throw DSH.fail("UNSUPPORTED_API", "该宿主的 TrackItem 没有 remove()，无法删除片段",
            "该宿主版本不支持脚本化删除片段；请在时间线面板手动删除");
    }

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["从 " + dsh_trackLabel(located.track_type, located.track_index) + " 删除片段 "
                + located.ref + (ripple ? "（波纹删除：后续片段左移）" : "（不波纹）")],
            { removed: null, ref: located.ref });
    }

    var before = DSH.trackItems(located.track).length;
    try {
        removeFn.call(located.item, ripple ? 1 : 0, 0);
    } catch (removeError) {
        throw DSH.fail("HOST_ERROR", "TrackItem.remove() 调用失败：" + DSH.errorMessage(removeError),
            "确认时间线没有处于特殊编辑状态（如正在拖动）；必要时重试");
    }
    var after = DSH.trackItems(located.track).length;
    if (after >= before) {
        throw DSH.fail("HOST_ERROR",
            "remove() 之后该轨道的片段数没有减少（" + before + " -> " + after + "）",
            "宿主可能拒绝了删除；请用 ppro_timeline_list_clips 确认现状");
    }
    return { removed: true, ref: located.ref };
}

// ---- timeline.razor（QE） --------------------------------------------

function timeline_razor(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var atSeconds = DSH.argNumber(args, "at_s", true, null);
    var trackType = DSH.argEnum(args, "track_type", ["video", "audio", "all"], "all");
    var trackIndex = DSH.argInt(args, "track_index", false, null);
    if (atSeconds < 0) { throw DSH.argReport("at_s", "不能是负数"); }
    if (trackIndex !== null && trackIndex < 1) { throw DSH.argReport("track_index", "轨道序号是 1 基"); }
    var sequence = DSH.requireSequence(selector);
    var types = dsh_trackTypes(trackType);
    var labels = [];
    var t;
    for (t = 0; t < types.length; t += 1) {
        if (trackIndex !== null) {
            DSH.requireTrack(sequence, types[t], trackIndex);
            labels[labels.length] = dsh_trackLabel(types[t], trackIndex);
        } else {
            var count = DSH.trackCount(sequence, types[t]);
            var i;
            for (i = 1; i <= count; i += 1) { labels[labels.length] = dsh_trackLabel(types[t], i); }
        }
    }
    if (labels.length === 0) {
        throw DSH.fail("NOT_FOUND", "序列上没有可切分的轨道",
            "先确认序列上有视频/音频轨道（ppro_sequence_info）");
    }

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["在 " + atSeconds + "s 处切分 " + labels.join("/") + "（QE 未公开接口，有宿主崩溃风险）"],
            { razored_at_s: atSeconds, tracks: labels });
    }

    var before = dsh_totalClipCount(sequence, trackType);
    DSH.qe.razor(sequence, atSeconds, trackType, trackIndex);
    var after = dsh_totalClipCount(sequence, trackType);
    if (after <= before) {
        throw DSH.fail("HOST_ERROR",
            "QE 切分调用后片段数没有增加（" + before + " -> " + after + "），切分未生效",
            "QE 静默失败很常见：确认切分时间点落在某个片段内部（片段两侧端点切不开）；"
                + "QE 是未公开接口，行为随版本变化");
    }
    return { razored_at_s: atSeconds, tracks: labels };
}

// ---- timeline.add_track（public_or_qe） ------------------------------

function timeline_add_track(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var trackType = DSH.argEnum(args, "track_type", ["video", "audio"], "video");
    var position = DSH.argInt(args, "position", false, null);
    var trackName = DSH.argString(args, "name", false, null);
    var sequence = DSH.requireSequence(selector);
    var before = DSH.trackCount(sequence, trackType);
    if (position !== null && (position < 1 || position > before + 1)) {
        throw DSH.argReport("position",
            "插入位置越界（当前 " + before + " 条轨道，允许 1.." + (before + 1) + "）");
    }
    var newIndex = (position === null) ? (before + 1) : position;
    DSH.note("qe_enabled", DSH.qeEnabled(),
        DSH.qeEnabled() ? "将使用未公开的 QE 接口（可能使宿主崩溃）" : "QE 未启用");
    if (!DSH.qeEnabled()) {
        throw DSH.fail("QE_DISABLED",
            "公开 DOM 没有新增轨道的接口（Sequence 只提供 createCaptionTrack），当前未启用 QE",
            "在配置中设置 extendscript.enable_qe = true 后重启面板；"
                + "注意 QE 是未公开接口，可能导致宿主崩溃");
    }

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["新增 1 条 " + trackType + " 轨道到第 " + newIndex + " 位（QE 未公开接口）"],
            { track_type: trackType, track_count: before + 1, track_index: newIndex });
    }

    DSH.qe.addTracks(sequence, trackType, position, trackName);
    var after = DSH.trackCount(sequence, trackType);
    if (after !== before + 1) {
        throw DSH.fail("HOST_ERROR",
            "QE 新增轨道后轨道数没有 +1（" + before + " -> " + after + "）",
            "QE 静默失败很常见；请在宿主界面手动新增轨道。QE 行为随版本变化");
    }
    return { track_type: trackType, track_count: after, track_index: newIndex };
}

// ---- timeline.remove_track（public_or_qe） ---------------------------

function timeline_remove_track(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var trackType = DSH.argEnum(args, "track_type", ["video", "audio"], "video");
    var trackIndex = DSH.argInt(args, "track_index", true, null);
    if (trackIndex < 1) { throw DSH.argReport("track_index", "轨道序号是 1 基"); }
    var sequence = DSH.requireSequence(selector);
    var track = DSH.requireTrack(sequence, trackType, trackIndex);
    var before = DSH.trackCount(sequence, trackType);
    var clipCount = DSH.trackItems(track).length;
    DSH.note("track_clip_count", true, dsh_trackLabel(trackType, trackIndex)
        + " 上有 " + clipCount + " 个片段，删除轨道会一并删除");
    if (!DSH.qeEnabled()) {
        throw DSH.fail("QE_DISABLED",
            "公开 DOM 没有删除轨道的接口，当前未启用 QE",
            "在配置中设置 extendscript.enable_qe = true 后重启面板；"
                + "注意 QE 是未公开接口，可能导致宿主崩溃");
    }

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["删除 " + dsh_trackLabel(trackType, trackIndex) + " 轨道（其上 " + clipCount
                + " 个片段一并删除，QE 未公开接口）"],
            {
                removed: null, track_type: trackType, track_index: trackIndex,
                track_count: before - 1
            });
    }

    DSH.qe.removeTracks(sequence, trackType, trackIndex);
    var after = DSH.trackCount(sequence, trackType);
    if (after !== before - 1) {
        throw DSH.fail("HOST_ERROR",
            "QE 删除轨道后轨道数没有 -1（" + before + " -> " + after + "）",
            "QE 静默失败很常见；请在宿主界面手动删除轨道。QE 行为随版本变化");
    }
    return { removed: true, track_type: trackType, track_index: trackIndex, track_count: after };
}

// ---- timeline.add_marker ---------------------------------------------

function dsh_collectMarkers(sequence) {
    var collection = sequence.markers;
    if (!collection) {
        throw DSH.fail("UNSUPPORTED_API", "该序列对象没有 markers 集合",
            "确认宿主版本支持 Sequence.markers；可改用 ppro_sequence_info 诊断");
    }
    var out = [];
    var getFirst = DSH.api(collection, "getFirstMarker");
    var getNext = DSH.api(collection, "getNextMarker");
    if (getFirst && getNext) {
        var marker = null;
        try {
            marker = getFirst.call(collection);
        } catch (firstError) {
            marker = null;
        }
        var guard = 0;
        while (marker && guard < 10000) {
            out[out.length] = marker;
            try {
                marker = getNext.call(collection, marker);
            } catch (nextError) {
                marker = null;
            }
            guard += 1;
        }
        return out;
    }
    var count = DSH.collectionCount(collection);
    var i;
    for (i = 0; i < count; i += 1) {
        var item = DSH.collectionAt(collection, i);
        if (item) { out[out.length] = item; }
    }
    return out;
}

function dsh_markerTypeName(marker) {
    var raw = null;
    try {
        raw = marker.type ? String(marker.type) : null;
    } catch (typeError) {
        raw = null;
    }
    if (!raw) { return null; }
    var lower = raw.toLowerCase();
    if (lower === "weblink" || lower === "web") { return "web"; }
    if (lower === "comment" || lower === "chapter" || lower === "segmentation") { return lower; }
    return lower;
}

function dsh_markerEntry(marker, index) {
    var startSeconds = null;
    var endSeconds = null;
    try {
        startSeconds = DSH.timeToSeconds(marker.start);
    } catch (startError) {
        startSeconds = null;
    }
    try {
        endSeconds = DSH.timeToSeconds(marker.end);
    } catch (endError) {
        endSeconds = null;
    }
    var duration = null;
    if (startSeconds !== null && endSeconds !== null && endSeconds > startSeconds) {
        duration = endSeconds - startSeconds;
    }
    return {
        index: index,
        name: DSH.hasProperty(marker, "name") ? String(marker.name) : null,
        comment: DSH.hasProperty(marker, "comments") ? String(marker.comments) : null,
        at_s: startSeconds,
        duration_s: duration,
        type: dsh_markerTypeName(marker)
    };
}

function timeline_list_markers(args, ctx) {
    var selector = DSH.argString(args, "sequence", false, null);
    var sequence = DSH.requireSequence(selector);
    var markers = dsh_collectMarkers(sequence);
    var out = [];
    var i;
    for (i = 0; i < markers.length; i += 1) {
        out[out.length] = dsh_markerEntry(markers[i], i);
    }
    return { markers: out };
}

function timeline_add_marker(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var atSeconds = DSH.argNumber(args, "at_s", true, null);
    var markerName = DSH.argString(args, "name", false, null);
    var comment = DSH.argString(args, "comment", false, null);
    var durationSeconds = DSH.argNumber(args, "duration_s", false, null);
    var markerType = DSH.argEnum(args, "type",
        ["comment", "chapter", "segmentation", "web"], "comment");
    if (atSeconds < 0) { throw DSH.argReport("at_s", "不能是负数"); }
    if (durationSeconds !== null && durationSeconds < 0) {
        throw DSH.argReport("duration_s", "不能是负数（0 表示单点标记）");
    }
    var sequence = DSH.requireSequence(selector);
    var collection = sequence.markers;
    if (!collection) {
        throw DSH.fail("UNSUPPORTED_API", "该序列对象没有 markers 集合，无法添加标记",
            "确认宿主版本支持 Sequence.markers");
    }
    DSH.requireApi(collection, "createMarker",
        "该宿主版本的 MarkerCollection 没有 createMarker；无法脚本化添加标记", "MarkerCollection");
    var tolerance = DSH.frameToleranceSeconds(sequence);

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["在序列 " + sequence.name + " 的 " + atSeconds + "s 处添加 "
                + markerType + " 标记" + (markerName ? "（" + markerName + "）" : "")],
            {
                index: null, name: markerName, at_s: atSeconds,
                duration_s: durationSeconds, type: markerType
            });
    }

    var marker = null;
    try {
        marker = collection.createMarker(atSeconds);
    } catch (createError) {
        throw DSH.fail("HOST_ERROR", "createMarker() 调用失败：" + DSH.errorMessage(createError),
            "确认时间点在序列范围内；宿主可能拒绝越界标记");
    }
    if (!marker) {
        throw DSH.fail("HOST_ERROR", "createMarker() 未返回标记对象（at_s=" + atSeconds + "）",
            "宿主可能拒绝了该时间点；请在宿主界面手动添加确认");
    }
    if (markerName !== null) {
        try { marker.name = markerName; } catch (nameError) { /* 下面用读回值如实汇报 */ }
    }
    if (comment !== null) {
        try { marker.comments = comment; } catch (commentError) { /* 同上 */ }
    }
    if (durationSeconds !== null && durationSeconds > 0) {
        // 文档：设置 marker.end 时传"秒数"，不是完整 Time 对象。
        var endSeconds = atSeconds + durationSeconds;
        try {
            marker.end = endSeconds;
        } catch (endError) {
            try { marker.end = DSH.makeTime(endSeconds); } catch (endFallbackError) { /* 读回值会体现 */ }
        }
    }
    var typeSetter = {
        "comment": "setTypeAsComment",
        "chapter": "setTypeAsChapter",
        "segmentation": "setTypeAsSegmentation",
        "web": "setTypeAsWebLink"
    }[markerType];
    var setter = DSH.api(marker, typeSetter);
    if (!setter) {
        throw DSH.fail("UNSUPPORTED_API", "该宿主的 Marker 没有 " + typeSetter + " 方法",
            "标记类型只能取宿主支持的四种；请用 comment（默认）或不传 type");
    }
    try {
        setter.call(marker);
    } catch (typeError) {
        throw DSH.fail("HOST_ERROR", "设置标记类型失败：" + DSH.errorMessage(typeError),
            "确认宿主版本支持该标记类型（web 类型在部分版本需要额外字段）");
    }

    // 写后读回：宿主可能按帧吸附时间点，如实汇报实际值。
    var markers = dsh_collectMarkers(sequence);
    var found = null;
    var index = -1;
    var i;
    for (i = 0; i < markers.length; i += 1) {
        var entry = dsh_markerEntry(markers[i], i);
        if (DSH.nearlyEqual(entry.at_s, atSeconds, tolerance)
                && (markerName === null || entry.name === markerName)) {
            found = entry;
            index = i;
        }
    }
    if (!found) {
        throw DSH.fail("HOST_ERROR", "createMarker 之后在序列上找不到该标记（at_s=" + atSeconds + "）",
            "宿主可能把标记吸附到了别的帧；用 ppro_timeline_list_markers 查看实际结果");
    }
    return {
        index: index,
        name: found.name,
        at_s: found.at_s,
        duration_s: found.duration_s,
        type: found.type
    };
}
