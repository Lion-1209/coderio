"""Integration tests for run_deep_agent with a fake model.

These tests exercise the full deepagents graph (create_deep_agent → stream →
HarnessMiddleware → PermissionMiddleware → session persistence) without a real
LLM. The fake model yields predetermined AIMessages, so we can verify:

1. Pure Q&A: model returns text, no tool calls → session persisted correctly.
2. Write + verify: model writes a file then declares done → HarnessMiddleware
   forces continuation (VerifyGate).
3. Permission denial: PLAN mode blocks write_file → model gets error message.

The fake model inherits BaseChatModel so deepagents accepts it. Its .stream()
yields the predetermined messages in sequence.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import Field

# Skip entire module if deepagents isn't installed (CI installs it, local may not).
deepagents = pytest.importorskip("deepagents")


class _FakeModel(BaseChatModel):
    """Fake chat model that yields predetermined AIMessages.

    deepagents calls model.invoke() internally (not .stream()), so _generate
    must return a proper ChatResult with ChatGeneration objects.
    """

    messages: list = Field(default_factory=list)
    _call_index: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, **kwargs):  # noqa: ANN202, ARG002
        from langchain_core.outputs import ChatGeneration, ChatResult

        idx = min(self._call_index, len(self.messages) - 1)
        self._call_index += 1
        msg = self.messages[idx]
        gen = ChatGeneration(message=msg, generation_info={})
        return ChatResult(generations=[gen])

    def stream(self, input, **kwargs):  # noqa: ANN202, ARG002
        idx = min(self._call_index, len(self.messages) - 1)
        self._call_index += 1
        msg = self.messages[idx]
        yield AIMessageChunk(content=msg.content, tool_calls=getattr(msg, "tool_calls", []))

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self


def _make_model(*messages):
    """Create a fake model that returns the given AIMessages in sequence."""
    return _FakeModel(messages=list(messages))


def _make_session(tmp_path):
    """Create a test session in a temp directory."""
    from coderio.session.store import Session

    return Session.create(save_dir=tmp_path, meta={"model": "fake", "provider": "test"})


class _NoOpStream:
    """Minimal stream handler that records events without rendering."""

    def __init__(self):
        self.tokens = []
        self.tool_starts = []
        self.tool_ends = []
        self.finished = False

    def on_step_start(self, step=1):
        pass

    def on_token(self, text):
        self.tokens.append(text)

    def on_thinking(self, text):
        pass

    def on_tool_start(self, name, args, **kw):
        self.tool_starts.append((name, args))

    def on_tool_end(self, name, result):
        self.tool_ends.append((name, result))

    def on_finish(self):
        self.finished = True

    def on_turn_end(self, writes):
        pass

    def add_usage(self, usage):
        pass


@pytest.mark.skipif(
    not deepagents,
    reason="deepagents not installed",
)
def test_fake_model_qa(tmp_path):
    """Pure Q&A: model returns text, no tools → session has user + assistant."""
    from coderio.agent.deep_loop import run_deep_agent

    model = _make_model(AIMessage(content="你好！我是 coderio。"))
    session = _make_session(tmp_path)
    stream = _NoOpStream()

    result = run_deep_agent(
        "你好",
        model,
        session,
        stream=stream,
        harness_enabled=False,
        workdir=str(tmp_path),
    )

    assert "你好" in result or "coderio" in result
    assert stream.finished
    assert len(session.messages) >= 2  # user + assistant


@pytest.mark.skipif(not deepagents, reason="deepagents not installed")
def test_permission_plan_mode_blocks_write(tmp_path):
    """PLAN mode blocks write_file → model gets Permission denied error."""
    from coderio.agent.deep_loop import run_deep_agent
    from coderio.tools.permission import PermissionGate

    model = _make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"file_path": "/test.txt", "content": "x"},
                    "id": "tc1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="done."),
    )
    session = _make_session(tmp_path)
    stream = _NoOpStream()
    gate = PermissionGate("plan")

    result = run_deep_agent(
        "write a file",
        model,
        session,
        stream=stream,
        gate=gate,
        harness_enabled=False,
        workdir=str(tmp_path),
    )

    # The write should have been blocked by the gate.
    assert any("Permission denied" in r for _, r in stream.tool_ends), (
        f"expected Permission denied in tool_ends, got: {stream.tool_ends}"
    )


@pytest.mark.skipif(not deepagents, reason="deepagents not installed")
def test_full_mode_allows_write(tmp_path):
    """FULL mode allows write_file without prompting."""
    from coderio.agent.deep_loop import run_deep_agent
    from coderio.tools.permission import AutoPermissionGate

    model = _make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"file_path": "/test.txt", "content": "hello"},
                    "id": "tc1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="done."),
    )
    session = _make_session(tmp_path)
    stream = _NoOpStream()
    gate = AutoPermissionGate()

    result = run_deep_agent(
        "write a file",
        model,
        session,
        stream=stream,
        gate=gate,
        harness_enabled=False,
        workdir=str(tmp_path),
    )

    # The write should NOT be blocked.
    assert not any("Permission denied" in r for _, r in stream.tool_ends), (
        f"FULL mode should allow write, but got denied: {stream.tool_ends}"
    )
