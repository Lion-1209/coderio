from __future__ import annotations

from typing import Any, Protocol


class StreamHandler(Protocol):
    """Abstract streaming UI. CLI/GUI implement these (spec §4.3)."""
    def on_step_start(self, step: int = 1) -> None: ...
    def on_token(self, text: str) -> None: ...
    def on_tool_start(self, name: str, args: dict[str, Any], step: int = 1,
                      tool_index: int = 0, tool_total: int = 0) -> None: ...
    def on_tool_end(self, name: str, result: str) -> None: ...
    def on_finish(self) -> None: ...
    # Optional hooks (have attr guards in run_step; NullStream implements them too):
    def on_thinking(self, text: str) -> None: ...     # 'is thinking' indicator
    def on_truncated(self, stop_reason: str) -> None: ...  # output was cut off
    # Harness escalation release: the agent claimed completion despite an
    # unverified write / pending todos, and the gate exhausted its retries.
    def on_harness_warn(self, message: str) -> None: ...


class NullStream:
    """Default no-op handler for tests/headless."""
    def on_step_start(self, step: int = 1) -> None: pass
    def on_token(self, text: str) -> None: pass
    def on_tool_start(self, name: str, args: dict[str, Any], step: int = 1,
                      tool_index: int = 0, tool_total: int = 0) -> None: pass
    def on_tool_end(self, name: str, result: str) -> None: pass
    def on_finish(self) -> None: pass
    def on_thinking(self, text: str) -> None: pass
    def on_truncated(self, stop_reason: str) -> None: pass
    def on_harness_warn(self, message: str) -> None: pass
