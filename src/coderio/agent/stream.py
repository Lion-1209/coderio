from __future__ import annotations

from typing import Any, Protocol


class StreamHandler(Protocol):
    """Abstract streaming UI. CLI/GUI implement these (spec §4.3)."""

    def on_step_start(self, step: int = 1) -> None: ...
    def on_token(self, text: str) -> None: ...
    def on_tool_start(
        self,
        name: str,
        args: dict[str, Any],
        step: int = 1,
        tool_index: int = 0,
        tool_total: int = 0,
    ) -> None: ...
    def on_tool_end(self, name: str, result: str) -> None: ...
    def on_finish(self) -> None: ...
    # Optional hooks (have attr guards in run_step; NullStream implements them too):
    def on_thinking(self, text: str) -> None: ...  # 'is thinking' indicator
    def on_truncated(self, stop_reason: str) -> None: ...  # output was cut off
    # Harness escalation release: the agent claimed completion despite an
    # unverified write / pending todos, and the gate exhausted its retries.
    def on_harness_warn(self, message: str) -> None: ...
    # Harness force-continue: the agent claimed done, but the harness found
    # unfinished work (unverified writes, pending todos, ungrounded claims) and
    # is injecting a follow-up demand instead of letting the turn end. Distinct
    # from on_harness_warn (which fires on ESCALATION RELEASE, after retries
    # are exhausted). This fires on every force-continue iteration, surfacing
    # why the agent is still running after the model produced a "final" answer.
    def on_harness_continue(self, reason: str) -> None: ...
    # Agent phase change (explore/plan/implement/verify/complete). Fired by the
    # AgentStateTracker from inside Harness.observe/check_termination. Lets the
    # UI show the task-level phase alongside the model-activity micro-phase.
    def on_phase_change(self, state: str, step: int, hint: str) -> None: ...
    # Turn end summary: the list of files modified this turn (write_file /
    # edit_file / multi_edit). Lets the UI show a "files changed" summary —
    # matching the "always show what changed" UX of claude code / zcode.
    def on_turn_end(self, writes: list[str]) -> None: ...


class NullStream:
    """Default no-op handler for tests/headless."""

    def on_step_start(self, step: int = 1) -> None:
        pass

    def on_token(self, text: str) -> None:
        pass

    def on_tool_start(
        self,
        name: str,
        args: dict[str, Any],
        step: int = 1,
        tool_index: int = 0,
        tool_total: int = 0,
    ) -> None:
        pass

    def on_tool_end(self, name: str, result: str) -> None:
        pass

    def on_finish(self) -> None:
        pass

    def on_thinking(self, text: str) -> None:
        pass

    def on_truncated(self, stop_reason: str) -> None:
        pass

    def on_harness_warn(self, message: str) -> None:
        pass

    def on_harness_continue(self, reason: str) -> None:
        pass

    def on_phase_change(self, state: str, step: int, hint: str) -> None:
        pass

    def on_turn_end(self, writes: list[str]) -> None:
        pass
