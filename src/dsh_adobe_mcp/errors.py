"""错误码、默认消息与 **remedy**（可执行的修复建议）。

说明书 §7.1 把这套设计定为可用性的关键：AI 客户端在失败时最需要的是"下一步怎么办"，
而不是错误码。因此每一个错误码都必须带一句**可直接照做**的 remedy，而不是"请重试"这类空话。

``tests/unit/test_errors.py`` 断言：每个错误码都有 remedy，且 remedy 不是一个短到无意义的串。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

__all__ = [
    "ErrorSpec",
    "ERRORS",
    "spec",
    "make_error",
    "error_codes",
    "is_known",
    "ToolError",
    "code_of",
    "describe_exception",
    "error_payload",
]


class ErrorSpec(BaseModel):
    """一个错误码的完整说明。"""

    code: str
    message: str
    remedy: str
    retryable: bool = False
    detail: str = ""


def _spec(
    code: str, message: str, remedy: str, *, retryable: bool = False, detail: str = ""
) -> ErrorSpec:
    return ErrorSpec(
        code=code, message=message, remedy=remedy, retryable=retryable, detail=detail
    )


#: 全部错误码。命名与说明书 §12 的失败模式表逐条对应。
ERRORS: dict[str, ErrorSpec] = {
    s.code: s
    for s in (
        # ---------- 宿主发现与启动（§4.1、§12） ----------
        _spec(
            "HOST_NOT_INSTALLED",
            "本机没有发现已安装的该宿主",
            "调用 host_list 查看已发现的宿主；若确认已安装，请检查注册表键 "
            "HKLM\\SOFTWARE\\Adobe\\Premiere Pro\\<版本>\\InstallPath 是否存在",
        ),
        _spec(
            "HOST_NOT_RUNNING",
            "宿主进程未运行",
            "调用 host_launch 启动宿主，或在配置中把 hosts.premiere.launch_on_demand 设为 true",
            retryable=True,
        ),
        _spec(
            "HOST_AMBIGUOUS_VERSION",
            "本机安装了多个版本，未指定目标版本",
            "在调用中显式给出 host=\"premiere:<版本>\"，或设置配置项 hosts.premiere.version",
        ),
        _spec(
            "HOST_LAUNCH_TIMEOUT",
            "宿主启动后在超时时间内没有完成面板握手",
            "增大 hosts.premiere.launch_timeout_s；确认 CEP 扩展已部署（host_status 会给出部署状态）"
            "且已设置 PlayerDebugMode；若宿主弹出登录/欢迎对话框，先手工处理",
            retryable=True,
        ),
        _spec(
            "HOST_CRASHED",
            "宿主进程已退出",
            "重新调用 host_launch 启动宿主，然后用 safety_list_checkpoints 选择检查点恢复工程",
        ),
        # ---------- 面板部署（§4.2） ----------
        _spec(
            "PANEL_NOT_LOADED",
            "宿主内没有出现面板握手文件",
            "先调用 host_status 查看面板部署状态；若未部署，运行 "
            "scripts\\dev_install_cep.ps1 手工部署并重启宿主；确认 CEP 用户级扩展目录存在",
            retryable=True,
        ),
        _spec(
            "PANEL_DEPLOY_FAILED",
            "CEP 面板部署失败",
            "查看返回的 detail 中的具体原因；若为权限问题，手工运行 scripts\\dev_install_cep.ps1",
        ),
        _spec(
            "PANEL_DEPLOY_BLOCKED",
            "部署面板时被文件系统沙箱拒绝写入",
            "由用户在普通终端运行 scripts\\dev_install_cep.ps1 完成一次部署，"
            "本服务器随后只做存在性检测（说明书 §4.2 的退路）",
        ),
        _spec(
            "PANEL_MANIFEST_INVALID",
            "面板 manifest.xml 缺少必需元素或不满足 CEP 规范",
            "按 error.detail 列出的缺失项修正 cep-extension/CSXS/manifest.xml；"
            "最常见的是缺少 CEFCommandLine 的 --mixed-context，会导致面板里 require 未定义",
        ),
        # ---------- 桥接与求值（§5.3、§5.4） ----------
        _spec(
            "BRIDGE_TIMEOUT",
            "命令在超时时间内没有返回。**这不等于命令失败**：ExtendScript 没有取消机制，"
            "命令可能仍在宿主内执行",
            "先调用 host_resync 重新读取宿主真实状态并比对差异，再决定是否重发；"
            "禁止直接重试（说明书 C-4）。若宿主窗口有未响应的模态对话框，先手工关闭它",
        ),
        _spec(
            "BRIDGE_DESYNC",
            "桥接连接处于 DESYNC 状态（此前有命令求值超时，宿主状态未知）",
            "调用 host_resync 重新同步后再继续",
        ),
        _spec(
            "BRIDGE_DISCONNECTED",
            "与宿主面板的连接未建立或已断开",
            "调用 host_status 查看连接状态；必要时 host_launch 重启宿主，"
            "或用 host_reload_panel 重载面板",
            retryable=True,
        ),
        _spec(
            "BRIDGE_AUTH_FAILED",
            "面板拒绝了认证帧（令牌不匹配）",
            "握手令牌每次面板启动都会刷新，请等下一次握手文件更新后重试；"
            "若持续失败，用 host_reload_panel 重载面板",
            retryable=True,
        ),
        _spec(
            "BRIDGE_PROTOCOL_ERROR",
            "桥接协议违规（帧结构或顺序不符合契约）",
            "确认 L1 与面板版本一致：运行 python scripts/gen_jsx_dispatch.py --check，"
            "并重新部署面板",
        ),
        _spec(
            "BRIDGE_BUSY",
            "该宿主上已有一条求值在途（约束 C-2：同一宿主同时只允许一个求值）",
            "等待当前调用返回后重试；不要并发调用同一宿主",
            retryable=True,
        ),
        # ---------- 脚本执行 ----------
        _spec(
            "SCRIPT_ERROR",
            "宿主内脚本抛出了异常",
            "查看 error.detail 里的 code/line/script_excerpt 定位；"
            "WAL 日志（logs/wal-YYYY-MM-DD.jsonl）保存了本次调用的完整脚本原文",
        ),
        _spec(
            "UNKNOWN_COMMAND",
            "面板不认识该命令",
            "面板与 L1 的契约版本不一致：运行 python scripts/gen_jsx_dispatch.py --check，"
            "然后重新部署面板",
        ),
        _spec(
            "HANDLER_MISSING",
            "面板缺少该命令的处理器实现",
            "面板文件不完整或版本不一致：重新运行 scripts\\dev_install_cep.ps1 部署整个面板目录",
        ),
        _spec(
            "EVAL_SCRIPT_ERROR",
            "CSInterface.evalScript 调用本身失败",
            "确认面板已加载且宿主窗口没有模态对话框；必要时 host_reload_panel 重载面板",
            retryable=True,
        ),
        _spec(
            "RESULT_TOO_LARGE",
            "单次脚本返回的字符串超过面板可安全处理的长度",
            "缩小查询范围（例如用 track_index 只查一条轨道，或减小 limit 参数）后重试",
        ),
        _spec(
            "UNSUPPORTED_API",
            "该宿主版本不提供完成此操作所需的 API",
            "按 error.detail 中的说明改用替代路径（例如提供 .sqpreset 预设文件），"
            "或改用宿主 GUI 手工完成这一步",
        ),
        _spec(
            "NOT_IMPLEMENTED",
            "该能力在当前版本中尚未实现",
            "参见 error.detail；如需要请提交 issue，并说明宿主版本与操作场景",
        ),
        _spec(
            "QE_DISABLED",
            "该操作依赖未公开的 QE 接口，当前配置未启用",
            "若确实需要：把配置项 extendscript.enable_qe 设为 true 并重启宿主面板。"
            "注意 QE 是 Adobe 未公开的接口，可能导致宿主崩溃（说明书 §6.2）",
        ),
        # ---------- 安全（§8） ----------
        _spec(
            "CONFIRM_REQUIRED",
            "这是 T2 破坏性操作，必须先用确认令牌显式确认",
            "阅读返回的 data.impact（影响对象、数量、不可逆部分），确认无误后调用 "
            "safety_confirm_operation(confirm_token=<data.confirm_token>)",
        ),
        _spec(
            "CONFIRM_TOKEN_INVALID",
            "确认令牌无效或与要执行的操作不匹配（令牌与参数哈希绑定）",
            "重新发起一次该 T2 工具调用以获得新令牌，并用同一组参数确认",
        ),
        _spec(
            "CONFIRM_TOKEN_EXPIRED",
            "确认令牌已过期",
            "重新发起该 T2 工具调用获得新令牌（有效期见配置 safety.confirm_token_ttl_s）",
        ),
        _spec(
            "CONFIRM_TOKEN_USED",
            "确认令牌已被使用（一次性消费）",
            "重新发起该 T2 工具调用获得新令牌",
        ),
        _spec(
            "CHECKPOINT_FAILED",
            "创建检查点失败，因此已拒绝执行该破坏性操作",
            "检查配置项 safety.checkpoint_dir 是否可写、磁盘空间是否充足；"
            "修复后再重试（T2 操作在无检查点时不执行，这是刻意的安全设计）",
        ),
        _spec(
            "CHECKPOINT_NOT_FOUND",
            "找不到指定的检查点",
            "先调用 safety_list_checkpoints 查看可用检查点及其 id",
        ),
        _spec(
            "PATH_NOT_ALLOWED",
            "路径不在允许的白名单根目录内",
            "把文件放到用户目录或当前工程目录下；"
            "或在配置 safety.allowed_path_roots 中追加允许的根目录",
        ),
        # ---------- 参数与业务（§8.6、§12） ----------
        _spec(
            "INVALID_ARGUMENT",
            "参数不合法",
            "按 error.detail 的说明修正参数后重试；可用工具的 input schema 查看参数约束",
        ),
        _spec(
            "TOOL_NOT_ENABLED",
            "该工具所属的工具组未启用",
            "在配置 tools.enabled_groups 中加入对应工具组（可选组：project_edit、timeline_edit、"
            "effects、export、media）后重启 MCP 服务器",
        ),
        _spec(
            "NO_PROJECT",
            "当前没有打开的工程",
            "先用 ppro_project_open 打开工程，或 ppro_project_new 新建工程",
        ),
        _spec(
            "NOT_FOUND",
            "目标对象不存在",
            "先用对应的查询工具确认目标（序列用 ppro_sequence_list、片段用 "
            "ppro_timeline_list_clips、工程条目用 ppro_project_item_find）",
        ),
        _spec(
            "ITEM_NOT_FOUND",
            "找不到指定的工程条目",
            "用 ppro_project_item_find(query=...) 得到准确的树路径或 nodeId 后重试",
        ),
        _spec(
            "REF_INVALID",
            "clip ref 格式不合法（应为 V:1:0 这样 <V|A>:<轨道>:<序号>）",
            "用 ppro_timeline_list_clips 获取现成的 ref 值",
        ),
        _spec(
            "DUPLICATE_NAME",
            "同名对象已存在",
            "换一个名字，或先用查询工具确认现有对象",
        ),
        _spec(
            "DIRTY_PROJECT",
            "当前工程有未保存的改动，该操作会触发宿主模态对话框",
            "先调用 ppro_project_save 保存（或在明确要丢弃改动时把 confirm_discard 设为 true）",
        ),
        _spec(
            "DIALOG_RISK",
            "该操作可能弹出宿主模态对话框，导致求值挂起，因此被拒绝",
            "在宿主中手工处理该对话框后重试；或在宿主中先把界面调整到无对话框的状态",
        ),
        _spec(
            "HOST_ERROR",
            "宿主 API 抛出了异常",
            "按 error.detail 中的宿主错误信息排查；必要时在宿主中手工执行一次同样的操作确认行为",
        ),
        # ---------- 任务（§9） ----------
        _spec(
            "JOB_NOT_FOUND",
            "找不到该任务",
            "用 job_list 查看当前与历史任务 id",
        ),
        _spec(
            "JOB_NOT_CANCELLABLE",
            "该任务无法取消",
            "编码器不支持取消已提交的编码；可在宿主/AME 的编码队列中手工移除，"
            "或等其结束后删除输出文件",
        ),
        _spec(
            "JOB_QUEUE_FULL",
            "该宿主上排队的长任务已达到上限",
            "等已有任务结束（用 job_status 轮询），或先 job_cancel 不需要的任务",
            retryable=True,
        ),
        _spec(
            "EXPORT_PATH_OCCUPIED",
            "导出目标文件已存在且未指定覆盖",
            "换一个输出文件名，或先删除/移走已有文件",
        ),
        # ---------- 兜底 ----------
        _spec(
            "INTERNAL_ERROR",
            "MCP 服务器内部错误",
            "查看服务器日志（logs/server-YYYY-MM-DD.jsonl）中同 trace_id 的记录；"
            "这属于缺陷，请提交 issue 并附上 trace_id",
        ),
    )
}


def is_known(code: str) -> bool:
    return code in ERRORS


def spec(code: str) -> ErrorSpec:
    """取错误码说明；未知码退化为 INTERNAL_ERROR 的说明但保留原始码，便于诊断。"""
    found = ERRORS.get(code)
    if found is not None:
        return found
    fallback = ERRORS["INTERNAL_ERROR"]
    return ErrorSpec(
        code=code,
        message=f"未登记的错误码：{code}",
        remedy=fallback.remedy,
        detail=fallback.detail,
    )


def error_codes() -> list[str]:
    return sorted(ERRORS)


def make_error(
    code: str,
    message: str | None = None,
    remedy: str | None = None,
    *,
    detail: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造统一返回信封里的 ``error`` 字段。

    ``message`` / ``remedy`` 可覆盖默认值（例如附上具体对象名），``detail`` 放原始诊断信息。
    """
    found = spec(code)
    payload: dict[str, Any] = {
        "code": found.code,
        "message": message or found.message,
        "remedy": remedy or found.remedy,
    }
    if detail is not None:
        payload["detail"] = detail
    if found.retryable:
        payload["retryable"] = True
    if extra:
        payload.update(extra)
    return payload


class ToolError(Exception):
    """贯穿 L1/L2 的结构化异常。

    L2 的各组件（部署器、客户端、求值器）都用它报错，工具层统一把它转成返回信封，
    因此**任何失败路径都带着 code 与 remedy**，不会退化成裸异常。
    """

    def __init__(
        self,
        code: str,
        message: str | None = None,
        remedy: str | None = None,
        detail: Any = None,
    ) -> None:
        found = spec(code)
        self.code = found.code
        self.message = message or found.message
        self.remedy = remedy or found.remedy
        self.detail = detail
        super().__init__(self.message)

    def payload(self) -> dict[str, Any]:
        return make_error(self.code, self.message, self.remedy, detail=self.detail)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


#: 与错误码等价、可互换的异常属性名
_ERROR_ATTR_ALIASES = {"code": ("code", "dsh_code"), "message": ("message",), "remedy": ("remedy",)}


def code_of(exc: BaseException) -> str:
    """从任意异常里取出错误码；没有登记过的异常退化为 INTERNAL_ERROR。"""
    for attribute in _ERROR_ATTR_ALIASES["code"]:
        value = getattr(exc, attribute, None)
        if isinstance(value, str) and is_known(value):
            return value
    return "INTERNAL_ERROR"


def describe_exception(exc: BaseException) -> dict[str, Any]:
    """把异常转成信封的 ``error`` 字段。"""
    code = code_of(exc)
    message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
    remedy = getattr(exc, "remedy", None)
    detail = getattr(exc, "detail", None)
    if code == "INTERNAL_ERROR" and detail is None:
        detail = f"{exc.__class__.__name__}: {exc}"
    return make_error(code, str(message), remedy, detail=detail)


def error_payload(**kwargs: Any) -> dict[str, Any]:
    """``make_error`` 的别名，便于在工具层表达"这里要构造错误体"。"""
    return make_error(**kwargs)
