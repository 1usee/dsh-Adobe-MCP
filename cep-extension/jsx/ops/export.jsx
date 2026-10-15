// =====================================================================
// ops/export.jsx —— 编码导出处理器（契约 §5.3）
//   export.encode_sequence -> export_encode_sequence   （job）
//   export.encode_file     -> export_encode_file       （job）
//   export.job_status      -> export_job_status
//
// 公开 API 依据（ppro-scripting 文档）：
//   app.encoder.encodeSequence(sequence, outputPath, presetPath, workArea, removeUponCompletion)
//     -> 返回 AME 队列里的 job id 字符串（失败返回 0）
//   app.encoder.encodeProjectItem(projectItem, outputPath, presetPath, workArea, removeUponCompletion)
//   app.encoder.encodeFile(filePath, outputPath, presetPath, workArea, removeUponCompletion, in, out)
//   workArea 取值：0=ENTIRE、1=IN_TO_OUT、2=WORK_AREA
//
// 长任务模型（说明书 §9.3）：这些命令只负责"入队"就返回，在途求值到提交为止，
// 不违反约束 C-2。**Encoder 对象没有任何文档化的任务状态查询接口**，
// 所以 export.job_status 在拿不到宿主状态时明确返回 state="unknown"（不猜）。
// =====================================================================

if (typeof DSH === "undefined") { var DSH = {}; }

//: 契约的 work_area 枚举 -> 宿主 encoder 的 workArea 整数。
var DSH_ENCODE_WORK_AREA = {
    "entire": 0,
    "in_out": 1
};

function dsh_encoder() {
    var encoder = null;
    try {
        encoder = (typeof app !== "undefined" && app) ? app.encoder : null;
    } catch (encoderError) {
        encoder = null;
    }
    if (!encoder) {
        throw DSH.fail("UNSUPPORTED_API", "当前宿主没有 app.encoder（Adobe Media Encoder 接口）",
            "确认宿主与 AME 安装完整；也可在宿主界面用\"导出\"菜单手工导出");
    }
    return encoder;
}

//: 编码前置检查：输出目录可写（父目录存在）+ 预设文件存在。
function dsh_checkEncodeTarget(outputPath, presetPath) {
    if (!DSH.parentFolderExists(outputPath)) {
        throw DSH.fail("INVALID_ARGUMENT", "输出路径的父目录不存在：" + outputPath,
            "先创建输出目录；本工具不会替你创建目录");
    }
    DSH.note("output_parent_folder_exists", true, outputPath);
    if (!DSH.fileExists(presetPath)) {
        throw DSH.fail("INVALID_ARGUMENT", "编码预设文件不存在：" + presetPath,
            "传一个真实存在的 .epr 预设路径（宿主自带预设在程序目录的 Settings 下）");
    }
    DSH.note("preset_file_exists", true, presetPath);
}

function export_encode_sequence(args, ctx) {
    DSH.beginChecks(ctx);
    var selector = DSH.argString(args, "sequence", false, null);
    var outputPath = DSH.argString(args, "output_path", true, null);
    var presetPath = DSH.argString(args, "preset_path", true, null);
    var workArea = DSH.argEnum(args, "work_area", ["entire", "in_out"], "entire");
    var removeFromQueue = DSH.argBool(args, "remove_from_queue", true);
    var sequence = DSH.requireSequence(selector);
    dsh_checkEncodeTarget(outputPath, presetPath);
    var encoder = dsh_encoder();
    DSH.requireApi(encoder, "encodeSequence",
        "该宿主版本的 app.encoder 没有 encodeSequence；无法脚本化入队导出", "Encoder");
    var sequenceName = DSH.hasProperty(sequence, "name") ? String(sequence.name) : null;

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["把序列 " + sequenceName + " 按预设 " + presetPath + " 排入编码队列，输出 " + outputPath
                + "（范围：" + workArea + "）"],
            { job_id: null, output_path: outputPath, sequence: sequenceName, queued: null });
    }

    var jobId = null;
    try {
        jobId = encoder.encodeSequence(sequence, outputPath, presetPath,
            DSH_ENCODE_WORK_AREA[workArea], removeFromQueue ? 1 : 0);
    } catch (encodeError) {
        throw DSH.fail("HOST_ERROR", "encodeSequence() 调用失败：" + DSH.errorMessage(encodeError),
            "确认 AME 已安装且可启动、预设与宿主版本匹配");
    }
    if (!jobId || jobId === 0) {
        throw DSH.fail("HOST_ERROR", "encodeSequence() 没有返回 job id（返回 " + String(jobId) + "）",
            "宿主可能拒绝了该预设或输出路径；请在宿主界面手动导出确认");
    }
    return {
        job_id: String(jobId),
        output_path: outputPath,
        sequence: sequenceName,
        queued: true
    };
}

function export_encode_file(args, ctx) {
    DSH.beginChecks(ctx);
    var itemRef = DSH.argString(args, "item", true, null);
    var outputPath = DSH.argString(args, "output_path", true, null);
    var presetPath = DSH.argString(args, "preset_path", true, null);
    var removeFromQueue = DSH.argBool(args, "remove_from_queue", true);
    var item = DSH.requireProjectItem(itemRef);
    var typeName = DSH.itemTypeName(item);
    DSH.note("item_is_encodable", typeName !== "bin",
        "条目类型：" + typeName);
    if (typeName === "bin") {
        throw DSH.fail("INVALID_ARGUMENT", "素材箱不能直接导出：" + itemRef,
            "传一个媒体素材（或序列条目）的路径/nodeId");
    }
    dsh_checkEncodeTarget(outputPath, presetPath);
    var encoder = dsh_encoder();
    DSH.requireApi(encoder, "encodeProjectItem",
        "该宿主版本的 app.encoder 没有 encodeProjectItem；无法脚本化入队导出", "Encoder");
    var itemName = DSH.hasProperty(item, "name") ? String(item.name) : itemRef;

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["把条目 " + itemName + " 按预设 " + presetPath + " 排入编码队列，输出 " + outputPath],
            { job_id: null, output_path: outputPath, item: itemRef, queued: null });
    }

    var jobId = null;
    try {
        // workArea=0（整段）：条目没有序列意义上的入出点。
        jobId = encoder.encodeProjectItem(item, outputPath, presetPath, 0, removeFromQueue ? 1 : 0);
    } catch (encodeError) {
        throw DSH.fail("HOST_ERROR", "encodeProjectItem() 调用失败：" + DSH.errorMessage(encodeError),
            "确认 AME 已安装且可启动、预设与素材匹配");
    }
    if (!jobId || jobId === 0) {
        throw DSH.fail("HOST_ERROR", "encodeProjectItem() 没有返回 job id（返回 " + String(jobId) + "）",
            "宿主可能拒绝了该预设或输出路径；请在宿主界面手动导出确认");
    }
    return {
        job_id: String(jobId),
        output_path: outputPath,
        item: itemRef,
        queued: true
    };
}

function export_job_status(args, ctx) {
    var jobId = DSH.argString(args, "job_id", true, null);
    // 宿主侧没有文档化的任务状态接口。若将来确认存在（例如 encoder.encodeManager），
    // 应在这里做特性检测并补充实现；当前如实返回 unknown，由 L1 结合输出文件增长判定。
    var state = "unknown";
    var message = "宿主公开 DOM 未提供编码任务状态查询接口（app.encoder 只有入队方法），"
        + "无法确定该 job 的进度；L1 侧应结合输出文件是否增长/存在来判定";
    return {
        job_id: jobId,
        state: state,
        progress: null,
        message: message
    };
}
