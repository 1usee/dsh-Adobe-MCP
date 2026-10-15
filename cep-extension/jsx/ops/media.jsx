// =====================================================================
// ops/media.jsx —— 媒体处理器（契约 §5.3）
//   media.probe            -> media_probe
//   media.relink           -> media_relink
//   media.generate_proxies -> media_generate_proxies
//
// 公开 API 依据（ppro-scripting 文档）：
//   ProjectItem.getMediaPath()、isOffline()、canChangeMediaPath()、
//   changeMediaPath(newPath, overrideChecks)、getFootageInterpretation()（frameRate 等）、
//   canProxy()/hasProxy()/getProxyPath()/attachProxy(mediaPath, isHiRes)、
//   getAudioChannelMapping()
//
// media.probe 的设计要点（契约明确要求）：宿主公开 API 覆盖不到的字段
// （时长/宽高/音频通道数）进 unavailable 列表并返回 null，**不猜**。
//
// media.generate_proxies：**宿主没有脚本化的"生成代理"接口**（公开 DOM 与已知
// QE 都没有）。因此这里只处理"已经有代理"的情况，其余如实报 UNSUPPORTED_API
// 并给出可执行的替代路径，而不是伪造一个 job_id。
// =====================================================================

if (typeof DSH === "undefined") { var DSH = {}; }

//: 媒体条目的公共字段。unavailable 里列出"宿主公开 API 未提供"的字段。
function dsh_probeItem(item) {
    var unavailable = [];
    var mediaPath = null;
    var offline = null;
    var frameRate = null;
    var width = null;
    var height = null;
    var hasVideo = null;
    var hasAudio = null;
    var audioChannels = null;

    var getMediaPath = DSH.api(item, "getMediaPath");
    if (getMediaPath) {
        try {
            var rawPath = getMediaPath.call(item);
            mediaPath = (rawPath && rawPath !== 0) ? String(rawPath) : null;
        } catch (mediaPathError) {
            mediaPath = null;
            unavailable[unavailable.length] = "media_path";
        }
    } else {
        unavailable[unavailable.length] = "media_path";
    }

    var isOffline = DSH.api(item, "isOffline");
    if (isOffline) {
        try {
            offline = isOffline.call(item) === true;
        } catch (offlineError) {
            offline = null;
            unavailable[unavailable.length] = "offline";
        }
    } else {
        unavailable[unavailable.length] = "offline";
    }

    var getFootageInterpretation = DSH.api(item, "getFootageInterpretation");
    if (getFootageInterpretation) {
        try {
            var interpretation = getFootageInterpretation.call(item);
            if (interpretation && typeof interpretation.frameRate !== "undefined") {
                var rate = Number(interpretation.frameRate);
                if (isFinite(rate) && rate > 0) { frameRate = rate; }
            }
        } catch (interpretationError) {
            frameRate = null;
        }
    }
    if (frameRate === null) { unavailable[unavailable.length] = "frame_rate"; }

    // 时长 / 宽高 / 音频通道数：公开 DOM 的 ProjectItem 没有对应只读属性。
    unavailable[unavailable.length] = "duration_s";
    unavailable[unavailable.length] = "width";
    unavailable[unavailable.length] = "height";
    unavailable[unavailable.length] = "audio_channels";

    var hasVideoFn = DSH.api(item, "hasVideo");
    if (hasVideoFn) {
        try {
            hasVideo = hasVideoFn.call(item) === true;
            if (hasVideo !== null) { unavailable = dsh_removeFromList(unavailable, "has_video"); }
        } catch (hasVideoError) {
            hasVideo = null;
        }
    } else {
        unavailable[unavailable.length] = "has_video";
    }
    var hasAudioFn = DSH.api(item, "hasAudio");
    if (hasAudioFn) {
        try {
            hasAudio = hasAudioFn.call(item) === true;
            if (hasAudio !== null) { unavailable = dsh_removeFromList(unavailable, "has_audio"); }
        } catch (hasAudioError) {
            hasAudio = null;
        }
    } else {
        unavailable[unavailable.length] = "has_audio";
    }

    return {
        id: DSH.hasProperty(item, "nodeId") ? String(item.nodeId) : null,
        name: DSH.hasProperty(item, "name") ? String(item.name) : null,
        media_path: mediaPath,
        type: DSH.itemTypeName(item),
        offline: offline,
        duration_s: null,
        frame_rate: frameRate,
        width: width,
        height: height,
        has_video: hasVideo,
        has_audio: hasAudio,
        audio_channels: audioChannels,
        unavailable: unavailable
    };
}

function dsh_removeFromList(list, value) {
    var out = [];
    var i;
    for (i = 0; i < list.length; i += 1) {
        if (list[i] !== value) { out[out.length] = list[i]; }
    }
    return out;
}

// ---- media.probe ------------------------------------------------------

function media_probe(args, ctx) {
    var itemRef = DSH.argString(args, "item", true, null);
    var item = DSH.requireProjectItem(itemRef);
    var probed = dsh_probeItem(item);
    return {
        id: probed.id,
        name: probed.name,
        media_path: probed.media_path,
        type: probed.type,
        offline: probed.offline,
        duration_s: probed.duration_s,
        frame_rate: probed.frame_rate,
        width: probed.width,
        height: probed.height,
        has_video: probed.has_video,
        has_audio: probed.has_audio,
        audio_channels: probed.audio_channels,
        unavailable: probed.unavailable
    };
}

// ---- media.relink ----------------------------------------------------

function media_relink(args, ctx) {
    DSH.beginChecks(ctx);
    var itemRef = DSH.argString(args, "item", true, null);
    var newPath = DSH.argString(args, "new_path", true, null);
    var overrideChecks = DSH.argBool(args, "override_checks", false);
    var item = DSH.requireProjectItem(itemRef);
    var typeName = DSH.itemTypeName(item);

    DSH.note("relink_target_is_media", typeName !== "bin" && typeName !== "sequence",
        "条目类型：" + typeName);
    if (typeName === "bin" || typeName === "sequence") {
        throw DSH.fail("INVALID_ARGUMENT", "该条目不是媒体素材，无法重新链接：" + itemRef
            + "（类型 " + typeName + "）",
            "relink 只能用于媒体素材；素材箱/序列没有媒体路径");
    }
    var canChange = DSH.api(item, "canChangeMediaPath");
    if (canChange) {
        var allowed = false;
        try {
            allowed = canChange.call(item) === true;
        } catch (canChangeError) {
            allowed = false;
        }
        DSH.note("media_path_changeable", allowed, null);
        if (!allowed) {
            throw DSH.fail("UNSUPPORTED_API", "宿主要求不能修改该条目的媒体路径：" + itemRef,
                "该条目可能是合成素材/占位符/只读素材；请在宿主界面手动重新链接");
        }
    }
    var fileExists = DSH.fileExists(newPath);
    DSH.note("new_media_file_exists", fileExists, newPath);
    if (!fileExists) {
        throw DSH.fail("INVALID_ARGUMENT", "新的媒体文件不存在：" + newPath,
            "relink 指向的文件必须先在磁盘上存在（否则会触发宿主的定位文件对话框）");
    }
    var previousPath = null;
    var getMediaPath = DSH.api(item, "getMediaPath");
    if (getMediaPath) {
        try {
            var rawPrevious = getMediaPath.call(item);
            previousPath = (rawPrevious && rawPrevious !== 0) ? String(rawPrevious) : null;
        } catch (previousPathError) {
            previousPath = null;
        }
    }
    DSH.requireApi(item, "changeMediaPath",
        "该宿主版本无法脚本化重新链接素材；请在宿主界面手动链接", "ProjectItem");

    var id = DSH.hasProperty(item, "nodeId") ? String(item.nodeId) : null;
    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["把条目 " + (probedName(item) ? probedName(item) : itemRef) + " 的媒体路径从 "
                + String(previousPath) + " 改为 " + newPath
                + (overrideChecks ? "（跳过宿主一致性校验）" : "")],
            { id: id, previous_path: previousPath, media_path: newPath });
    }

    // 参数形式在版本间有差异（文档签名只写了 newPath，参数表里却有 overrideChecks）。
    var attempts = [
        { kind: "两参数(newPath, overrideChecks)", args: [newPath, overrideChecks === true] },
        { kind: "一参数(newPath)", args: [newPath] }
    ];
    var errors = [];
    var index;
    var changed = false;
    for (index = 0; index < attempts.length; index += 1) {
        try {
            item.changeMediaPath.apply(item, attempts[index].args);
        } catch (changeError) {
            errors[errors.length] = attempts[index].kind + " -> " + DSH.errorMessage(changeError);
            continue;
        }
        var verify = DSH.api(item, "getMediaPath");
        var actual = null;
        if (verify) {
            try {
                var rawActual = verify.call(item);
                actual = (rawActual && rawActual !== 0) ? String(rawActual) : null;
            } catch (actualError) {
                actual = null;
            }
        }
        if (actual !== null && DSH.pathKey(actual) === DSH.pathKey(newPath)) {
            changed = true;
            break;
        }
        errors[errors.length] = attempts[index].kind + " -> 调用成功但读回路径仍是 " + String(actual);
    }
    if (!changed) {
        throw DSH.fail("HOST_ERROR", "重新链接未生效（期望 " + newPath + "）",
            "宿主可能因媒体一致性校验拒绝；可尝试 override_checks=true，"
                + "或在宿主界面手动重新链接",
            { detail: errors.join(" | ") });
    }
    var finalPath = newPath;
    var finalReader = DSH.api(item, "getMediaPath");
    if (finalReader) {
        try {
            var finalRaw = finalReader.call(item);
            if (finalRaw && finalRaw !== 0) { finalPath = String(finalRaw); }
        } catch (finalError) {
            finalPath = newPath;
        }
    }
    return { id: id, previous_path: previousPath, media_path: finalPath };
}

function probedName(item) {
    return DSH.hasProperty(item, "name") ? String(item.name) : null;
}

// ---- media.generate_proxies -----------------------------------------

function media_generate_proxies(args, ctx) {
    DSH.beginChecks(ctx);
    var itemRefs = DSH.argStringArray(args, "items", true);
    var presetPath = DSH.argString(args, "preset_path", false, null);
    var ingest = DSH.argBool(args, "ingest", true);
    DSH.requireProject();

    var items = [];
    var i;
    for (i = 0; i < itemRefs.length; i += 1) {
        items[items.length] = DSH.requireProjectItem(itemRefs[i]);
    }
    if (presetPath !== null && !DSH.fileExists(presetPath)) {
        throw DSH.fail("INVALID_ARGUMENT", "代理编码预设不存在：" + presetPath,
            "传一个真实存在的编码预设路径，或不传 preset_path（宿主用当前设置）");
    }

    var attached = [];
    var pending = [];
    var j;
    for (j = 0; j < items.length; j += 1) {
        var item = items[j];
        var itemId = DSH.hasProperty(item, "nodeId") ? String(item.nodeId) : itemRefs[j];
        var hasProxyFn = DSH.api(item, "hasProxy");
        var alreadyHas = false;
        if (hasProxyFn) {
            try {
                alreadyHas = hasProxyFn.call(item) === true;
            } catch (hasProxyError) {
                alreadyHas = false;
            }
        }
        if (alreadyHas) {
            attached[attached.length] = itemId;
            continue;
        }
        if (DSH.api(item, "canProxy")) {
            var canAttach = false;
            try {
                canAttach = item.canProxy() === true;
            } catch (canProxyError) {
                canAttach = false;
            }
            if (!canAttach) {
                throw DSH.fail("UNSUPPORTED_API", "该条目不允许附加代理：" + itemRefs[j],
                    "该素材类型（如合成素材）不支持代理；请从 items 中去掉它");
            }
        }
        pending[pending.length] = itemId;
    }
    DSH.note("all_items_have_proxies", pending.length === 0,
        pending.length ? (pending.length + " 个条目还没有代理") : null);

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            pending.length === 0
                ? ["所有 " + attached.length + " 个条目都已有代理，无需生成"]
                : ["为 " + pending.length + " 个条目生成代理（宿主无脚本化接口，预计失败）"],
            {
                job_id: null,
                attached: attached,
                pending: pending
            });
    }

    if (pending.length === 0) {
        // 已有代理：什么都不用做，如实返回（这不是"假装成功"，而是本来就没有待办）。
        return { job_id: null, attached: attached, pending: [] };
    }

    // 宿主没有脚本化的代理生成接口（公开 DOM 与已知 QE 都没有）——不伪造 job_id。
    throw DSH.fail("UNSUPPORTED_API",
        "宿主未提供脚本化的代理生成接口，" + pending.length + " 个条目无法通过脚本生成代理",
        "替代路径：① 用 ppro_export_encode_sequence 按代理预设导出代理媒体，"
            + "再用 media.relink 的 attachProxy 思路在宿主界面附加；"
            + "② 打开 首选项 > 媒体 > 代理 > 添加，或右键素材选\"生成代理\"；"
            + "③ 在导入前用 Ingest 设置自动生成代理",
        { detail: "待生成代理的条目：" + pending.join(" / "), ingest: ingest, preset_path: presetPath });
}
