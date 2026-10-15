"""测试替身（假面板、假注册表等）。

这些替身让 L1/L2/L3 的链路在没有 Premiere Pro 的机器上也能被真实验证：
被测的仍然是生产代码（CepClient、JsxRunner、BridgeService、ToolContext）。
"""

from __future__ import annotations

from .fake_panel import FakePanel, default_host_payloads

__all__ = ["FakePanel", "default_host_payloads"]
