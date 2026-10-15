// =====================================================================
// util.jsx —— 配置、时间换算、引用解析、宿主查找辅助（契约 §5）
//
// 这一层的存在理由：宿主 DOM 在不同 PPro 版本上属性/方法名并不完全一致
// （例如 TrackCollection 的计数属性是 numTracks 还是 numItems 历史上变过），
// 而"把不确定的 API 当成存在的"是本项目最危险的做法。所以：
//   * 所有访问都走 DSH.api()/DSH.requireApi() 做特性检测；
//   * 拿不到的能力一律 throw DSH.fail("UNSUPPORTED_API", ..., remedy)，
//     绝不猜、绝不假装成功（硬性约束 4）。
//
// 时间单位约定：契约里以 _s 结尾的都是秒；宿主 API 大多吃 tick（或 Time 对象）。
// ticks_per_second = 254016000000（契约 conventions）。
// =====================================================================

if (typeof DSH === "undefined") { var DSH = {}; }

//: 兜底常量：dispatch.jsx 会在载入后写入 DSH.ticksPerSecond（此处保证函数体
//: 在任何载入顺序下都算得出正确的换算）。
if (typeof DSH.ticksPerSecond === "undefined") { DSH.ticksPerSecond = 254016000000; }

//: 宿主内默认配置（由 L1 通过 DSH.setConfig 覆盖；协议 §3 的 config 帧）。
DSH.config = {
    qe_enabled: false,
    log_level: "info",
    log_dir: null,
    host: "PPRO"
};

DSH.setConfig = function (cfg) {
    if (!cfg || typeof cfg !== "object") { return DSH.config; }
    var nested = cfg.extendscript ? cfg.extendscript : null;
    if (typeof cfg.qe_enabled === "boolean") { DSH.config.qe_enabled = cfg.qe_enabled; }
    if (typeof cfg.enable_qe === "boolean") { DSH.config.qe_enabled = cfg.enable_qe; }
    if (nested) {
        if (typeof nested.enable_qe === "boolean") { DSH.config.qe_enabled = nested.enable_qe; }
        if (typeof nested.qe_enabled === "boolean") { DSH.config.qe_enabled = nested.qe_enabled; }
    }
    if (cfg.log_level) { DSH.config.log_level = String(cfg.log_level); }
    if (cfg.log_dir) { DSH.config.log_dir = String(cfg.log_dir); }
    if (cfg.host) { DSH.config.host = String(cfg.host); }
    return DSH.config;
};

//: QE 门禁的唯一判据（dispatch.jsx 与各处理器都读它）。
DSH.qeEnabled = function () {
    try {
        return DSH.config && DSH.config.qe_enabled === true;
    } catch (configError) {
        return false;
    }
};

// ---- 时间 ---------------------------------------------------------------

DSH.nowMs = function () { return new Date().getTime(); };

DSH.ticks = function (seconds) {
    var tps = DSH.ticksPerSecond;
    var value = Number(seconds);
    if (!isFinite(value)) {
        throw DSH.fail("INVALID_ARGUMENT", "无法把 " + seconds + " 换算成 tick",
            "秒数必须是有限数值");
    }
    return Math.round(value * tps);
};

DSH.seconds = function (ticks) {
    var tps = DSH.ticksPerSecond;
    var value = Number(ticks);
    if (!isFinite(value)) { return null; }
    return value / tps;
};

//: 把宿主 API 返回的时间值（Time 对象 / tick 字符串 / 秒数）统一成秒。
//: 明确区分：Time 对象取 .seconds；数字与字符串一律按 **tick** 解释
//: （宿主返回 timebase/end/zeroPoint 时都是 tick 字符串）。
DSH.timeToSeconds = function (value) {
    if (value === null || typeof value === "undefined") { return null; }
    if (typeof value === "object" && typeof value.seconds === "number") {
        return value.seconds;
    }
    var numeric = Number(value);
    if (!isFinite(numeric)) { return null; }
    return DSH.seconds(numeric);
};

//: 另一类接口（getInPoint()/getOutPoint()/getWorkAreaInPoint()/getWorkAreaOutPoint()）
//: 文档明确返回**秒字符串**，不是 tick。混用会把 1 秒读成 1 tick（≈4e-12 秒），
//: 这个错误在真机上会表现为"入点读回总是 0"，所以两类必须分开。
DSH.secondsFromValue = function (value) {
    if (value === null || typeof value === "undefined" || value === "") { return null; }
    if (typeof value === "object") {
        if (typeof value.seconds === "number") { return value.seconds; }
        var ticks = DSH.timeTicks(value);
        return (ticks === null) ? null : DSH.seconds(ticks);
    }
    var numeric = Number(value);
    return isFinite(numeric) ? numeric : null;
};

DSH.timeTicks = function (value) {
    if (value === null || typeof value === "undefined") { return null; }
    if (typeof value === "object" && typeof value.ticks !== "undefined") {
        var fromTicks = Number(value.ticks);
        return isFinite(fromTicks) ? fromTicks : null;
    }
    var numeric = Number(value);
    return isFinite(numeric) ? numeric : null;
};

//: 构造 Time 对象。优先用有文档的 setSecondsAsFraction（Time 对象的 .ticks
//: 只保证可读），失败时退回直接写 ticks。
DSH.makeTime = function (seconds) {
    if (typeof Time === "undefined") {
        throw DSH.fail("UNSUPPORTED_API", "当前宿主没有提供 Time 构造器",
            "确认宿主为 Premiere Pro，且 ExtendScript 引擎未被裁剪");
    }
    var value = Number(seconds);
    if (!isFinite(value)) {
        throw DSH.fail("INVALID_ARGUMENT", "非法的时间值：" + seconds, "时间必须是有限秒数");
    }
    var time = new Time();
    if (typeof time.setSecondsAsFraction === "function") {
        time.setSecondsAsFraction(Math.round(value * 1000000), 1000000);
        return time;
    }
    time.ticks = String(DSH.ticks(value));
    return time;
};

// ---- 参数校验 -----------------------------------------------------------
// 只读取契约中声明的参数名；缺必需参数一律 INVALID_ARGUMENT。

DSH.argReport = function (name, why, remedy) {
    return DSH.fail("INVALID_ARGUMENT", "参数 " + name + " " + why,
        remedy ? remedy : ("按命令契约修正参数 " + name));
};

DSH.hasArg = function (args, name) {
    return !!(args && typeof args[name] !== "undefined" && args[name] !== null);
};

DSH.argNumber = function (args, name, required, fallback) {
    if (!DSH.hasArg(args, name)) {
        if (required) { throw DSH.argReport(name, "缺失（必需，number）"); }
        return fallback;
    }
    var value = Number(args[name]);
    if (!isFinite(value)) { throw DSH.argReport(name, "不是有限数值：" + args[name]); }
    return value;
};

DSH.argInt = function (args, name, required, fallback) {
    var value = DSH.argNumber(args, name, required, fallback);
    if (value === null || typeof value === "undefined") { return value; }
    if (Math.round(value) !== value) {
        throw DSH.argReport(name, "必须是整数，实际是 " + value);
    }
    return value;
};

DSH.argString = function (args, name, required, fallback) {
    if (!DSH.hasArg(args, name)) {
        if (required) { throw DSH.argReport(name, "缺失（必需，string）"); }
        return fallback;
    }
    var value = String(args[name]);
    if (required && value === "") { throw DSH.argReport(name, "不能是空字符串"); }
    return value;
};

DSH.argBool = function (args, name, fallback) {
    if (!DSH.hasArg(args, name)) { return fallback; }
    var value = args[name];
    if (typeof value === "boolean") { return value; }
    if (value === 1 || value === 0) { return value === 1; }
    if (value === "true" || value === "false") { return value === "true"; }
    throw DSH.argReport(name, "不是布尔值：" + value, "传 true/false");
};

DSH.argEnum = function (args, name, allowed, fallback) {
    if (!DSH.hasArg(args, name)) { return fallback; }
    var value = String(args[name]);
    var i;
    for (i = 0; i < allowed.length; i += 1) {
        if (allowed[i] === value) { return value; }
    }
    throw DSH.argReport(name, "取值不在允许集合内：" + value,
        "允许值：" + allowed.join(" / "));
};

DSH.argStringArray = function (args, name, required) {
    if (!DSH.hasArg(args, name)) {
        if (required) { throw DSH.argReport(name, "缺失（必需，array<string>）"); }
        return null;
    }
    var value = args[name];
    if (Object.prototype.toString.apply(value) !== "[object Array]") {
        throw DSH.argReport(name, "不是数组");
    }
    if (required && value.length === 0) {
        throw DSH.argReport(name, "是空数组");
    }
    var out = [];
    var i;
    for (i = 0; i < value.length; i += 1) {
        if (value[i] === null || typeof value[i] === "undefined") {
            throw DSH.argReport(name + "[" + i + "]", "是 null/undefined");
        }
        out[out.length] = String(value[i]);
    }
    return out;
};

// ---- 特性检测 -----------------------------------------------------------

//: 存在且是函数才返回它，否则 null。宿主 API 的存在性只能这样问。
DSH.api = function (object, name) {
    if (!object) { return null; }
    try {
        var member = object[name];
        return (typeof member === "function") ? member : null;
    } catch (probeError) {
        return null;
    }
};

DSH.requireApi = function (object, name, remedy, subject) {
    var fn = DSH.api(object, name);
    if (!fn) {
        throw DSH.fail("UNSUPPORTED_API",
            (subject ? subject : "当前宿主对象") + " 没有可用的 API：" + name,
            remedy ? remedy : "该宿主版本不支持此能力；请在目标 PPro 版本上用真机确认后改用其它路径");
    }
    return fn;
};

DSH.hasProperty = function (object, name) {
    if (!object) { return false; }
    try {
        return typeof object[name] !== "undefined";
    } catch (probeError) {
        return false;
    }
};

// ---- 工程与序列 ---------------------------------------------------------

DSH.requireProject = function () {
    var project = null;
    try {
        project = (typeof app !== "undefined" && app) ? app.project : null;
    } catch (projectError) {
        project = null;
    }
    var ok = !!(project && typeof project.rootItem !== "undefined" && project.rootItem);
    DSH.note("project_open", ok, ok ? null : "app.project 不可用（没有打开的工程）");
    if (!ok) {
        throw DSH.fail("NO_PROJECT", "当前没有打开的工程",
            "先调用 ppro_project_new 新建，或 ppro_project_open 打开一个 .prproj");
    }
    return project;
};

//: project.sequences 是 SequenceCollection；计数属性历史上出现过 numSequences/
//: numItems 两种，故统一探测。
DSH.collectionCount = function (collection) {
    if (!collection) { return 0; }
    if (Object.prototype.toString.apply(collection) === "[object Array]") {
        return collection.length;
    }
    var names = ["numItems", "numTracks", "numSequences", "numMarkers", "numChildren", "length"];
    var i;
    for (i = 0; i < names.length; i += 1) {
        if (typeof collection[names[i]] === "number") { return collection[names[i]]; }
    }
    return 0;
};

DSH.collectionAt = function (collection, index) {
    if (!collection) { return null; }
    try {
        var item = collection[index];
        return (typeof item === "undefined") ? null : item;
    } catch (indexError) {
        return null;
    }
};

DSH.sequenceList = function () {
    var project = DSH.requireProject();
    var collection = project.sequences;
    if (!collection) {
        throw DSH.fail("UNSUPPORTED_API", "app.project.sequences 不可用",
            "确认宿主为 Premiere Pro 且工程已完全加载；必要时重开面板");
    }
    var out = [];
    var count = DSH.collectionCount(collection);
    var i;
    for (i = 0; i < count; i += 1) {
        var sequence = DSH.collectionAt(collection, i);
        if (sequence) { out[out.length] = sequence; }
    }
    return out;
};

//: selector 为空 -> 活动序列；否则按 名称 -> treePath -> sequenceID 依次匹配。
DSH.findSequence = function (selector) {
    var project = DSH.requireProject();
    if (selector === null || typeof selector === "undefined" || String(selector) === "") {
        var active = project.activeSequence;
        // 文档：没有活动序列时返回 0。
        if (!active || typeof active === "number") { return null; }
        return active;
    }
    var wanted = String(selector);
    var key = DSH.pathKey(wanted);
    var candidates = DSH.sequenceList();
    var i;
    var sequence;
    for (i = 0; i < candidates.length; i += 1) {
        sequence = candidates[i];
        if (String(sequence.name) === wanted) { return sequence; }
    }
    for (i = 0; i < candidates.length; i += 1) {
        sequence = candidates[i];
        if (sequence.projectItem && DSH.pathKey(DSH.treePathSafe(sequence.projectItem)) === key) {
            return sequence;
        }
    }
    for (i = 0; i < candidates.length; i += 1) {
        sequence = candidates[i];
        if (sequence.sequenceID && String(sequence.sequenceID) === wanted) { return sequence; }
    }
    for (i = 0; i < candidates.length; i += 1) {
        sequence = candidates[i];
        if (String(sequence.name).toLowerCase() === wanted.toLowerCase()) { return sequence; }
    }
    return null;
};

DSH.requireSequence = function (selector) {
    var sequence = null;
    var how = (selector === null || typeof selector === "undefined" || String(selector) === "")
        ? "活动序列" : ("序列 " + selector);
    try {
        sequence = DSH.findSequence(selector);
    } catch (findError) {
        DSH.note("sequence_found", false, DSH.errorMessage(findError));
        throw findError;
    }
    var available = [];
    var i;
    try {
        var all = DSH.sequenceList();
        for (i = 0; i < all.length; i += 1) { available[available.length] = String(all[i].name); }
    } catch (listError) {
        available = [];
    }
    var ok = !!sequence;
    DSH.note("sequence_found", ok, ok ? DSH.sequenceLabel(sequence) : ("找不到 " + how));
    if (!ok) {
        throw DSH.fail("NOT_FOUND", "找不到 " + how,
            available.length
                ? ("工程内现有序列：" + available.join(" / ") + "；也可先 sequence.set_active")
                : "工程内没有任何序列，先用 ppro_sequence_create 创建");
    }
    return sequence;
};

DSH.sequenceLabel = function (sequence) {
    if (!sequence) { return null; }
    var name = sequence.name ? String(sequence.name) : "(未命名序列)";
    var path = null;
    if (sequence.projectItem) {
        path = DSH.treePathSafe(sequence.projectItem);
    }
    return path ? (name + " @ " + path) : name;
};

//: 帧率由 timebase（每帧 tick 数，字符串）反推；拿不到就返回 null。
DSH.sequenceFrameRate = function (sequence) {
    var timebase = DSH.timeTicks(sequence.timebase);
    if (!timebase || timebase <= 0) { return null; }
    return DSH.ticksPerSecond / timebase;
};

//: 序列时长的秒数。文档：Sequence.end 是 tick 字符串，且相对序列起点。
DSH.sequenceDurationSeconds = function (sequence) {
    return DSH.timeToSeconds(sequence.end);
};

DSH.sequenceRef = function (sequence) {
    return {
        id: sequence.sequenceID ? String(sequence.sequenceID) : null,
        name: sequence.name ? String(sequence.name) : null,
        path: sequence.projectItem ? DSH.treePathSafe(sequence.projectItem) : null
    };
};

// ---- 轨道 ---------------------------------------------------------------

DSH.trackTypeOf = function (ref) {
    return (String(ref).toUpperCase().charAt(0) === "A") ? "audio" : "video";
};

DSH.trackLetter = function (trackType) {
    return (trackType === "audio") ? "A" : "V";
};

DSH.trackCollection = function (sequence, trackType) {
    var collection = (trackType === "audio") ? sequence.audioTracks : sequence.videoTracks;
    var ok = !!collection;
    DSH.note("track_collection_" + trackType, ok,
        ok ? null : ("序列没有提供 " + trackType + "Tracks"));
    if (!ok) {
        throw DSH.fail("UNSUPPORTED_API", "序列没有提供 " + trackType + "Tracks",
            "确认宿主版本与序列对象有效；必要时重开序列");
    }
    return collection;
};

DSH.trackCount = function (sequence, trackType) {
    return DSH.collectionCount(DSH.trackCollection(sequence, trackType));
};

DSH.findTrack = function (sequence, trackType, trackIndex) {
    var collection = DSH.trackCollection(sequence, trackType);
    var count = DSH.collectionCount(collection);
    if (trackIndex < 1 || trackIndex > count) { return null; }
    return DSH.collectionAt(collection, trackIndex - 1);
};

DSH.requireTrack = function (sequence, trackType, trackIndex) {
    var count = DSH.trackCount(sequence, trackType);
    var track = (trackIndex >= 1 && trackIndex <= count)
        ? DSH.collectionAt(DSH.trackCollection(sequence, trackType), trackIndex - 1)
        : null;
    var ok = !!track;
    DSH.note("track_" + trackType + "_" + trackIndex + "_exists", ok,
        ok ? null : ("该序列有 " + count + " 条 " + trackType + " 轨道"));
    if (!ok) {
        throw DSH.fail("NOT_FOUND",
            "序列 " + DSH.sequenceLabel(sequence) + " 上不存在 " + trackType
                + " 轨道 " + trackIndex + "（共 " + count + " 条）",
            "轨道序号是 1 基；先用 ppro_timeline_list_clips 或 ppro_sequence_info 确认真实序号");
    }
    return track;
};

//: Track.clips -> TrackItemCollection。
DSH.trackClips = function (track) {
    var clips = track.clips;
    if (!clips) {
        throw DSH.fail("UNSUPPORTED_API", "轨道对象没有 clips 集合",
            "确认宿主版本支持 Track.clips；可改用 ppro_timeline_list_clips 诊断");
    }
    return clips;
};

DSH.trackItems = function (track) {
    var clips = DSH.trackClips(track);
    var count = DSH.collectionCount(clips);
    var out = [];
    var i;
    for (i = 0; i < count; i += 1) {
        var item = DSH.collectionAt(clips, i);
        if (item) { out[out.length] = item; }
    }
    return out;
};

DSH.makeRef = function (trackType, trackIndex, index) {
    return DSH.trackLetter(trackType) + ":" + trackIndex + ":" + index;
};

//: 解析 "V:1:0" / "A:2:3"（大小写、空白容错）。
DSH.parseRef = function (ref) {
    var text = (ref === null || typeof ref === "undefined") ? "" : String(ref);
    var match = /^\s*([VvAa])\s*:\s*(\d+)\s*:\s*(\d+)\s*$/.exec(text);
    var ok = !!match;
    DSH.note("ref_format_valid", ok, ok ? null : ("无法解析的 ref：" + text));
    if (!ok) {
        throw DSH.fail("REF_INVALID", "非法的片段引用：" + text,
            "ref 形如 \"V:1:0\"（视频第 1 轨第 0 个片段）或 \"A:2:3\"；"
                + "可用 ppro_timeline_list_clips 取回真实 ref");
    }
    return {
        ref: DSH.trackLetter(match[1].toUpperCase() === "A" ? "audio" : "video")
            + ":" + String(Number(match[2])) + ":" + String(Number(match[3])),
        track_type: (match[1].toUpperCase() === "A") ? "audio" : "video",
        track_index: Number(match[2]),
        index: Number(match[3])
    };
};

//: ref -> {parsed, track, item, track_type, track_index, index}
DSH.requireTrackItem = function (sequence, ref) {
    var parsed = DSH.parseRef(ref);
    if (parsed.track_index < 1) {
        throw DSH.fail("REF_INVALID", "轨道序号是 1 基，不能是 0：" + ref,
            "ref 形如 \"V:1:0\"；轨道序号从 1 开始");
    }
    var track = DSH.requireTrack(sequence, parsed.track_type, parsed.track_index);
    var items = DSH.trackItems(track);
    var ok = (parsed.index >= 0 && parsed.index < items.length);
    DSH.note("clip_ref_exists", ok,
        ok ? null : ("该轨道上只有 " + items.length + " 个片段"));
    if (!ok) {
        throw DSH.fail("NOT_FOUND",
            "序列 " + DSH.sequenceLabel(sequence) + " 的 " + parsed.ref + " 不存在"
                + "（该轨道只有 " + items.length + " 个片段）",
            "片段序号是 0 基；用 ppro_timeline_list_clips 取回最新 ref（编辑后 ref 会变）");
    }
    parsed.ref = DSH.makeRef(parsed.track_type, parsed.track_index, parsed.index);
    return {
        parsed: parsed,
        track: track,
        item: items[parsed.index],
        track_type: parsed.track_type,
        track_index: parsed.track_index,
        index: parsed.index,
        ref: parsed.ref
    };
};

//: 反向查找：给定 TrackItem 找出它的 ref（供 selection 使用）。
DSH.locateTrackItem = function (sequence, wantedItem) {
    var types = ["video", "audio"];
    var t;
    var trackIndex;
    var items;
    var i;
    for (t = 0; t < types.length; t += 1) {
        var count = 0;
        try {
            count = DSH.trackCount(sequence, types[t]);
        } catch (countError) {
            count = 0;
        }
        for (trackIndex = 1; trackIndex <= count; trackIndex += 1) {
            var track = DSH.findTrack(sequence, types[t], trackIndex);
            if (!track) { continue; }
            items = DSH.trackItems(track);
            for (i = 0; i < items.length; i += 1) {
                if (items[i] === wantedItem) {
                    return {
                        ref: DSH.makeRef(types[t], trackIndex, i),
                        track_type: types[t],
                        track_index: trackIndex,
                        index: i
                    };
                }
            }
        }
    }
    return null;
};

DSH.trackItemStartSeconds = function (item) {
    if (!item) { return null; }
    return DSH.timeToSeconds(item.start);
};

DSH.trackItemEndSeconds = function (item) {
    if (!item) { return null; }
    return DSH.timeToSeconds(item.end);
};

//: TrackItem.type：文档为数字，1 视频 / 2 音频。
DSH.trackItemTypeName = function (item) {
    if (!item) { return null; }
    if (typeof item.mediaType === "string" && item.mediaType) {
        return item.mediaType.toLowerCase();
    }
    if (item.type === 1) { return "video"; }
    if (item.type === 2) { return "audio"; }
    return null;
};

//: enabled：文档把 disabled 列成"可读写的属性/带参方法"，两者都兼容；
//: 拿不到布尔值就返回 null（不猜）。
DSH.trackItemEnabled = function (item) {
    if (!item) { return null; }
    if (typeof item.disabled === "boolean") { return !item.disabled; }
    if (typeof item.enabled === "boolean") { return item.enabled; }
    return null;
};

DSH.trackItemSelected = function (item) {
    if (!item) { return null; }
    var isSelected = DSH.api(item, "isSelected");
    if (isSelected) {
        try {
            return isSelected.call(item) === true;
        } catch (selectError) {
            return null;
        }
    }
    if (typeof item.selected === "boolean") { return item.selected; }
    return null;
};

//: 播放头位置（秒）。getPlayerPosition() 返回 Time 对象。
DSH.sequencePlayheadSeconds = function (sequence) {
    if (!sequence) { return null; }
    var getPlayerPosition = DSH.api(sequence, "getPlayerPosition");
    if (!getPlayerPosition) { return null; }
    try {
        return DSH.timeToSeconds(getPlayerPosition.call(sequence));
    } catch (playheadError) {
        return null;
    }
};

//: 序列入/出点（秒）。getInPoint()/getOutPoint() 返回的是**秒**字符串，
//: 因此用 secondsFromValue 而不是 timeToSeconds。
DSH.sequenceInOut = function (sequence) {
    var result = { in_s: null, out_s: null };
    if (!sequence) { return result; }
    var getInPoint = DSH.api(sequence, "getInPoint");
    if (getInPoint) {
        try {
            result.in_s = DSH.secondsFromValue(getInPoint.call(sequence));
        } catch (inPointError) {
            result.in_s = null;
        }
    }
    var getOutPoint = DSH.api(sequence, "getOutPoint");
    if (getOutPoint) {
        try {
            result.out_s = DSH.secondsFromValue(getOutPoint.call(sequence));
        } catch (outPointError) {
            result.out_s = null;
        }
    }
    return result;
};

//: 浮点比较：宿主会按帧对齐，所以写入后读回的值允许有容差。
DSH.nearlyEqual = function (actual, expected, tolerance) {
    if (actual === null || typeof actual === "undefined") { return false; }
    if (expected === null || typeof expected === "undefined") { return false; }
    var diff = Number(actual) - Number(expected);
    if (diff < 0) { diff = -diff; }
    return diff <= (tolerance ? tolerance : 0);
};

//: 一帧对应的秒数（容差用）。拿不到 timebase 时退回 1/25 秒。
DSH.frameToleranceSeconds = function (sequence) {
    var timebase = DSH.timeTicks(sequence ? sequence.timebase : null);
    if (timebase && timebase > 0) { return DSH.seconds(timebase); }
    return 0.04;
};

//: "写入后读回校验"的通用原语。
//
// 为什么需要它：宿主 API 的参数形式（Time 对象 / tick 字符串 / 秒数）在不同
// 版本间变过，而 ExtendScript 无法反射出签名。所以这里逐个变体尝试，
// **每次尝试后都读回真实值**，只有读回值与目标一致才算成功。
// 这比"调用了就假定成功"多一次读操作，但换来的是"绝不假装成功"。
DSH.applyWithVariants = function (target, methodName, variants, verify, remedy, subject) {
    var fn = DSH.requireApi(target, methodName, remedy, subject);
    var failures = [];
    var index;
    for (index = 0; index < variants.length; index += 1) {
        var attempt = variants[index];
        try {
            fn.apply(target, attempt.args ? attempt.args : []);
        } catch (callError) {
            failures[failures.length] = attempt.kind + " -> 抛异常：" + DSH.errorMessage(callError);
            continue;
        }
        if (!verify) { return attempt.kind; }
        var ok = false;
        try {
            ok = verify() === true;
        } catch (verifyError) {
            failures[failures.length] = attempt.kind + " -> 读回校验抛异常：" + DSH.errorMessage(verifyError);
            continue;
        }
        if (ok) { return attempt.kind; }
        failures[failures.length] = attempt.kind + " -> 调用成功但读回值仍是原值（宿主未接受该参数形式）";
    }
    throw DSH.fail("UNSUPPORTED_API",
        "无法通过 " + methodName + " 写入目标值（已尝试 " + variants.length + " 种参数形式）",
        remedy ? remedy : "该宿主版本不接受这些参数形式；请在真机上确认 API 约定后重试",
        { detail: failures.join(" | ") });
};

//: 生成"时间参数"的候选形式：Time 对象 / tick 字符串 / 秒数。
DSH.timeArgVariants = function (seconds) {
    var variants = [];
    try {
        variants[variants.length] = { kind: "Time 对象", args: [DSH.makeTime(seconds)] };
    } catch (timeError) {
        // 宿主没有 Time 构造器时只试后两种
    }
    variants[variants.length] = { kind: "tick 字符串", args: [String(DSH.ticks(seconds))] };
    variants[variants.length] = { kind: "秒数", args: [Number(seconds)] };
    return variants;
};

//: 写属性（如 TrackItem.start / TrackItem.inPoint 这类"文档标注 read/write 的
//: Time 属性"）。与 applyWithVariants 同样的"写后读回校验"策略。
DSH.setPropertyWithVariants = function (target, propertyName, variants, verify, remedy, subject) {
    var failures = [];
    var index;
    for (index = 0; index < variants.length; index += 1) {
        var attempt = variants[index];
        try {
            target[propertyName] = attempt.args[0];
        } catch (assignError) {
            failures[failures.length] = attempt.kind + " -> 赋值抛异常：" + DSH.errorMessage(assignError);
            continue;
        }
        if (!verify) { return attempt.kind; }
        var ok = false;
        try {
            ok = verify() === true;
        } catch (verifyError) {
            failures[failures.length] = attempt.kind + " -> 读回校验抛异常：" + DSH.errorMessage(verifyError);
            continue;
        }
        if (ok) { return attempt.kind; }
        failures[failures.length] = attempt.kind + " -> 赋值成功但读回值仍是原值（宿主忽略了该写入）";
    }
    throw DSH.fail("UNSUPPORTED_API",
        "无法写入 " + (subject ? subject + "." : "") + propertyName + "（已尝试 " + variants.length + " 种形式）",
        remedy ? remedy : "该宿主版本不支持脚本化写这个属性；请在时间线面板手动调整",
        { detail: failures.join(" | ") });
};

// ---- 工程条目 -----------------------------------------------------------

//: 条目类型名。**先用能力探测**（isSequence()/isBin()），因为宿主把序列条目
//: 的 type 也报成 "CLIP"，只信 type 会把序列误判成素材。
DSH.itemTypeName = function (item) {
    if (!item) { return null; }
    if (DSH.api(item, "isSequence")) {
        try {
            if (item.isSequence()) { return "sequence"; }
        } catch (sequenceProbeError) {
            // 继续下面的探测
        }
    }
    if (DSH.api(item, "isBin")) {
        try {
            if (item.isBin()) { return "bin"; }
        } catch (binProbeError) {
            // 继续
        }
    }
    if (typeof item.type === "string") {
        var text = item.type.toLowerCase();
        if (text === "root") { return "bin"; }
        return text;
    }
    return null;
};

DSH.isBinItem = function (item) {
    if (!item) { return false; }
    var typeName = DSH.itemTypeName(item);
    return typeName === "bin" || typeName === "root";
};

DSH.treePathSafe = function (item) {
    if (!item) { return null; }
    try {
        if (typeof item.treePath !== "undefined" && item.treePath !== null) {
            return String(item.treePath);
        }
    } catch (treePathError) {
        // 落到下面的兜底
    }
    try {
        if (item.name) { return "\\" + String(item.name); }
    } catch (nameError) {
        // 忽略
    }
    return null;
};

//: 契约的 item_ref 是树路径（如 \\Project.prproj\\Bin\\clip.mp4）或 nodeId。
DSH.treePath = function (item) {
    return DSH.treePathSafe(item);
};

DSH.pathKey = function (path) {
    if (path === null || typeof path === "undefined") { return ""; }
    var text = String(path).replace(/\//g, "\\");
    while (text.length > 1 && text.charAt(text.length - 1) === "\\") {
        text = text.substring(0, text.length - 1);
    }
    if (text.charAt(0) !== "\\") { text = "\\" + text; }
    return text.toLowerCase();
};

//: 深度优先遍历整个工程树（含根），对每个条目回调。
DSH.walkProjectItems = function (visit) {
    var project = DSH.requireProject();
    var root = project.rootItem;
    if (!root) { return; }
    function walk(item, depth) {
        if (!item || depth > 64) { return; }
        visit(item);
        if (!DSH.isBinItem(item)) { return; }
        var children = null;
        try {
            children = item.children;
        } catch (childrenError) {
            children = null;
        }
        if (!children) { return; }
        var count = DSH.collectionCount(children);
        var i;
        for (i = 0; i < count; i += 1) {
            walk(DSH.collectionAt(children, i), depth + 1);
        }
    }
    walk(root, 0);
};

DSH.collectProjectItems = function () {
    var out = [];
    DSH.walkProjectItems(function (item) { out[out.length] = item; });
    return out;
};

//: 先按树路径匹配，再按 nodeId 匹配（契约 item_ref_format）。
DSH.findProjectItem = function (ref) {
    if (ref === null || typeof ref === "undefined" || String(ref) === "") { return null; }
    var wanted = String(ref);
    var wantedKey = DSH.pathKey(wanted);
    var items = DSH.collectProjectItems();
    var i;
    var item;
    for (i = 0; i < items.length; i += 1) {
        item = items[i];
        if (DSH.pathKey(DSH.treePathSafe(item)) === wantedKey) { return item; }
    }
    for (i = 0; i < items.length; i += 1) {
        item = items[i];
        try {
            if (item.nodeId && String(item.nodeId) === wanted) { return item; }
        } catch (nodeIdError) {
            // 忽略该条目
        }
    }
    return null;
};

DSH.requireProjectItem = function (ref) {
    var item = DSH.findProjectItem(ref);
    var ok = !!item;
    DSH.note("project_item_exists", ok, ok ? DSH.treePathSafe(item) : ("找不到条目 " + ref));
    if (!ok) {
        throw DSH.fail("ITEM_NOT_FOUND", "找不到工程条目：" + ref,
            "先用 ppro_project_item_find 确认树路径或 nodeId（编辑/重命名后路径会变）");
    }
    return item;
};

//: 素材箱查找：空选择器 -> 工程根。
DSH.findBin = function (selector) {
    if (selector === null || typeof selector === "undefined" || String(selector) === "") {
        var project = DSH.requireProject();
        return project.rootItem;
    }
    var item = DSH.findProjectItem(selector);
    if (!item) { return null; }
    return DSH.isBinItem(item) ? item : null;
};

DSH.requireBin = function (selector) {
    var bin = DSH.findBin(selector);
    var ok = !!bin;
    DSH.note("target_bin_exists", ok,
        ok ? DSH.treePathSafe(bin) : ("找不到素材箱 " + selector));
    if (!ok) {
        throw DSH.fail("NOT_FOUND",
            "找不到素材箱：" + (selector ? selector : "(工程根)"),
            "用 ppro_project_bin_list 列出真实素材箱路径，或先 ppro_project_bin_create");
    }
    return bin;
};

//: 直接子项（不递归）。
DSH.childItems = function (bin) {
    var out = [];
    if (!bin) { return out; }
    var children = null;
    try {
        children = bin.children;
    } catch (childrenError) {
        children = null;
    }
    if (!children) { return out; }
    var count = DSH.collectionCount(children);
    var i;
    for (i = 0; i < count; i += 1) {
        var child = DSH.collectionAt(children, i);
        if (child) { out[out.length] = child; }
    }
    return out;
};

DSH.itemSummary = function (item) {
    var mediaPath = null;
    var getMediaPath = DSH.api(item, "getMediaPath");
    if (getMediaPath) {
        try {
            mediaPath = getMediaPath.call(item);
            if (typeof mediaPath === "undefined" || mediaPath === 0) { mediaPath = null; }
        } catch (mediaPathError) {
            mediaPath = null;
        }
    }
    return {
        id: DSH.hasProperty(item, "nodeId") ? String(item.nodeId) : null,
        path: DSH.treePathSafe(item),
        name: DSH.hasProperty(item, "name") ? String(item.name) : null,
        type: DSH.itemTypeName(item),
        media_path: mediaPath
    };
};

// ---- 文件与数值 ---------------------------------------------------------

DSH.fileExists = function (path) {
    if (!path) { return false; }
    if (typeof File === "undefined") { return false; }
    try {
        var handle = new File(String(path));
        return handle.exists === true;
    } catch (fileError) {
        return false;
    }
};

DSH.parentFolderExists = function (path) {
    if (!path) { return false; }
    if (typeof File === "undefined") { return false; }
    try {
        var handle = new File(String(path));
        var parent = handle.parent;
        if (!parent) { return false; }
        return parent.exists === true;
    } catch (folderError) {
        return false;
    }
};

//: 只保留 JSON 能安全承载的形态；宿主参数值（颜色/点对象等）做浅层降级。
DSH.sanitizeValue = function (value, depth) {
    if (value === null || typeof value === "undefined") { return null; }
    var type = typeof value;
    if (type === "number" || type === "string" || type === "boolean") { return value; }
    if (type === "function") { return null; }
    var level = depth ? depth : 0;
    if (level > 3) { return String(value); }
    if (Object.prototype.toString.apply(value) === "[object Array]") {
        var list = [];
        var i;
        for (i = 0; i < value.length && i < 64; i += 1) {
            list[list.length] = DSH.sanitizeValue(value[i], level + 1);
        }
        return list;
    }
    var out = {};
    var count = 0;
    try {
        for (var key in value) {
            if (!Object.prototype.hasOwnProperty.call(value, key)) { continue; }
            if (count >= 32) { break; }
            var member = value[key];
            var memberType = typeof member;
            if (memberType === "number" || memberType === "string" || memberType === "boolean") {
                out[key] = member;
                count += 1;
            }
        }
    } catch (enumerateError) {
        return String(value);
    }
    if (count === 0) { return String(value); }
    return out;
};

DSH.guid = function () {
    function block() {
        return (Math.floor((1 + Math.random()) * 0x10000)).toString(16).substring(1);
    }
    return block() + block() + "-" + block() + "-" + block() + "-" + block() + "-"
        + block() + block() + block();
};
