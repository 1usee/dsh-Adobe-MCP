"""由 ``commands.json`` 生成 ``cep-extension/jsx/dispatch.jsx``（说明书 §14.2.2）。

**为什么要生成**：如果路由表手写，L1 的契约与 L3 的路由就会各自演化；
历史上这类"两侧各写一份"的设计必然漂移。因此路由表由契约生成，
从结构上消除漂移的可能，剩下的只有"契约本身写对没有"这一件事。

生成物只包含：路由表 + 命令分发逻辑（幂等键、dry-run 标志、QE 门禁、错误包装）。
各命令的业务实现位于 ``jsx/ops/*.jsx``，由契约的 ``ops_file`` / ``handler`` 指定。

用法::

    python scripts/gen_jsx_dispatch.py            # 写入文件
    python scripts/gen_jsx_dispatch.py --check    # 只校验是否与契约同步（CI 门禁）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401  (把 src/ 加入 sys.path)

from dsh_adobe_mcp.contract import CommandSpec, Contract, load_contract, repo_root

GENERATED_HEADER = """\
// =====================================================================
// 自动生成文件 —— 请勿手工编辑。
// 由 scripts/gen_jsx_dispatch.py 从 cep-extension/jsx/commands.json 生成；
// 任何手工改动都会在下一次生成时被覆盖，并被契约测试判为失败。
// 重新生成：python scripts/gen_jsx_dispatch.py
// =====================================================================
"""

#: dispatch.jsx 载入的公共库（顺序有依赖：json2 → es3_compat → wrap → util）。
LIB_INCLUDES = ("json2.jsx", "es3_compat.jsx", "wrap.jsx", "util.jsx")

#: 依赖 ops 文件顺序：基础工具先载入。
OPS_ORDER = (
    "app.jsx",
    "project.jsx",
    "sequence.jsx",
    "timeline.jsx",
    "effects.jsx",
    "media.jsx",
    "export.jsx",
    "qe.jsx",
)


def _js_string(text: str) -> str:
    """把 Python 字符串渲染成 ES3 安全的 JS 字符串字面量。"""
    out = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{out}"'


def _route_line(command: CommandSpec) -> str:
    return (
        f"    {_js_string(command.name)}: {{ file: {_js_string(command.ops_file or '')}, "
        f"handler: {_js_string(command.handler)}, "
        f"risk: {_js_string(command.risk)}, "
        f"dryRun: {'true' if command.dry_run else 'false'}, "
        f"idempotent: {'true' if command.idempotent else 'false'}, "
        f"capability: {_js_string(command.capability)}, "
        f"handlerReady: {'false' if command.executor == 'node' else 'true'} }}"
    )


def render_dispatch(contract: Contract, ops_dir: Path) -> str:
    """渲染 dispatch.jsx 的完整文本。

    ``ops_dir`` 用于探测 ops 文件的真实存在性：契约声明的文件必须存在，
    另外 ``OPS_ORDER`` 里存在但未被命令引用的文件（如 QE 辅助库）也要载入。
    """
    jsx_commands = [c for c in contract.commands if c.executor == "jsx"]
    node_commands = [c for c in contract.commands if c.executor == "node"]

    includes = list(LIB_INCLUDES)
    present = set(contract.ops_files()) | {
        name for name in OPS_ORDER if (ops_dir / name).is_file()
    }
    ordered_ops = [name for name in OPS_ORDER if name in present]
    ordered_ops += sorted(present - set(OPS_ORDER))  # 契约新增的 ops 文件自动纳入
    includes += [f"ops/{name}" for name in ordered_ops]

    lines: list[str] = [GENERATED_HEADER]
    lines.append(f"// 契约 schema_version: {contract.schema_version}")
    lines.append(f"// 命令总数: {len(contract.commands)}（jsx {len(jsx_commands)} / node {len(node_commands)}）")
    lines.append("")
    for inc in includes:
        lines.append(f'#include {_js_string(inc)}')
    lines.append("")
    lines.append('if (typeof DSH === "undefined") { var DSH = {}; }')
    lines.append("")
    lines.append("DSH.contractSchemaVersion = %d;" % contract.schema_version)
    lines.append("DSH.ticksPerSecond = %d;" % contract.ticks_per_second())
    lines.append("")
    lines.append("// 命令路由表：由 commands.json 生成，必须与契约逐字一致。")
    lines.append("DSH.routes = {")
    lines.append(",\n".join(_route_line(c) for c in contract.commands))
    lines.append("};")
    lines.append("")
    lines.append("// 路由表在宿主内的落点：jsx 命令走全局函数，node 命令由面板 Node 侧处理。")
    lines.append("DSH.resolveHandler = function (name) {")
    lines.append('    var scope = (typeof $.global !== "undefined") ? $.global : null;')
    lines.append("    if (scope && typeof scope[name] === \"function\") { return scope[name]; }")
    lines.append("    if (typeof this !== \"undefined\" && this && typeof this[name] === \"function\") {")
    lines.append("        return this[name];")
    lines.append("    }")
    lines.append("    return null;")
    lines.append("};")
    lines.append("")
    lines.append(ROUTER_BODY)
    return "\n".join(lines).rstrip("\n") + "\n"


#: 路由逻辑本体。刻意保持与具体命令无关，命令差异全部来自 DSH.routes。
ROUTER_BODY = """\
// 幂等键记录（说明书 §5.3.5）：只记录**执行成功**的命令，重放同键返回上次结果。
DSH._idempotencyOrder = [];
DSH._idempotencyResults = {};
DSH.IDEMPOTENCY_LIMIT = 200;

DSH.rememberIdempotent = function (key, response) {
    if (!key || !response || response.ok !== true) { return; }
    if (typeof DSH._idempotencyResults[key] === "undefined") {
        DSH._idempotencyOrder.push(key);
    }
    DSH._idempotencyResults[key] = response;
    while (DSH._idempotencyOrder.length > DSH.IDEMPOTENCY_LIMIT) {
        var oldest = DSH._idempotencyOrder.shift();
        delete DSH._idempotencyResults[oldest];
    }
};

DSH.recallIdempotent = function (key) {
    if (!key) { return null; }
    if (typeof DSH._idempotencyResults[key] === "undefined") { return null; }
    return DSH._idempotencyResults[key];
};

// 命令总入口：面板通过 CSInterface.evalScript("DSH.dispatch(<json 字符串>)") 调用。
// 入参：{ id, cmd, args, meta: { traceId, dryRun, idempotencyKey } }
// 出参：{ id, ok, data, error, host, warnings, timing: { host_ms } }
DSH.finish = function (response, request) {
    response.id = (request && request.id) ? request.id : null;
    return DSH.json.stringify(response);
};

DSH.dispatch = function (jsonText) {
    var started = new Date().getTime();
    var request = null;
    var command = "";
    var traceId = "";

    try {
        request = DSH.json.parse(String(jsonText));
    } catch (parseError) {
        return DSH.finish(DSH.wrapParseFailure(String(jsonText), parseError, started), null);
    }

    command = (request && request.cmd) ? String(request.cmd) : "";
    traceId = (request && request.meta && request.meta.traceId)
        ? String(request.meta.traceId) : "";

    var args = (request && request.args) ? request.args : {};
    var meta = (request && request.meta) ? request.meta : {};
    var idempotencyKey = meta.idempotencyKey ? String(meta.idempotencyKey) : "";
    var route = DSH.routes[command];

    if (!route) {
        return DSH.finish(DSH.wrapError(command, traceId,
            "UNKNOWN_COMMAND", "面板未注册命令 " + command, started,
            "升级/重新部署 CEP 面板后再试；当前面板契约版本 " + DSH.contractSchemaVersion),
            request);
    }

    if (route.handlerReady === false) {
        return DSH.finish(DSH.wrapError(command, traceId,
            "WRONG_EXECUTOR", "命令 " + command + " 由面板 Node 侧处理，不应到达 ExtendScript",
            started, "这是桥接实现错误，请上报；L1 侧不应把该命令发往 ExtendScript"), request);
    }

    // QE 门禁（说明书 §6.2）：L1 已按配置拦截，这里是第二道防线。
    if (route.capability === "qe" && !DSH.qeEnabled()) {
        return DSH.finish(DSH.wrapError(command, traceId,
            "QE_DISABLED", "命令 " + command + " 依赖未公开的 QE 接口，当前未启用",
            started, "在配置中设置 extendscript.enable_qe = true 后重启宿主面板"), request);
    }

    // 非幂等命令重放：直接返回上次结果，不重复执行（说明书 §5.3.5）。
    if (idempotencyKey && route.idempotent === false) {
        var remembered = DSH.recallIdempotent(idempotencyKey);
        if (remembered) {
            remembered.replayed = true;
            return DSH.finish(remembered, request);
        }
    }

    var handler = DSH.resolveHandler(route.handler);
    if (!handler) {
        return DSH.finish(DSH.wrapError(command, traceId,
            "HANDLER_MISSING", "命令 " + command + " 的处理器 " + route.handler
                + " 未在面板中定义（" + route.file + "）", started,
            "确认 CEP 面板部署完整且版本与 L1 契约一致：python scripts/gen_jsx_dispatch.py --check"),
            request);
    }

    var ctx = {
        command: command,
        traceId: traceId,
        dryRun: meta.dryRun ? true : false,
        qeEnabled: DSH.qeEnabled(),
        idempotencyKey: idempotencyKey ? idempotencyKey : null,
        startedAt: started
    };

    var response = DSH.wrap(command, handler, args, ctx, traceId, started);

    // 失败时附带契约元数据，便于 L1 侧做一致性诊断。
    response.capability = route.capability;
    if (route.capability === "qe" || route.capability === "public_or_qe") {
        response.qe_enabled = DSH.qeEnabled();
    }
    if (idempotencyKey && route.idempotent === false) {
        DSH.rememberIdempotent(idempotencyKey, response);
    }
    return DSH.finish(response, request);
};
"""


def generate(check_only: bool = False) -> int:
    root = repo_root()
    contract = load_contract(root)
    target = root / "cep-extension" / "jsx" / "dispatch.jsx"
    expected = render_dispatch(contract, target.parent / "ops")

    if check_only:
        if not target.exists():
            print(f"[FAIL] 缺少生成物：{target}", file=sys.stderr)
            return 1
        actual = target.read_text(encoding="utf-8")
        if actual != expected:
            print(
                "[FAIL] dispatch.jsx 与 commands.json 不同步。\n"
                "       请运行：python scripts/gen_jsx_dispatch.py",
                file=sys.stderr,
            )
            return 1
        print(f"[ OK ] {target.relative_to(root)} 与契约一致（{len(contract.commands)} 条命令）")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected, encoding="utf-8")
    print(
        f"[ OK ] 已生成 {target.relative_to(root)}："
        f"{len(contract.commands)} 条命令，"
        f"{len(contract.ops_files())} 个 ops 文件，{len(contract.handler_names())} 个处理器"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成或校验 cep-extension/jsx/dispatch.jsx")
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args(argv)
    return generate(check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
