import json

from coderio.session import Message, ToolCall


def test_user_message_roundtrip():
    m = Message.user("hello")
    d = m.to_dict()
    assert d["role"] == "user"
    assert d["content"] == "hello"
    m2 = Message.from_dict(d)
    assert m2.role == "user"
    assert m2.content == "hello"


def test_assistant_with_tool_calls():
    m = Message.assistant(
        "", tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "a.py"})]
    )
    d = m.to_dict()
    assert d["tool_calls"][0]["name"] == "read_file"
    assert d["tool_calls"][0]["args"] == {"path": "a.py"}
    m2 = Message.from_dict(d)
    assert m2.tool_calls[0].name == "read_file"


def test_tool_result_message():
    m = Message.tool_result(tool_call_id="c1", name="read_file", content="file body")
    assert m.role == "tool"
    assert m.tool_call_id == "c1"
    assert m.name == "read_file"


def test_json_serializable():
    m = Message.assistant(
        "hi", tool_calls=[ToolCall(id="c1", name="bash", args={"command": "ls"})]
    )
    line = json.dumps(m.to_dict(), ensure_ascii=False)
    assert "hi" in line


# --- system role (phase timeline / context summary) ---


def test_system_message_roundtrip():
    """A system-role message (phase timeline / context summary) survives a round trip."""
    m = Message.system('{"state":"explore"}', kind="phase_timeline")
    d = m.to_dict()
    assert d["role"] == "system"
    assert d["kind"] == "phase_timeline"
    m2 = Message.from_dict(d)
    assert m2.role == "system"
    assert m2.kind == "phase_timeline"
    assert m2.content == '{"state":"explore"}'


def test_system_message_kind_omitted_when_empty():
    """kind="" (default) is NOT written to the dict — keeps non-system messages
    unchanged for backward compatibility with old jsonl files."""
    m = Message.user("hi")
    d = m.to_dict()
    assert "kind" not in d


def test_old_jsonl_without_kind_loads_fine():
    """A dict from an old session (no kind field) loads with kind=''."""
    d = {"role": "user", "content": "hi", "timestamp": "2026-01-01T00:00:00"}
    m = Message.from_dict(d)
    assert m.kind == ""


def test_phase_timeline_json_is_serializable():
    """The timeline payload (a list of dicts) must be JSON-serializable for storage."""
    from coderio.agent.state import AgentStateTracker, AgentState

    t = AgentStateTracker()
    t.transition(AgentState.EXPLORE, step=1, hint="read")
    t.finish(step=2, hint="done")
    payload = t.to_payload()
    line = json.dumps(
        Message.system(
            json.dumps(payload, ensure_ascii=False), kind="phase_timeline"
        ).to_dict(),
        ensure_ascii=False,
    )
    assert "explore" in line
    assert "complete" in line
