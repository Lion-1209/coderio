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
    m = Message.assistant("", tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "a.py"})])
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
    m = Message.assistant("hi", tool_calls=[ToolCall(id="c1", name="bash", args={"command": "ls"})])
    line = json.dumps(m.to_dict(), ensure_ascii=False)
    assert "hi" in line
