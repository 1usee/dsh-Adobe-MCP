"""让 ``scripts/`` 下的独立脚本在未安装本包时也能 ``import dsh_adobe_mcp``。

用法（每个脚本文件顶部）：``import _bootstrap  # noqa: F401``
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
