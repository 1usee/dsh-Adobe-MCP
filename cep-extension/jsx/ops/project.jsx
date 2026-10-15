// =====================================================================
// ops/project.jsx —— 工程与素材箱处理器（契约 §5.3）
//   project.info        -> project_info
//   project.new         -> project_new
//   project.open        -> project_open
//   project.save        -> project_save
//   project.save_as     -> project_save_as
//   project.close       -> project_close
//   project.import      -> project_import
//   project.bin_create  -> project_bin_create
//   project.bin_list    -> project_bin_list
//   project.bin_move    -> project_bin_move
//   project.item_find   -> project_item_find
//
// 公开 API 依据（ppro-scripting 文档）：
//   app.newProject(path)、app.openDocument(path,...)、app.isDocument(path)、
//   app.isDocumentOpen()、project.save()、project.saveAs(path)、
//   project.closeDocument(saveFirst, promptIfDirty)、
//   project.importFiles(paths, suppressUI, targetBin, importAsNumberedStills)、
//   projectItem.createBin(name)、projectItem.moveBin(newParentBin)、
//   projectItem.findItemsMatchingMediaPath(path, ignoreSubClips)
// =====================================================================

if (typeof DSH === "undefined") { var DSH = {}; }

//: 工程路径比较用的规范化键（Windows 反斜杠 + 大小写不敏感）。
function dsh_projectPathKey(path) {
    if (!path) { return ""; }
    return String(path).replace(/\//g, "\\").toLowerCase();
}

//: 目标路径的前置检查：绝对路径、父目录存在、扩展名。
function dsh_checkTargetFile(args, name, extension, mustNotExist) {
    var value = DSH.argString(args, name, true, null);
    if (value.charAt(0) !== "\\" && value.charAt(0) !== "/" && value.indexOf(":") < 0) {
        throw DSH.argReport(name, "不是绝对路径：" + value, "传 Windows 绝对路径，例如 D:\\work\\a.prproj");
    }
    if (extension && value.toLowerCase().substr(value.length - extension.length) !== extension) {
        throw DSH.argReport(name, "扩展名必须是 " + extension + "：" + value);
    }
    DSH.note("target_parent_folder_exists", DSH.parentFolderExists(value),
        "父目录：" + value);
    if (!DSH.parentFolderExists(value)) {
        throw DSH.fail("INVALID_ARGUMENT", "目标路径的父目录不存在：" + value,
            "先创建目标目录；本工具不会替你创建目录");
    }
    if (mustNotExist) {
        var exists = DSH.fileExists(value);
        DSH.note("target_file_absent", !exists, exists ? "目标文件已存在" : null);
        if (exists) {
            throw DSH.fail("DIALOG_RISK", "目标文件已存在，写入会触发宿主覆盖确认对话框：" + value,
                "换一个不存在的路径；若确实要覆盖，请先在宿主内处理该文件");
        }
    }
    return value;
}

// ---- project.info -------------------------------------------------------

function project_info(args, ctx) {
    var snapshot = DSH.hostSnapshot();
    if (snapshot.has_project !== true) {
        return {
            has_project: false, name: null, path: null, dirty: null,
            sequences: [], bin_count: 0, item_count: 0
        };
    }
    var project = DSH.requireProject();
    var sequenceNames = [];
    try {
        var sequences = DSH.sequenceList();
        var i;
        for (i = 0; i < sequences.length; i += 1) {
            sequenceNames[sequenceNames.length] = DSH.hasProperty(sequences[i], "name")
                ? String(sequences[i].name) : null;
        }
    } catch (sequenceError) {
        sequenceNames = [];
    }
    var binCount = 0;
    var itemCount = 0;
    var root = project.rootItem;
    DSH.walkProjectItems(function (item) {
        if (item === root) { return; }          // 统计不含工程根
        itemCount += 1;
        if (DSH.isBinItem(item) && DSH.itemTypeName(item) !== "sequence") { binCount += 1; }
    });
    return {
        has_project: true,
        name: DSH.hasProperty(project, "name") ? String(project.name) : null,
        path: DSH.hasProperty(project, "path") ? String(project.path) : null,
        dirty: snapshot.dirty,
        sequences: sequenceNames,
        bin_count: binCount,
        item_count: itemCount
    };
}

// ---- project.new --------------------------------------------------------

function project_new(args, ctx) {
    DSH.beginChecks(ctx);
    var path = dsh_checkTargetFile(args, "path", ".prproj", true);
    DSH.requireApi(app, "newProject",
        "该宿主版本无法脚本化新建工程；请在宿主界面用 文件 > 新建 再重试", "Application");

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx, ["新建工程并保存到 " + path], { created: null, path: path });
    }

    var created = false;
    try {
        created = app.newProject(path) === true;
    } catch (newProjectError) {
        throw DSH.fail("HOST_ERROR", "app.newProject() 调用失败：" + DSH.errorMessage(newProjectError),
            "确认目标路径可写、且宿主没有未响应的对话框");
    }
    if (!created) {
        throw DSH.fail("HOST_ERROR", "app.newProject() 返回失败：" + path,
            "检查路径权限与磁盘空间；必要时在宿主界面手动新建");
    }
    var project = DSH.requireProject();
    var actual = DSH.hasProperty(project, "path") ? String(project.path) : "";
    if (dsh_projectPathKey(actual) !== dsh_projectPathKey(path)) {
        throw DSH.fail("HOST_ERROR",
            "新工程的路径与请求不一致：期望 " + path + "，实际 " + actual,
            "宿主可能改写了路径（扩展名/重名）；请用 project.info 确认真实路径");
    }
    return { created: true, path: actual };
}

// ---- project.open -------------------------------------------------------

function project_open(args, ctx) {
    DSH.beginChecks(ctx);
    var path = DSH.argString(args, "path", true, null);
    var exists = DSH.fileExists(path);
    DSH.note("project_file_exists", exists, path);
    if (!exists) {
        throw DSH.fail("INVALID_ARGUMENT", "工程文件不存在：" + path,
            "确认路径正确（UNC/长路径在宿主脚本里可能不可见）");
    }
    var isDocument = DSH.api(app, "isDocument");
    if (isDocument) {
        DSH.note("is_prproj_file", isDocument.call(app, path) === true, path);
        if (isDocument.call(app, path) !== true) {
            throw DSH.fail("INVALID_ARGUMENT", "该文件不是可打开的 Premiere Pro 工程：" + path,
                "确认文件是 .prproj（不是 .aep/.prtl 或损坏文件）");
        }
    }
    var dirty = DSH.projectDirty();
    if (dirty === true) {
        DSH.note("current_project_clean", false, "当前工程有未保存改动");
        throw DSH.fail("DIALOG_RISK",
            "当前工程有未保存改动，打开其它工程会触发宿主保存对话框",
            "先调用 ppro_project_save，再重试 ppro_project_open");
    }
    DSH.note("current_project_clean", true, dirty === null ? "宿主未提供脏标志（无法确认）" : null);
    DSH.requireApi(app, "openDocument",
        "该宿主版本无法脚本化打开工程；请在宿主界面打开后再调用其它工具", "Application");

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx, ["打开工程 " + path],
            { opened: null, path: path, name: null });
    }

    var opened = false;
    try {
        // 三个可选布尔位用于抑制转换/定位素材/警告对话框，避免脚本被模态框挂住。
        opened = app.openDocument(path, true, true, true) === true;
    } catch (openError) {
        throw DSH.fail("HOST_ERROR", "app.openDocument() 调用失败：" + DSH.errorMessage(openError),
            "确认工程未被其它进程锁定，且宿主没有未响应的对话框");
    }
    if (!opened) {
        throw DSH.fail("HOST_ERROR", "app.openDocument() 返回失败：" + path,
            "工程可能损坏或版本不兼容；请尝试在宿主界面手动打开确认");
    }
    var project = DSH.requireProject();
    var actual = DSH.hasProperty(project, "path") ? String(project.path) : "";
    if (dsh_projectPathKey(actual) !== dsh_projectPathKey(path)) {
        throw DSH.fail("HOST_ERROR",
            "打开的工程路径与请求不一致：期望 " + path + "，实际 " + actual,
            "可能有多个工程同时打开：请用 project.info 确认活动工程");
    }
    return {
        opened: true,
        path: actual,
        name: DSH.hasProperty(project, "name") ? String(project.name) : null
    };
}

// ---- project.save ------------------------------------------------------

function project_save(args, ctx) {
    DSH.beginChecks(ctx);
    var project = DSH.requireProject();
    var path = DSH.hasProperty(project, "path") ? String(project.path) : "";
    var hasPath = path !== "";
    DSH.note("project_has_path", hasPath, hasPath ? path : "工程从未保存过（无磁盘文件）");
    if (!hasPath) {
        throw DSH.fail("INVALID_ARGUMENT", "当前工程从未保存过，没有可用的现有路径",
            "改用 ppro_project_save_as 指定目标路径");
    }
    DSH.requireApi(project, "save", "该宿主版本无法脚本化保存工程；请在宿主界面保存", "Project");

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx, ["把工程保存到现有路径 " + path], { saved: null, path: path });
    }

    try {
        project.save();
    } catch (saveError) {
        throw DSH.fail("HOST_ERROR", "project.save() 调用失败：" + DSH.errorMessage(saveError),
            "确认磁盘可写、工程未被其它进程锁定");
    }
    var after = DSH.hasProperty(project, "path") ? String(project.path) : path;
    var dirty = DSH.projectDirty();
    if (dirty === true) {
        throw DSH.fail("HOST_ERROR", "project.save() 之后脏标志仍为 true，保存未生效",
            "检查磁盘权限；或在宿主界面手动保存并观察是否有报错");
    }
    return { saved: true, path: after };
}

// ---- project.save_as ---------------------------------------------------

function project_save_as(args, ctx) {
    DSH.beginChecks(ctx);
    DSH.requireProject();
    var path = dsh_checkTargetFile(args, "path", ".prproj", false);
    var project = DSH.requireProject();
    DSH.requireApi(project, "saveAs", "该宿主版本无法脚本化另存工程；请在宿主界面另存", "Project");

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx, ["把当前工程另存到 " + path], { saved: null, path: path });
    }

    try {
        project.saveAs(path);
    } catch (saveAsError) {
        throw DSH.fail("HOST_ERROR", "project.saveAs() 调用失败：" + DSH.errorMessage(saveAsError),
            "确认目标路径可写、且宿主没有未响应的对话框");
    }
    var actual = DSH.hasProperty(project, "path") ? String(project.path) : "";
    if (dsh_projectPathKey(actual) !== dsh_projectPathKey(path)) {
        throw DSH.fail("HOST_ERROR",
            "另存之后工程路径与请求不一致：期望 " + path + "，实际 " + actual,
            "宿主可能改写了文件名；请用 project.info 确认真实路径");
    }
    return { saved: true, path: actual };
}

// ---- project.close -----------------------------------------------------

function project_close(args, ctx) {
    DSH.beginChecks(ctx);
    var confirmDiscard = DSH.argBool(args, "confirm_discard", false);
    var project = DSH.requireProject();
    var dirty = DSH.projectDirty();
    var discard = confirmDiscard;
    DSH.note("unsaved_changes_handled", dirty === false || discard,
        dirty === true ? "有未保存改动" : (dirty === null ? "宿主未提供脏标志（按有改动处理）" : null));
    if (dirty !== false && !discard) {
        throw DSH.fail("DIALOG_RISK",
            dirty === true
                ? "当前工程有未保存改动，直接关闭会触发宿主的保存询问对话框"
                : "宿主未提供工程脏标志，无法确认是否有未保存改动；关闭可能触发保存询问对话框",
            "先调用 ppro_project_save 保存，或显式传 confirm_discard=true 表示丢弃改动");
    }
    DSH.requireApi(project, "closeDocument",
        "该宿主版本无法脚本化关闭工程；请在宿主界面关闭", "Project");

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx, ["关闭当前工程" + (discard ? "（丢弃未保存改动）" : "")],
            { closed: null, discarded: discard });
    }

    try {
        // saveFirst=0, promptIfDirty=0：脚本路径下绝不弹对话框。
        project.closeDocument(0, 0);
    } catch (closeError) {
        throw DSH.fail("HOST_ERROR", "project.closeDocument() 调用失败：" + DSH.errorMessage(closeError),
            "确认没有正在进行的后台编码/生成任务占用工程");
    }
    var stillOpen = false;
    var isDocumentOpen = DSH.api(app, "isDocumentOpen");
    if (isDocumentOpen) {
        try {
            stillOpen = isDocumentOpen.call(app) === true;
        } catch (documentOpenError) {
            stillOpen = false;
        }
    }
    if (stillOpen) {
        throw DSH.fail("HOST_ERROR", "调用 closeDocument 之后仍有打开的工程，关闭未生效",
            "可能还有其它工程处于打开状态（PPro 12+ 支持多工程）；用 project.info 确认");
    }
    return { closed: true, discarded: discard };
}

// ---- project.import ----------------------------------------------------

//: 按媒体路径找工程条目：优先用文档化的 findItemsMatchingMediaPath，
//: 拿到 0/空再用整树扫描兜底。
function dsh_findItemsByMediaPath(path) {
    var project = DSH.requireProject();
    var root = project.rootItem;
    var out = [];
    var findItems = DSH.api(root, "findItemsMatchingMediaPath");
    var i;
    if (findItems) {
        try {
            var found = findItems.call(root, path, 0);
            if (found && typeof found.length === "number") {
                for (i = 0; i < found.length; i += 1) {
                    if (found[i]) { out[out.length] = found[i]; }
                }
            }
        } catch (findError) {
            out = [];
        }
    }
    if (out.length > 0) { return out; }
    var wanted = dsh_projectPathKey(path);
    DSH.walkProjectItems(function (item) {
        var getMediaPath = DSH.api(item, "getMediaPath");
        if (!getMediaPath) { return; }
        var mediaPath = null;
        try {
            mediaPath = getMediaPath.call(item);
        } catch (mediaPathError) {
            mediaPath = null;
        }
        if (mediaPath && dsh_projectPathKey(mediaPath) === wanted) { out[out.length] = item; }
    });
    return out;
}

function project_import(args, ctx) {
    DSH.beginChecks(ctx);
    var paths = DSH.argStringArray(args, "paths", true);
    var binSelector = DSH.argString(args, "bin", false, null);
    var suppressUi = DSH.argBool(args, "suppress_ui", true);
    var project = DSH.requireProject();

    var bin = null;
    if (binSelector !== null) { bin = DSH.requireBin(binSelector); }

    var present = [];
    var missing = [];
    var i;
    for (i = 0; i < paths.length; i += 1) {
        if (DSH.fileExists(paths[i])) {
            present[present.length] = paths[i];
        } else {
            missing[missing.length] = paths[i];
        }
    }
    DSH.note("import_paths_exist", present.length > 0,
        present.length + " 个路径存在，" + missing.length + " 个不存在");
    if (present.length === 0) {
        throw DSH.fail("INVALID_ARGUMENT", "全部导入路径都不存在：" + missing.join(" / "),
            "确认路径正确，且素材对宿主脚本可见（网络盘/UNC 需先连接）");
    }
    DSH.requireApi(project, "importFiles",
        "该宿主版本无法脚本化导入；请在宿主界面导入后再调用其它工具", "Project");

    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["把 " + present.length + " 个文件导入到 "
                + (bin ? DSH.treePathSafe(bin) : "工程根")
                + (missing.length ? "（" + missing.length + " 个路径不存在，将跳过）" : "")],
            { imported: null, missing: missing });
    }

    var result = false;
    try {
        result = project.importFiles(present, suppressUi === true, bin ? bin : project.rootItem, false);
    } catch (importError) {
        throw DSH.fail("HOST_ERROR", "project.importFiles() 调用失败：" + DSH.errorMessage(importError),
            "确认素材格式受宿主支持、磁盘可读；有模态对话框时先处理宿主窗口");
    }
    if (result !== true) {
        throw DSH.fail("HOST_ERROR", "project.importFiles() 返回失败：" + present.join(" / "),
            "宿主可能拒绝了部分素材（编解码器不支持）；请检查宿主事件面板");
    }

    var imported = [];
    var remaining = [];
    var j;
    for (j = 0; j < present.length; j += 1) {
        var items = dsh_findItemsByMediaPath(present[j]);
        if (items.length === 0) {
            remaining[remaining.length] = present[j];
            continue;
        }
        var summary = DSH.itemSummary(items[items.length - 1]);
        summary.path = summary.path ? summary.path : present[j];
        imported[imported.length] = summary;
    }
    if (remaining.length > 0) {
        // 导入 API 返回 true 但条目找不到：不假装成功，把差异如实报出来。
        throw DSH.fail("HOST_ERROR",
            "importFiles 返回成功，但以下素材在工程里找不到对应条目：" + remaining.join(" / "),
            "素材可能被合并到已有条目或落在别的素材箱；用 ppro_project_item_find 按文件名查找",
            { detail: "已解析 " + imported.length + " 个条目" });
    }
    return { imported: imported, missing: missing };
}

// ---- project.bin_create ------------------------------------------------

function project_bin_create(args, ctx) {
    DSH.beginChecks(ctx);
    var name = DSH.argString(args, "name", true, null);
    var parentSelector = DSH.argString(args, "parent", false, null);
    var parent = DSH.requireBin(parentSelector);

    var siblings = DSH.childItems(parent);
    var duplicate = false;
    var i;
    for (i = 0; i < siblings.length; i += 1) {
        var siblingName = DSH.hasProperty(siblings[i], "name") ? String(siblings[i].name) : "";
        if (siblingName.toLowerCase() === name.toLowerCase()) { duplicate = true; }
    }
    DSH.note("bin_name_unique", !duplicate, duplicate ? ("同名条目已存在：" + name) : null);
    if (duplicate) {
        throw DSH.fail("DUPLICATE_NAME", "目标素材箱下已存在同名条目：" + name,
            "换一个名称，或先用 ppro_project_bin_move/重命名整理现有条目");
    }
    DSH.requireApi(parent, "createBin",
        "该宿主版本无法在素材箱内新建素材箱；请在宿主界面新建", "ProjectItem");

    var parentPath = DSH.treePathSafe(parent);
    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx, ["在 " + (parentPath ? parentPath : "工程根") + " 下新建素材箱 " + name],
            { created: null, path: null, name: name });
    }

    var bin = null;
    try {
        bin = parent.createBin(name);
    } catch (createError) {
        throw DSH.fail("HOST_ERROR", "createBin() 调用失败：" + DSH.errorMessage(createError),
            "确认父条目确实是素材箱（不是序列/素材）");
    }
    if (!bin || bin === 0) {
        throw DSH.fail("HOST_ERROR", "createBin() 未返回新建的素材箱：" + name,
            "宿主可能因重名/权限拒绝；请在宿主界面确认");
    }
    var createdPath = DSH.treePathSafe(bin);
    if (!createdPath) {
        throw DSH.fail("HOST_ERROR", "新建的素材箱没有可读的 treePath，无法确认结果",
            "请在宿主界面确认素材箱是否已创建；这通常说明宿主版本行为不同");
    }
    return { created: true, path: createdPath, name: name };
}

// ---- project.bin_list --------------------------------------------------

function project_bin_list(args, ctx) {
    var parentSelector = DSH.argString(args, "parent", false, null);
    DSH.requireProject();
    var bins = [];
    if (parentSelector !== null) {
        var parent = DSH.requireBin(parentSelector);
        var children = DSH.childItems(parent);
        var i;
        for (i = 0; i < children.length; i += 1) {
            if (DSH.itemTypeName(children[i]) === "bin") { bins[bins.length] = children[i]; }
        }
    } else {
        DSH.walkProjectItems(function (item) {
            if (DSH.itemTypeName(item) === "bin") { bins[bins.length] = item; }
        });
    }
    var out = [];
    var j;
    for (j = 0; j < bins.length; j += 1) {
        var list = DSH.childItems(bins[j]);
        var childBins = 0;
        var k;
        for (k = 0; k < list.length; k += 1) {
            if (DSH.itemTypeName(list[k]) === "bin") { childBins += 1; }
        }
        out[out.length] = {
            path: DSH.treePathSafe(bins[j]),
            name: DSH.hasProperty(bins[j], "name") ? String(bins[j].name) : null,
            item_count: list.length,
            child_count: childBins
        };
    }
    return { bins: out };
}

// ---- project.bin_move -------------------------------------------------

//: 目标素材箱是否是某个条目的自身或其后代（非法移动）。
function dsh_isSelfOrDescendant(item, candidate) {
    var itemPath = DSH.pathKey(DSH.treePathSafe(item));
    var candidatePath = DSH.pathKey(DSH.treePathSafe(candidate));
    if (itemPath === "" || candidatePath === "") { return false; }
    if (itemPath === candidatePath) { return true; }
    return candidatePath.indexOf(itemPath + "\\") === 0;
}

function project_bin_move(args, ctx) {
    DSH.beginChecks(ctx);
    var itemRefs = DSH.argStringArray(args, "items", true);
    var targetSelector = DSH.argString(args, "target_bin", true, null);
    var target = DSH.requireBin(targetSelector);

    var items = [];
    var i;
    for (i = 0; i < itemRefs.length; i += 1) {
        var item = DSH.findProjectItem(itemRefs[i]);
        DSH.note("move_source_" + i, !!item,
            item ? DSH.treePathSafe(item) : ("找不到条目 " + itemRefs[i]));
        if (!item) {
            throw DSH.fail("ITEM_NOT_FOUND", "找不到要移动的条目：" + itemRefs[i],
                "先用 ppro_project_item_find 确认真实路径或 nodeId");
        }
        if (dsh_isSelfOrDescendant(item, target)) {
            throw DSH.fail("INVALID_ARGUMENT",
                "不能把条目移动到它自己或它的子素材箱里：" + itemRefs[i],
                "目标素材箱必须是该条目的祖先之外的位置");
        }
        items[items.length] = item;
    }
    var targetPath = DSH.pathKey(DSH.treePathSafe(target));
    if (ctx.dryRun) {
        return DSH.dryRunResult(ctx,
            ["把 " + items.length + " 个条目移动到 " + DSH.treePathSafe(target)],
            { moved: null, failed: [] });
    }

    var moved = 0;
    var failed = [];
    var j;
    for (j = 0; j < items.length; j += 1) {
        var moveBin = DSH.api(items[j], "moveBin");
        if (!moveBin) {
            failed[failed.length] = {
                item: itemRefs[j], code: "UNSUPPORTED_API",
                message: "该宿主版本无法脚本化移动工程条目"
            };
            continue;
        }
        var sourcePath = DSH.pathKey(DSH.treePathSafe(items[j]));
        try {
            moveBin.call(items[j], target);
        } catch (moveError) {
            failed[failed.length] = {
                item: itemRefs[j], code: "HOST_ERROR", message: DSH.errorMessage(moveError)
            };
            continue;
        }
        var newPath = DSH.pathKey(DSH.treePathSafe(items[j]));
        if (newPath === sourcePath || newPath.indexOf(targetPath) !== 0) {
            failed[failed.length] = {
                item: itemRefs[j], code: "HOST_ERROR",
                message: "moveBin 之后路径仍是 " + DSH.treePathSafe(items[j]) + "（未生效）"
            };
            continue;
        }
        moved += 1;
    }
    return { moved: moved, failed: failed };
}

// ---- project.item_find -------------------------------------------------

function dsh_itemKindMatches(kind, item) {
    if (kind === "any") { return true; }
    var typeName = DSH.itemTypeName(item);
    if (kind === "bin") { return typeName === "bin"; }
    if (kind === "sequence") { return typeName === "sequence"; }
    if (kind === "file") { return typeName === "file"; }
    if (kind === "clip") { return typeName === "clip" || typeName === "file"; }
    return true;
}

function project_item_find(args, ctx) {
    var query = DSH.argString(args, "query", true, null);
    var kind = DSH.argEnum(args, "kind", ["any", "clip", "bin", "sequence", "file"], "any");
    var limit = DSH.argInt(args, "limit", false, 50);
    if (limit <= 0) { throw DSH.argReport("limit", "必须大于 0"); }
    var project = DSH.requireProject();
    var root = project.rootItem;
    var needle = query.toLowerCase();
    var matched = [];
    var truncated = false;
    DSH.walkProjectItems(function (item) {
        if (item === root) { return; }
        if (!dsh_itemKindMatches(kind, item)) { return; }
        var name = DSH.hasProperty(item, "name") ? String(item.name) : "";
        var path = DSH.treePathSafe(item);
        var haystack = (name + " " + (path ? path : "")).toLowerCase();
        if (haystack.indexOf(needle) < 0) { return; }
        if (matched.length >= limit) {
            truncated = true;
            return;
        }
        matched[matched.length] = DSH.itemSummary(item);
    });
    return { items: matched, truncated: truncated };
}
