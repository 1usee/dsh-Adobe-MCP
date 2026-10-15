"""路径规范化与白名单（说明书 §8.6）。

规则：

1. 所有文件路径参数在 L1 做规范化（``os.path.realpath``），解析 ``~`` 与
   ``%VAR%`` 形式的变量；
2. 规范化后必须落在**允许根目录**之内，否则拒绝（``PATH_NOT_ALLOWED``）；
3. 拒绝越过根目录的 ``..`` 逃逸（由"规范化后是否仍在根内"这一条统一覆盖，
   避免出现两套互相矛盾的判定逻辑）。

默认允许根目录：用户目录 + 当前工程目录（工程目录在打开工程时由运行期动态加入）。
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Iterable, Sequence

from ..config import expand_path

__all__ = ["PathNotAllowed", "PathPolicy"]


class PathNotAllowed(Exception):
    """路径不在白名单内。"""

    def __init__(self, raw: str, normalized: Path, roots: Sequence[Path], reason: str) -> None:
        self.raw = raw
        self.normalized = normalized
        self.roots = list(roots)
        self.reason = reason
        super().__init__(f"路径不被允许：{raw}（{reason}）")

    def detail(self) -> dict[str, object]:
        return {
            "raw": self.raw,
            "normalized": str(self.normalized),
            "allowed_roots": [str(root) for root in self.roots],
            "reason": self.reason,
        }

    def __str__(self) -> str:  # 便于拼进错误消息
        return json.dumps(self.detail(), ensure_ascii=False)


def _normcase_text(path: Path) -> str:
    return os.path.normcase(str(path))


def is_within(path: Path, root: Path) -> bool:
    """判断 ``path`` 是否在 ``root`` 之内（Windows 上大小写不敏感）。"""
    try:
        candidate = _normcase_text(path)
        base = _normcase_text(root)
    except (TypeError, ValueError):  # pragma: no cover - 极端路径
        return False
    if candidate == base:
        return True
    try:
        return os.path.commonpath([candidate, base]) == base
    except ValueError:
        # 不同盘符会抛 ValueError —— 显然不在根内。
        return False


class PathPolicy:
    """路径白名单校验器。"""

    def __init__(self, roots: Iterable[Path | str] = ()) -> None:
        self._roots: list[Path] = []
        for root in roots:
            self.add_root(root)

    # ---- 根目录管理 -----------------------------------------------------
    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    def add_root(self, root: Path | str | None) -> None:
        if root is None:
            return
        resolved = Path(expand_path(root) if isinstance(root, str) else root)
        try:
            resolved = resolved.resolve()
        except OSError:  # pragma: no cover - 依赖环境
            resolved = Path(os.path.abspath(str(resolved)))
        if resolved not in self._roots:
            self._roots.append(resolved)

    def replace_roots(self, roots: Iterable[Path | str]) -> None:
        self._roots = []
        for root in roots:
            self.add_root(root)

    # ---- 校验 ----------------------------------------------------------
    def normalize(self, raw: str | Path, *, base: Path | None = None) -> Path:
        """规范化路径（不校验白名单）。"""
        text = str(raw)
        expanded = expand_path(text, base=base)
        try:
            return Path(os.path.realpath(str(expanded)))
        except OSError:  # pragma: no cover - 依赖环境
            return Path(os.path.abspath(str(expanded)))

    def check(
        self,
        raw: str | Path,
        *,
        must_exist: bool = False,
        must_be_file: bool = False,
        allow_new: bool = True,
        base: Path | None = None,
    ) -> Path:
        """规范化并校验；不通过时抛 :class:`PathNotAllowed`。

        :param allow_new: 允许路径尚不存在（例如导出输出文件）；但仍要求其父目录在根内。
        """
        normalized = self.normalize(raw, base=base)
        roots = self._roots
        if not roots:
            raise PathNotAllowed(str(raw), normalized, roots, "没有配置任何允许根目录")

        if any(is_within(normalized, root) for root in roots):
            pass
        else:
            # 允许"不存在的输出文件"：其最近存在的祖先目录必须在根内。
            ancestor = normalized.parent
            found = False
            while True:
                if any(is_within(ancestor, root) for root in roots):
                    found = True
                    break
                if ancestor.parent == ancestor:
                    break
                ancestor = ancestor.parent
            if not (allow_new and found):
                raise PathNotAllowed(
                    str(raw), normalized, roots, "规范化后不在任何允许根目录之内"
                )

        if must_exist and not normalized.exists():
            raise PathNotAllowed(str(raw), normalized, roots, "路径不存在")
        if must_be_file and not normalized.is_file():
            raise PathNotAllowed(str(raw), normalized, roots, "不是文件")
        return normalized

    def check_many(self, raws: Sequence[str | Path], **kwargs: object) -> list[Path]:
        return [self.check(raw, **kwargs) for raw in raws]  # type: ignore[arg-type]


def path_param_names() -> frozenset[str]:
    """参数名约定：这些参数的值按文件路径处理并走白名单校验。

    约定是刻意的：契约里的路径参数命名统一以 ``path``/``paths`` 结尾或等于 ``path``，
    因此白名单校验可以对全部工具统一实施，而不需要在每个工具里各写一遍。
    """
    return frozenset({"path", "paths", "output_path", "preset_path", "new_path", "proxy_path"})


def looks_like_path_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in path_param_names() or lowered.endswith("_path") or lowered.endswith("_paths")
