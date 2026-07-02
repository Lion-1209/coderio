import pytest
from pathlib import Path
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

from coderio.agent.loop import (
    run_agent,
    run_step,
    _BoundModelCache,
    _to_langchain_messages,
)
from coderio.agent.prompts import ActiveSkills
from coderio.agent.stream import NullStream
from coderio.config import Config
from coderio.session import Message, ToolCall
from coderio.session.store import Session
from coderio.skills.store import SkillStore
from coderio.tools import build_default_tools, to_langchain_tools
from coderio.tools.permission import PermissionGate


def _model_returning(*ai_messages):
    """Mock model whose .stream() yields the given AIMessages in sequence per call."""
    model = MagicMock()
    model.bind_tools = MagicMock(return_value=model)
    calls = {"i": 0}

    def _stream(_msgs):
        i = calls["i"]
        calls["i"] += 1
        msg = ai_messages[min(i, len(ai_messages) - 1)]
        yield msg

    model.stream = MagicMock(side_effect=_stream)
    return model


def _tool_call_msg(name, args, mid="c1", content=""):
    return AIMessage(
        content=content,
        tool_calls=[{"name": name, "args": args, "id": mid, "type": "tool_call"}],
    )


def test_run_agent_loops_tool_then_answer(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("42", encoding="utf-8")
    model = _model_returning(
        _tool_call_msg("read_file", {"path": str(f)}),
        AIMessage(content="The answer is 42.", tool_calls=[]),
    )
    session = Session.create(tmp_path, {"meta": "test"})
    final = run_agent(
        user_input="Read the sample file and tell me the answer.",
        model=model, tools=build_default_tools(),
        gate=PermissionGate("auto"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=NullStream(), max_rounds=10,
    )
    assert "42" in final
    # bind_tools should be called once to bind the tool schemas
    assert model.bind_tools.call_count == 1
    roles = [m.role for m in session.messages]
    assert "user" in roles
    assert "assistant" in roles
    assert "tool" in roles
    # at least one tool result recorded
    assert len([m for m in session.messages if m.role == "tool"]) >= 1


def test_max_rounds_break(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("data", encoding="utf-8")
    model = _model_returning(_tool_call_msg("read_file", {"path": str(f)}))
    session = Session.create(tmp_path, {"meta": "test"})
    out = run_agent(
        user_input="go", model=model, tools=build_default_tools(),
        gate=PermissionGate("auto"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=NullStream(), max_rounds=2,
    )
    assert "max" in out.lower() or "round" in out.lower()


def test_permission_blocked_in_plan_mode(tmp_path):
    model = _model_returning(
        _tool_call_msg("bash", {"command": "echo x"}),
        AIMessage(content="done", tool_calls=[]),
    )
    session = Session.create(tmp_path, {"meta": "test"})
    out = run_agent(
        user_input="run it", model=model, tools=build_default_tools(),
        gate=PermissionGate("plan"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=NullStream(), max_rounds=3,
    )
    tool_results = [m for m in session.messages if m.role == "tool"]
    assert tool_results, "expected a tool result message"
    denied = [m.content.lower() for m in tool_results if "permission denied" in m.content.lower()]
    assert denied, "expected bash to be blocked with a permission-denied result"
    assert isinstance(out, str)


def test_to_langchain_messages_produces_real_message_types():
    convo = [
        Message.user("hi"),
        Message.assistant("", tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "a"})]),
        Message.tool_result(tool_call_id="c1", name="read_file", content="body"),
    ]
    msgs = _to_langchain_messages("SYS", convo)
    import langchain_core.messages as L
    assert isinstance(msgs[0], L.SystemMessage)
    assert msgs[0].content == "SYS"
    assert isinstance(msgs[1], L.HumanMessage)
    assert isinstance(msgs[2], L.AIMessage)
    assert msgs[2].tool_calls[0]["name"] == "read_file"
    assert isinstance(msgs[3], L.ToolMessage)
    assert msgs[3].tool_call_id == "c1"


def test_to_langchain_tools_produces_real_bound_tools():
    tools = build_default_tools()
    lc_tools = to_langchain_tools(tools)
    names = {t.name for t in lc_tools}
    assert "read_file" in names
    assert "bash" in names
    # each tool should carry an args_schema -> json schema has properties
    schema = lc_tools[0].args_schema.model_json_schema()
    assert "properties" in schema


def test_bind_tools_called_with_schemas(tmp_path):
    """The loop must call model.bind_tools with StructuredTool objects, not name strings."""
    model = _model_returning(AIMessage(content="done", tool_calls=[]))
    session = Session.create(tmp_path, {"meta": "test"})
    run_agent(
        user_input="hi", model=model, tools=build_default_tools(),
        gate=PermissionGate("auto"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=NullStream(), max_rounds=3,
    )
    model.bind_tools.assert_called_once()
    bound_arg = model.bind_tools.call_args[0][0]
    assert isinstance(bound_arg, list)
    assert all(not isinstance(x, str) for x in bound_arg), "tools must be objects, not strings"
    assert all(hasattr(x, "args_schema") for x in bound_arg)


def test_content_to_text_handles_string_and_blocks():
    """Anthropic-style models return content as str OR list of blocks; both must work."""
    from coderio.agent.loop import _content_to_text
    # plain string
    assert _content_to_text("hello") == "hello"
    # blocks
    blocks = [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]
    assert _content_to_text(blocks) == "hello world"
    # thinking block ignored, text kept
    mixed = [{"type": "thinking", "text": "..."}, {"type": "text", "text": "visible"}]
    assert _content_to_text(mixed) == "visible"


def test_run_step_aggregates_incremental_tool_call_chunks():
    """Regression: a provider that streams tool_call_chunks (no single complete AIMessage)
    must still yield an AIMessage with complete tool_calls."""
    from langchain_core.messages import AIMessageChunk
    from coderio.agent.loop import run_step, _BoundModelCache
    chunks = [
        AIMessageChunk(content="", tool_call_chunks=[{"name": "x", "args": '{"a": 1', "id": "c1", "index": 0, "type": "tool_call_chunk"}]),
        AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": "}", "id": "c1", "index": 0, "type": "tool_call_chunk"}]),
    ]
    model = MagicMock()
    model.stream = MagicMock(return_value=iter(chunks))
    model.bind_tools = MagicMock(return_value=model)
    cache = _BoundModelCache(model)
    ai = run_step(cache, [], "SYS", [], NullStream())
    assert ai.tool_calls, "expected aggregated tool_calls from incremental chunks"
    assert ai.tool_calls[0]["name"] == "x"
    assert ai.tool_calls[0]["args"] == {"a": 1}


class _RecStream(NullStream):
    """NullStream that records harness warnings (on_harness_warn) + step starts."""

    def __init__(self):
        self.warnings = []
        self.step_starts = 0

    def on_harness_warn(self, message):
        self.warnings.append(message)

    def on_step_start(self):
        self.step_starts += 1


def test_verify_gate_blocks_unverified_done(tmp_path):
    """write_file -> 'done' (no bash) must be intercepted and force-continued."""
    f = tmp_path / "a.txt"
    model = _model_returning(
        _tool_call_msg("write_file", {"path": str(f), "content": "hi"}),
        AIMessage(content="All done!", tool_calls=[]),
        AIMessage(content="ok really done now", tool_calls=[]),
    )
    session = Session.create(tmp_path, {"meta": "test"})
    stream = _RecStream()
    final = run_agent(
        user_input="write a file", model=model, tools=build_default_tools(),
        gate=PermissionGate("auto"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=stream, max_rounds=10,
    )
    # the harness injected [harness] user messages to force-continue
    harness_msgs = [m.content for m in session.messages if m.role == "user" and m.content.startswith("[harness]")]
    assert len(harness_msgs) == 2, "expected verify gate to force-continue twice"
    assert "bash" in session.messages[-2].content or any("bash" in m.content for m in session.messages if m.role == "user")
    # an escalation warning is emitted on release
    assert stream.warnings
    assert "UNVERIFIED" in stream.warnings[0]


def test_verify_gate_passes_after_bash(tmp_path):
    """write -> bash -> 'done' passes cleanly: no interception, no warning."""
    f = tmp_path / "a.txt"
    model = _model_returning(
        _tool_call_msg("write_file", {"path": str(f), "content": "hi"}),
        _tool_call_msg("bash", {"command": "echo ok"}),
        AIMessage(content="Done and verified.", tool_calls=[]),
    )
    session = Session.create(tmp_path, {"meta": "test"})
    stream = _RecStream()
    final = run_agent(
        user_input="write and run", model=model, tools=build_default_tools(),
        gate=PermissionGate("auto"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=stream, max_rounds=10,
    )
    assert "verified" in final.lower()
    harness_msgs = [m.content for m in session.messages if m.role == "user" and m.content.startswith("[harness]")]
    assert harness_msgs == [], "no interception expected after bash verification"
    assert stream.warnings == [], "no warning expected after bash verification"


def test_harness_disabled_passthrough(tmp_path):
    """harness_enabled=False keeps the original behavior: no interception, no warning."""
    f = tmp_path / "a.txt"
    model = _model_returning(
        _tool_call_msg("write_file", {"path": str(f), "content": "hi"}),
        AIMessage(content="done", tool_calls=[]),
    )
    session = Session.create(tmp_path, {"meta": "test"})
    stream = _RecStream()
    final = run_agent(
        user_input="write", model=model, tools=build_default_tools(),
        gate=PermissionGate("auto"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=stream, max_rounds=10, harness_enabled=False,
    )
    harness_msgs = [m.content for m in session.messages if m.role == "user" and m.content.startswith("[harness]")]
    assert harness_msgs == [], "no interception expected when harness disabled"
    assert stream.warnings == [], "no warning expected when harness disabled"
    assert "done" in final


def test_failed_write_not_counted(tmp_path):
    """A write that errored (nothing changed on disk) must not arm the verify gate."""
    model = _model_returning(
        _tool_call_msg("edit_file", {"path": str(tmp_path / "nope.txt"), "old_string": "a", "new_string": "b"}),
        AIMessage(content="done", tool_calls=[]),
    )
    session = Session.create(tmp_path, {"meta": "test"})
    stream = _RecStream()
    final = run_agent(
        user_input="edit", model=model, tools=build_default_tools(),
        gate=PermissionGate("auto"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=stream, max_rounds=10,
    )
    harness_msgs = [m.content for m in session.messages if m.role == "user" and m.content.startswith("[harness]")]
    assert harness_msgs == [], "failed write must not trigger verify gate"
    assert stream.warnings == [], "failed write must not warn"
    assert "done" in final


def test_plan_gate_nudge_visible_in_tool_result(tmp_path):
    """Writing with no todo list appends a [nudge] to the tool result in the session."""
    f = tmp_path / "a.txt"
    model = _model_returning(
        _tool_call_msg("write_file", {"path": str(f), "content": "hi"}),
        AIMessage(content="done", tool_calls=[]),
    )
    session = Session.create(tmp_path, {"meta": "test"})
    stream = _RecStream()
    run_agent(
        user_input="write", model=model, tools=build_default_tools(),
        gate=PermissionGate("auto"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=stream, max_rounds=10,
    )
    tool_results = [m for m in session.messages if m.role == "tool" and m.name == "write_file"]
    assert tool_results, "expected a write_file tool result"
    assert "[nudge]" in tool_results[0].content, "expected plan-gate nudge in write result"


def test_on_step_start_called_before_each_model_call(tmp_path):
    """Every model call must be preceded by on_step_start, so the UI arms its
    busy indicator before the (possibly long, silent) wait begins."""
    f = tmp_path / "x.txt"
    f.write_text("42", encoding="utf-8")
    model = _model_returning(
        _tool_call_msg("read_file", {"path": str(f)}),
        AIMessage(content="The answer is 42.", tool_calls=[]),
    )
    session = Session.create(tmp_path, {"meta": "test"})
    stream = _RecStream()
    run_agent(
        user_input="read it", model=model, tools=build_default_tools(),
        gate=PermissionGate("auto"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=stream, max_rounds=5,
    )
    assert stream.step_starts == 2, "expected on_step_start before each of 2 model calls, got " + str(stream.step_starts)


def test_bad_tool_args_become_result_not_crash(tmp_path):
    """A tool call with wrong kwargs (e.g. bash(path=...)) must NOT crash the turn.
    The error becomes a tool-result string the model can react to, and the loop
    continues. (The model passed `path` to bash which only takes `cwd`.)"""
    model = _model_returning(
        _tool_call_msg("bash", {"command": "echo hi", "path": "."}),
        AIMessage(content="recovered after the tool error", tool_calls=[]),
    )
    session = Session.create(tmp_path, {"meta": "test"})
    final = run_agent(
        user_input="run it", model=model, tools=build_default_tools(),
        gate=PermissionGate("auto"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=NullStream(), max_rounds=5,
    )
    assert "recovered" in final
    tool_results = [m for m in session.messages if m.role == "tool" and m.name == "bash"]
    assert tool_results, "expected a bash tool result even though the call was bad"
    assert "rejected" in tool_results[0].content or "Error" in tool_results[0].content


def test_tool_raising_arbitrary_exception_becomes_result(tmp_path):
    """Any tool execution exception (not just TypeError) must be caught and turned
    into a result — the agent keeps going. We force it via a stub tool."""
    from coderio.tools.base import Tool

    session = Session.create(tmp_path, {"meta": "test"})

    class _Exploder:
        name = "bash"
        description = "stub"

        def run(self, **kwargs):
            raise RuntimeError("kaboom")

    tools = [t for t in build_default_tools() if t.name != "bash"]
    tools.append(_Exploder())
    model = _model_returning(
        _tool_call_msg("bash", {"command": "x"}),
        AIMessage(content="moved on", tool_calls=[]),
    )
    final = run_agent(
        user_input="go", model=model, tools=tools,
        gate=PermissionGate("auto"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=NullStream(), max_rounds=5,
    )
    assert "moved on" in final
    tool_results = [m for m in session.messages if m.role == "tool" and m.name == "bash"]
    assert "kaboom" in tool_results[0].content
