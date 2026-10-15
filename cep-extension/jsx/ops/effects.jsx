// =====================================================================
// ops/effects.jsx —— 效果与参数处理器（契约 §5.3）
//   effects.list_available  -> effects_list_available
//   effects.apply           -> effects_apply            （qe：未公开接口）
//   effects.get_params      -> effects_get_params
//   effects.set_param       -> effects_set_param
//   effects.apply_transition-> effects_apply_transition （qe：未公开接口）
//
// 公开 API 依据（ppro-scripting 文档）：
//   TrackItem.components（ComponentCollection）、Component.displayName/matchName/
//   properties（ComponentParamCollection）、ComponentParam.displayName、
//   getValue()/setValue(value, updateUI)/setColorValue(alpha,red,green,blue,updateUI)/
//   isTimeVarying()/areKeyframesSupported()/getKeys()/getValueAtTime(time)、
//   Track.transitions
//
// 重要事实（文档核实）：**公开 DOM 没有"给片段添加效果"的接口**，
// Component 只能读。因此 effects.apply / effects.apply_transition 走 QE
// （capability=qe，dispatch.jsx 已做门禁）；effects.list_available 也没有公开的
// 效果枚举接口，只能在 enable_qe 下用 QE 枚举，否则如实报 UNSUPPORTED_API。
// =====================================================================

if (typeof DSH === "undefined") { var DSH = {}; }

//: 取 ComponentParam 的名字（displayName 是文档化的；name/matchName 是常见但
//: 未文档化的成员，存在就一并返回）。
function dsh_paramNames(param) {
    var names = { name: null, match_name: null };
    if (DSH.hasProperty(param, "displayName")) { names.name = String(param.displayName); }
    if (DSH.hasProperty(param, "name")) { names.name = String(param.name); }
    if (DSH.hasProperty(param, "matchName")) { names.match_name = String(param.matchName); }
    return names;
}

function dsh_componentNames(component) {
    var names = { name: null, match_name: null };
    if (DSH.hasProperty(component, "displayName")) { names.name = String(component.displayName); }
    if (DSH.hasProperty(component, "matchName")) { names.match_name = String(component.matchName); }
    return names;
}

//: 参数当前值。时间变化的参数 getValue() 会失败——那就如实返回 null，
//: 并由调用方在结构里标记 time_varying，绝不编造一个值。
function dsh_paramValue(param) {
    var getValue = DSH.api(param, "getValue");
    if (!getValue) { return { value: null, readable: false, reason: "该参数没有 getValue()" }; }
    try {
        return { value: DSH.sanitizeValue(getValue.call(param)), readable: true, reason: null };
    } catch (valueError) {
        return { value: null, readable: false, reason: DSH.errorMessage(valueError) };
    }
}

function dsh_paramTypeName(value) {
    if (value === null || typeof value === "undefined") { return "null"; }
    var type = typeof value;
    if (type === "number" || type === "string" || type === "boolean") { return type; }
    if (Object.prototype.toString.apply(value) === "[object Array]") { return "array"; }
    return "object";
}

function dsh_componentList(item) {
    var components = null;
    try {
        components = item.components;
    } catch (componentsError) {
        components = null;
    }
    var out = [];
    if (!components) { return out; }
    var count = DSH.collectionCount(components);
    var i;
    for (i = 0; i < count; i += 1) {
        var component = DSH.collectionAt(components, i);
        if (component) { out[out.length] = { index: i, component: component }; }
    }
    return out;
}

function dsh_paramList(component) {
    var properties = null;
    try {
        properties = component.properties;
    } catch (propertiesError) {
        properties = null;
    }
    var out = [];
    if (!properties) { return out; }
    var count = DSH.collectionCount(properties);
    var i;
    for (i = 0; i < count; i += 1) {
        var param = DSH.collectionAt(properties, i);
        if (param) { out[out.length] = { index: i, param: param }; }
    }
    return out;
}

// ---- effects.list_available -------------------------------------------

function effects_list_available(args, ctx) {
    var target = DSH.argEnum(args, "target",
        ["clip", "video_track", "audio_track", "sequence", "master"], "clip");
    var query = DSH.argString(args, "query", false, null);
    var mediaType = (target === "audio_track") ? "audio" : "video";

    if (!DSH.qeEnabled()) {
        throw DSH.fail("UNSUPPORTED_API",
            "公开 DOM 没有枚举可用效果的接口（Component 只能从已有片段读），且 QE 未启用",
            "设置 extendscript.enable_qe = true 后重启面板可尝试用 QE 枚举；"
                + "也可以直接对已有效果的片段调用 ppro_effects_get_params 读取参数。"
                + "注意 QE 是未公开接口，可能导致宿主崩溃");
    }

    var names = DSH.qe.effectList(mediaType);
    var needle = query ? query.toLowerCase() : null;
    var effects = [];
    var truncated = false;
    var i;
    for (i = 0; i < names.length; i += 1) {
        if (needle !== null && names[i].toLowerCase().indexOf(needle) < 0) { continue; }
        if (effects.length >= 500) { truncated = true; break; }
        effects[effects.length] = { name: names[i], media_type: mediaType };
    }
    return {
        effects: effects,
        truncated: truncated,
        // QE 枚举是"全量效果名"，宿主不暴露 target 作用域：如实说明而不是假装已按 target 过滤
        note: "效果清单来自未公开的 QE 接口，无法按 target=" + target
            + " 过滤（宿主未暴露该信息）；请在使用前用 effects.get_params 确认该目标上是否可直接施加"
    };
}

// ---- effects.get_params ----------------------------------------------

function effects_get_params(args, ctx) {
    var selector = DSH.argString(args, "sequence", false, null);
    var ref = DSH.argString(args, "ref", true, null);
    var sequence = DSH.requireSequence(selector);
    var located = DSH.requireTrackItem(sequence, ref);
    var components = dsh_componentList(located.item);
    var out = [];
    var i;
    for (i = 0; i < components.length; i += 1) {
        var componentNames = dsh_componentNames(components[i].component);
        var params = dsh_paramList(components[i].component);
        var properties = [];
        var j;
        for (j = 0; j < params.length; j += 1) {
            var names = dsh_paramNames(params[j].param);
            var read = dsh_paramValue(params[j].param);
            var timeVarying = null;
            var isTimeVarying = DSH.api(params[j].param, "isTimeVarying");
            if (isTimeVarying) {
                try {
                    timeVarying = isTimeVarying.call(params[j].param) === true;
                } catch (timeVaryingError) {
                    timeVarying = null;
                }
            }
            properties[properties.length] = {
                index: params[j].index,
                name: names.name,
                match_name: names.match_name,
                value: read.value,
                type: dsh_paramTypeName(read.value),
                value_readable: read.readable,
                value_note: read.reason,
                time_varying: timeVarying
            };
        }
        out[out.length] = {
            index: components[i].index,
            name: componentNames.name,
            match_name: componentNames.match_name,
            properties: properties
        };
    }
    return { components: out };
}

// ---- effects.set_param -----------------------------------------------

function dsh_findComponent(located, componentSelector) {
    var components = dsh_componentList(located.item);
    if (components.length === 0) {
        throw DSH.fail("NOT_FOUND", "片段 " + located.ref + " 上没有任何效果组件",
            "先用 ppro_effects_get_params 查看已有组件；施加效果需要 enable_qe 下的 QE 路径");
    }
    if (componentSelector === null || componentSelector === "") {
        return components[0];
    }
    var needle = String(componentSelector);
    var numeric = Number(needle);
    var i;
    if (isFinite(numeric) && Math.round(numeric) === numeric && numeric >= 0) {
        for (i = 0; i < components.length; i += 1) {
            if (components[i].index === numeric) { return components[i]; }
        }
    }
    var lower = needle.toLowerCase();
    for (i = 0; i < components.length; i += 1) {
        var names = dsh_componentNames(components[i].component);
        var candidates = [names.name, names.match_name];
        var k;
        for (k = 0; k < candidates.length; k += 1) {
            if (candidates[k] !== null && String(candidates[k]).toLowerCase() === lower) {
                return components[i];
            }
        }
    }
    throw DSH.fail("NOT_FOUND", "片段 " + located.ref + " 上找不到组件：" + componentSelector,
        "用 ppro_effects_get_params 查看组件的 name/match_name/index");
}

function dsh_findParam(componentEntry, paramSelector) {
    var params = dsh_paramList(componentEntry.component);
    var needle = String(paramSelector);
    var lower = needle.toLowerCase();
    var i;
    for (i = 0; i < params.length; i += 1) {
        var names = dsh_paramNames(params[i].param);
        var candidates = [names.name, names.match_name, params[i].index];
        var k;
        for (k = 0; k < candidates.length; k += 1) {
            if (candidates[k] === null || typeof candidates[k] === "undefined") { continue; }
            if (String(candidates[k]).toLowerCase() === lower
                    || String(candidates[k]) === needle) {
                return params[i];
            }
        }
    }
    throw DSH.fail("NOT_FOUND", "组件里找不到参数：" + paramSelector,
        "用 ppro_effects_get_params 查看该组件参数的 name/match_name/index");
}

function effects_set_param(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var ref = DSH.argString(args, "ref", true, null);
    var componentSelector = DSH.argString(args, "component", false, null);
    var paramSelector = DSH.argString(args, "param", true, null);
    if (!DSH.hasArg(args, "value")) {
        throw DSH.argReport("value", "缺失（必需，any）");
    }
    var value = args.value;
    var sequence = DSH.requireSequence(selector);
    var located = DSH.requireTrackItem(sequence, ref);
    var componentEntry = dsh_findComponent(located, componentSelector);
    var paramEntry = dsh_findParam(componentEntry, paramSelector);
    var componentNames = dsh_componentNames(componentEntry.component);
    var paramNames = dsh_paramNames(paramEntry.param);
    var previous = dsh_paramValue(paramEntry.param);
    var setValue = DSH.api(paramEntry.param, "setValue");
    var setColorValue = DSH.api(paramEntry.param, "setColorValue");
    var isColor = (value !== null && typeof value === "object"
        && typeof value.red !== "undefined" && typeof value.green !== "undefined"
        && typeof value.blue !== "undefined");
    DSH.note("param_writable", !!(isColor ? setColorValue : setValue),
        (isColor ? "setColorValue" : "setValue")
            + (isColor ? (setColorValue ? " 可用" : " 不可用") : (setValue ? " 可用" : " 不可用")));
    if (isColor) {
        if (!setColorValue) {
            throw DSH.fail("UNSUPPORTED_API", "该参数不接受颜色对象（没有 setColorValue）",
                "改用数值参数，或传 {alpha,red,green,blue} 时确认宿主支持 setColorValue");
        }
    } else if (!setValue) {
        throw DSH.fail("UNSUPPORTED_API", "该参数没有 setValue(value, updateUI) 接口",
            "该宿主版本无法脚本化写这个参数");
    }

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["把片段 " + located.ref + " 的组件 " + (componentNames.name ? componentNames.name : componentEntry.index)
                + " 参数 " + (paramNames.name ? paramNames.name : paramEntry.index)
                + " 从 " + String(previous.value) + " 改为 " + String(value)],
            {
                ref: located.ref,
                component: componentNames.name ? componentNames.name : componentEntry.index,
                param: paramNames.name ? paramNames.name : paramEntry.index,
                previous_value: previous.value,
                value: DSH.sanitizeValue(value)
            });
    }

    if (isColor) {
        try {
            setColorValue.call(paramEntry.param,
                Number(value.alpha ? value.alpha : 1), Number(value.red),
                Number(value.green), Number(value.blue), 1);
        } catch (colorError) {
            throw DSH.fail("HOST_ERROR", "setColorValue() 调用失败：" + DSH.errorMessage(colorError),
                "确认颜色分量范围（宿主通常要求 0..1 或 0..255）");
        }
    } else {
        var numeric = (typeof value === "number");
        var targetValue = numeric ? Number(value) : value;
        try {
            setValue.call(paramEntry.param, targetValue, 1);
        } catch (setError) {
            throw DSH.fail("HOST_ERROR", "setValue() 调用失败：" + DSH.errorMessage(setError),
                "参数可能是时间变化的（需要关键帧接口）或类型不匹配；"
                    + "先用 ppro_effects_get_params 确认参数的 type/time_varying");
        }
    }

    var after = dsh_paramValue(paramEntry.param);
    var matched = false;
    if (after.readable) {
        if (typeof value === "number") {
            matched = DSH.nearlyEqual(after.value, Number(value), 0.0001);
        } else if (isColor) {
            matched = true; // 颜色读回是宿主对象，逐分量比较不可靠：明确标记为未逐项校验
        } else {
            matched = String(after.value) === String(value);
        }
    }
    if (!after.readable) {
        throw DSH.fail("HOST_ERROR",
            "写入后无法读回参数值（" + after.reason + "），不能确认是否生效",
            "该参数可能是时间变化的；请用宿主界面确认参数值，或改用其它参数");
    }
    if (!matched && !isColor) {
        throw DSH.fail("HOST_ERROR",
            "参数写入后读回不一致：期望 " + String(value) + "，实际 " + String(after.value),
            "宿主可能按参数类型做了量化（如整数化/范围钳制）；请用 ppro_effects_get_params 确认");
    }
    return {
        ref: located.ref,
        component: componentNames.name ? componentNames.name : componentEntry.index,
        param: paramNames.name ? paramNames.name : paramEntry.index,
        previous_value: previous.value,
        value: after.value,
        verified: !isColor
    };
}

// ---- effects.apply（QE） ---------------------------------------------

function effects_apply(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var ref = DSH.argString(args, "ref", true, null);
    var effect = DSH.argString(args, "effect", true, null);
    var sequence = DSH.requireSequence(selector);
    var located = DSH.requireTrackItem(sequence, ref);
    var before = dsh_componentList(located.item).length;
    DSH.note("effect_name_given", effect !== "", effect);

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["给片段 " + located.ref + " 施加效果 " + effect + "（QE 未公开接口，有宿主崩溃风险）"],
            { ref: located.ref, effect: effect, component_index: null });
    }

    var qeLocated = DSH.qe.trackItem(sequence, located.ref);
    DSH.qe.addVideoEffect(qeLocated.item, effect);
    var components = dsh_componentList(located.item);
    if (components.length <= before) {
        throw DSH.fail("HOST_ERROR",
            "QE 施加效果后组件数没有增加（" + before + " -> " + components.length + "）",
            "效果名可能不是宿主的 Display Name，或该效果不能施加到片段上；"
                + "QE 静默失败很常见，且它是未公开接口");
    }
    return {
        ref: located.ref, effect: effect,
        component_index: components[components.length - 1].index
    };
}

// ---- effects.apply_transition（QE） ----------------------------------

function effects_apply_transition(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var ref = DSH.argString(args, "ref", true, null);
    var transition = DSH.argString(args, "transition", true, null);
    var at = DSH.argEnum(args, "at", ["start", "end"], "start");
    var sequence = DSH.requireSequence(selector);
    var located = DSH.requireTrackItem(sequence, ref);
    // 校验手段：轨道 transitions 集合（文档化的 Track.transitions）+ 片段的组件数。
    var transitionsBefore = null;
    try {
        if (located.track.transitions) {
            transitionsBefore = DSH.collectionCount(located.track.transitions);
        }
    } catch (transitionsError) {
        transitionsBefore = null;
    }
    var componentsBefore = dsh_componentList(located.item).length;
    DSH.note("transition_verifiable", transitionsBefore !== null,
        transitionsBefore !== null ? null : "宿主未提供 Track.transitions，无法校验转场是否真的加上");

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["给片段 " + located.ref + " 的" + (at === "start" ? "起点" : "终点") + "添加转场 "
                + transition + "（QE 未公开接口，有宿主崩溃风险）"],
            { ref: located.ref, transition: transition, at: at });
    }

    var qeLocated = DSH.qe.trackItem(sequence, located.ref);
    DSH.qe.addTransition(qeLocated.item, transition, at === "start");
    var transitionsAfter = null;
    try {
        if (located.track.transitions) {
            transitionsAfter = DSH.collectionCount(located.track.transitions);
        }
    } catch (transitionsAfterError) {
        transitionsAfter = null;
    }
    var componentsAfter = dsh_componentList(located.item).length;
    if (transitionsBefore !== null && transitionsAfter !== null) {
        if (transitionsAfter <= transitionsBefore && componentsAfter <= componentsBefore) {
            throw DSH.fail("HOST_ERROR",
                "QE 添加转场后轨道转场数与组件数都没有变化（转场 " + transitionsBefore + " -> "
                    + transitionsAfter + "，组件 " + componentsBefore + " -> " + componentsAfter + "）",
                "转场名可能不是宿主的 Display Name，或该片段端点不支持该转场；QE 静默失败很常见");
        }
    }
    return {
        ref: located.ref,
        transition: transition,
        at: at,
        // 读回手段缺失时如实说明"未校验"，不假装已验证
        verified: (transitionsBefore !== null && transitionsAfter !== null)
            || componentsAfter > componentsBefore
    };
}
