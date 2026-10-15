"""生成 ``config.example.toml``（与配置模型保持同步）。

配置模型的键发生变化而示例文件没跟上时，``tests/unit/test_config.py`` 会失败。

用法::

    python scripts/render_config_example.py
    python scripts/render_config_example.py --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from dsh_adobe_mcp.config import Config, example_toml, repo_root, validate_config_text

TARGET = Path("config.example.toml")


def _self_check(text: str) -> None:
    """示例必须能被解析，且解析结果与内置默认值完全一致。

    这条自检能挡住"示例里写出了模型读不懂的值"这类错误——例如 TOML 没有 null，
    可选字符串若被渲染成 "None" 就会变成字面量字符串，从而悄悄改变默认行为。
    """
    parsed = validate_config_text(text)
    defaults = Config()
    if parsed.model_dump(mode="json") != defaults.model_dump(mode="json"):
        raise SystemExit(
            "[FAIL] config.example.toml 解析结果与内置默认值不一致，"
            "请检查 config.example_toml() 的渲染逻辑"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成或校验 config.example.toml")
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args(argv)

    root = repo_root()
    target = root / TARGET
    expected = example_toml()

    # 自校验：生成的示例必须能被配置模型解析回默认值，否则示例本身就是错的。
    _self_check(expected)

    if args.check:
        if not target.is_file():
            print(f"[FAIL] 缺少 {TARGET}，请运行：python scripts/render_config_example.py", file=sys.stderr)
            return 1
        if target.read_text(encoding="utf-8") != expected:
            print(
                f"[FAIL] {TARGET} 与配置模型不同步，请运行："
                "python scripts/render_config_example.py",
                file=sys.stderr,
            )
            return 1
        print(f"[ OK ] {TARGET} 与配置模型一致")
        return 0

    target.write_text(expected, encoding="utf-8")
    print(f"[ OK ] 已生成 {TARGET}（{len(expected.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
