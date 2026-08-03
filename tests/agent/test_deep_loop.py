"""Tests for deepagents engine core functions.

These test the building blocks of run_deep_agent without needing a real LLM
or the full deepagents graph — which would require a BaseChatModel subclass
and real HTTP calls. Each function is tested in isolation.

For full-graph integration with a real model, see scripts/verify_deepagent_live.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from coderio.agent.deep_loop import (
    _build_history_messages,
    _final_already_persisted,
    _try_create_checkpointer,
)
from coderio.agent.harness import _parse_exit_code
from coderio.agent.harness_middleware import _result_to_text
from coderio.session import Message
from coderio.session.store import Session


# ----------------------------------------------------- _build_history_messages
def test_build_history_messages_basic():
    """User/assistant/tool messages are converted to langchain format."""
    msgs = [
        Message.user("hello"),
        Message.assistant("hi there"),
    ]
    result = _build_history_messages(msgs)
    assert len(result) == 2
    assert result[0].content == "hello"
    assert result[1].content == "hi there"


def test_build_history_messages_skips_phase_timeline():
    """phase_timeline system messages are metadata — never shown to the model."""
    msgs = [
        Message.user("hello"),
        Message.system('{"phase": "implement"}', kind="phase_timeline"),
        Message.assistant("response"),
    ]
    result = _build_history_messages(msgs)
    assert len(result) == 2  # phase_timeline dropped


def test_build_history_messages_keeps_context_summary():
    """context_summary system messages ARE shown (they carry compacted history)."""
    msgs = [
        Message.user("old msg"),
        Message.system("summary of earlier conversation", kind="context_summary"),
        Message.user("new msg"),
    ]
    result = _build_history_messages(msgs)
    assert len(result) == 3  # all kept (summary as HumanMessage)


def test_build_history_messages_with_tool_calls():
    """Assistant messages with tool_calls are preserved."""
    from coderio.session import ToolCall

    msgs = [
        Message.user("fix the bug"),
        Message.assistant("", tool_calls=[ToolCall(id="tc1", name="read_file", args={"path": "/foo.py"})]),
    ]
    result = _build_history_messages(msgs)
    assert len(result) == 2
    assert result[1].tool_calls  # AIMessage has tool_calls


# ----------------------------------------------------- _final_already_persisted
def test_final_already_persisted_true():
    """Last message is assistant text (no tool_calls) → already persisted."""
    session = MagicMock()
    session.messages = [Message.assistant("done")]
    assert _final_already_persisted(session) is True


def test_final_already_persisted_false_user():
    """Last message is user → not persisted."""
    session = MagicMock()
    session.messages = [Message.user("question")]
    assert _final_already_persisted(session) is False


def test_final_already_persisted_false_tool_calls():
    """Last assistant message has tool_calls → not a final text."""
    from coderio.session import ToolCall

    session = MagicMock()
    session.messages = [
        Message.assistant("", tool_calls=[ToolCall(id="tc1", name="read_file", args={})]),
    ]
    assert _final_already_persisted(session) is False


def test_final_already_persisted_empty():
    """Empty session → not persisted."""
    session = MagicMock()
    session.messages = []
    assert _final_already_persisted(session) is False


# ----------------------------------------------------- _try_create_checkpointer
def test_try_create_checkpointer_creates_db(tmp_path):
    """SqliteSaver is created and the DB file exists after setup."""
    session = Session.create(save_dir=tmp_path, meta={"model": "test"})
    cp, conn = _try_create_checkpointer(session)
    if cp is None:
        pytest.skip("langgraph-checkpoint-sqlite not installed")
    assert cp is not None
    assert conn is not None
    db_file = tmp_path / f"{session.id}.sqlite"
    assert db_file.exists()
    conn.close()


def test_try_create_checkpointer_closes_on_setup_failure(tmp_path, monkeypatch):
    """If SqliteSaver.setup() fails, the conn must be closed (not leaked)."""
    session = Session.create(save_dir=tmp_path, meta={"model": "test"})

    # We need to intercept AFTER conn creation but DURING setup.
    # Patch SqliteSaver to raise on setup.
    import sqlite3

    real_connect = sqlite3.connect

    closed = {"value": False}

    class TrackingConn:
        def __init__(self, real):
            self._real = real

        def close(self):
            closed["value"] = True
            return self._real.close()

        def __getattr__(self, name):
            return getattr(self._real, name)

    def tracking_connect(*a, **kw):
        return TrackingConn(real_connect(*a, **kw))

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver as RealSaver

        class FailingSaver(RealSaver):
            def setup(self):
                raise RuntimeError("setup failed")

        monkeypatch.setattr("langgraph.checkpoint.sqlite.SqliteSaver", FailingSaver)
    except ImportError:
        pytest.skip("langgraph-checkpoint-sqlite not installed")

    cp, conn = _try_create_checkpointer(session)
    assert cp is None
    assert conn is None
    assert closed["value"], "conn was not closed after setup failure"


# ----------------------------------------------------- _result_to_text (exit_code)
def test_result_to_text_execute_response_with_exit_code():
    """ExecuteResponse exit_code is appended as [exit_code: N] marker."""

    class FakeExecResp:
        output = "tests passed"
        exit_code = 0

    text = _result_to_text(FakeExecResp())
    assert "[exit_code: 0]" in text
    assert _parse_exit_code(text) == 0


def test_result_to_text_execute_response_failed():
    """Failed execution (exit_code=1) includes the marker."""

    class FakeExecResp:
        output = "FAILED"
        exit_code = 1

    text = _result_to_text(FakeExecResp())
    assert "[exit_code: 1]" in text
    assert _parse_exit_code(text) == 1


def test_result_to_text_read_result_error():
    """ReadResult with error attr extracts the error text."""

    class FakeReadResult:
        error = "File '/nonexistent.py' not found"
        file_data = None

    text = _result_to_text(FakeReadResult())
    assert "not found" in text


def test_result_to_text_plain_string():
    """Plain string result passes through unchanged."""
    assert _result_to_text("hello world") == "hello world"


def test_result_to_text_no_exit_code_attr():
    """Object without exit_code attr doesn't get a marker."""

    class NoExit:
        content = "just text"

    text = _result_to_text(NoExit())
    assert "[exit_code:" not in text


# ----------------------------------------------------- multi-turn checkpoint
def test_checkpoint_multi_turn_persistence(tmp_path):
    """SqliteSaver persists across multiple _try_create_checkpointer calls
    with the same session (same DB file, different connections)."""
    session = Session.create(save_dir=tmp_path, meta={"model": "test"})
    db_file = tmp_path / f"{session.id}.sqlite"

    cp1, conn1 = _try_create_checkpointer(session)
    if cp1 is None:
        pytest.skip("langgraph-checkpoint-sqlite not installed")
    conn1.close()

    # Second call should open the same DB file (not create a new one).
    cp2, conn2 = _try_create_checkpointer(session)
    assert cp2 is not None
    assert db_file.exists()
    conn2.close()
