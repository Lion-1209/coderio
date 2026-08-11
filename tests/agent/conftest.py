"""Shared fixtures for agent tests.

The fake model + stream + session helpers live here so both
test_deep_integration.py (graph-level) and test_deep_loop_unit.py (unit-level)
can reuse them without duplicating ~80 lines of stub definitions.

The _FakeModel inherits BaseChatModel so deepagents' create_deep_agent accepts
it. Its _generate returns predetermined AIMessages in sequence; .stream() does
the same via AIMessageChunk. bind_tools is a no-op (the fake doesn't actually
bind anything — it just returns self so deepagents' tool-binding path doesn't
choke).
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import Field


class _FakeModel(BaseChatModel):
    """Fake chat model that yields predetermined AIMessages in sequence.

    deepagents calls model.invoke() internally (not .stream()), so _generate
    must return a proper ChatResult with ChatGeneration objects. The .stream()
    override exists because agent.stream(stream_mode=["messages", ...]) routes
    model output through the streaming path.
    """

    messages: list = Field(default_factory=list)
    _call_index: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages: Any, **kwargs: Any):  # noqa: ANN202, ARG002
        from langchain_core.outputs import ChatGeneration, ChatResult

        idx = min(self._call_index, len(self.messages) - 1)
        self._call_index += 1
        msg = self.messages[idx]
        gen = ChatGeneration(message=msg, generation_info={})
        return ChatResult(generations=[gen])

    def stream(self, input: Any, **kwargs: Any):  # noqa: ANN202, ARG002
        idx = min(self._call_index, len(self.messages) - 1)
        self._call_index += 1
        msg = self.messages[idx]
        yield AIMessageChunk(content=msg.content, tool_calls=getattr(msg, "tool_calls", []))

    def bind_tools(self, tools: Any, **kwargs: Any):  # noqa: ARG002
        return self


def make_model(*messages: AIMessage) -> _FakeModel:
    """Create a fake model that returns the given AIMessages in sequence."""
    return _FakeModel(messages=list(messages))


def make_session(tmp_path):
    """Create a test Session in a temp directory (isolated per-test)."""
    from coderio.session.store import Session

    return Session.create(save_dir=tmp_path, meta={"model": "fake", "provider": "test"})


class NoOpStream:
    """Minimal stream handler that records events without rendering.

    Captures tool_starts/tool_ends/tokens/harness signals/phase transitions so
    tests can assert on what the agent actually did without a real TUI. This is
    the test-side mirror of coderio's StreamHandler protocol.
    """

    def __init__(self):
        self.tokens: list[str] = []
        self.tool_starts: list[tuple[str, dict]] = []
        self.tool_ends: list[tuple[str, str]] = []
        self.finished: bool = False
        self.harness_signals: list[dict] = []
        self.phases: list[tuple[str, int, str]] = []
        self.todos_updates: list[list] = []

    def on_step_start(self, step: int = 1) -> None:
        pass

    def on_token(self, text: str) -> None:
        self.tokens.append(text)

    def on_thinking(self, text: str) -> None:
        pass

    def on_tool_start(self, name: str, args: dict, **kw: Any) -> None:
        self.tool_starts.append((name, args))

    def on_tool_end(self, name: str, result: str) -> None:
        self.tool_ends.append((name, result))

    def on_finish(self) -> None:
        self.finished = True

    def on_turn_end(self, writes: Any) -> None:
        pass

    def add_usage(self, usage: Any) -> None:
        pass

    def on_phase_change(self, state: str, step: int, hint: str) -> None:
        self.phases.append((state, step, hint))

    def on_harness_continue(self, reason: str) -> None:
        self.harness_signals.append({"type": "harness_continue", "reason": reason})

    def on_harness_warn(self, message: str) -> None:
        self.harness_signals.append({"type": "harness_warn", "message": message})

    def on_todos_update(self, todos: list) -> None:
        self.todos_updates.append(todos)
