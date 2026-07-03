import os
import time
from pathlib import Path

from coderio.session import Message, ToolCall
from coderio.session.store import Session, new_session_id


def test_append_and_load(tmp_path):
    s = Session.create(tmp_path, {"model": "glm-4.5"})
    s.append(Message.user("hi"))
    s.append(Message.assistant("hello"))
    loaded = Session.load(s.path)
    assert loaded.id == s.id
    assert loaded.meta["model"] == "glm-4.5"
    msgs = loaded.messages
    assert len(msgs) == 2
    assert msgs[0].content == "hi"
    assert msgs[1].role == "assistant"


def test_append_writes_tool_calls(tmp_path):
    s = Session.create(tmp_path, {"meta": "test"})
    s.append(Message.assistant("", tool_calls=[ToolCall(id="c1", name="bash", args={"command": "ls"})]))
    s.append(Message.tool_result(tool_call_id="c1", name="bash", content="output"))
    loaded = Session.load(s.path)
    msgs = loaded.messages
    assert msgs[0].tool_calls[0].name == "bash"
    assert msgs[1].role == "tool"


def test_list_recent(tmp_path):
    a = Session.create(tmp_path, {"meta": "test"})
    time.sleep(0.05)
    b = Session.create(tmp_path, {"meta": "test"})
    # make b older than a so a sorts first
    ts = time.time()
    os.utime(b.path, (ts - 2, ts - 2))
    ids = Session.list_recent(tmp_path)
    assert b.id in ids
    assert a.id in ids
    assert ids[0] == a.id


def test_resume_loads_history(tmp_path):
    s = Session.create(tmp_path, {"meta": "test"})
    s.append(Message.user("one"))
    resumed = Session.load(s.path)
    assert len(resumed.messages) == 1


def test_session_id_format():
    sid = new_session_id()
    parts = sid.split("-")
    assert len(parts) == 3
    assert len(parts[0]) == 8
    assert len(parts[2]) == 4


def test_summaries_returns_recognizable_preview(tmp_path):
    """The resume picker needs a human-recognizable preview, not a bare id.
    summaries() must surface the first user message + message count so the user
    can tell sessions apart ('which one asked about the bug?')."""
    s = Session.create(tmp_path, {"model": "glm-5.2"})
    s.append(Message.user("帮我修一下登录 bug"))
    s.append(Message.assistant("好的，我先看看代码"))
    s.append(Message.user("在 auth.py 里"))
    out = Session.summaries(tmp_path)
    assert len(out) == 1
    row = out[0]
    assert row["id"] == s.id
    assert "登录 bug" in row["first_user"]   # recognizable, not an id
    assert row["message_count"] == 3          # counts user messages
    assert row["model"] == "glm-5.2"
    assert row["mtime"]  # non-empty time string


def test_summaries_orders_recent_first(tmp_path):
    a = Session.create(tmp_path, {"meta": "test"})
    a.append(Message.user("first session"))
    time.sleep(0.05)
    b = Session.create(tmp_path, {"meta": "test"})
    b.append(Message.user("second session"))
    out = Session.summaries(tmp_path)
    assert out[0]["first_user"] == "second session"  # newest first
    assert out[1]["first_user"] == "first session"


def test_summaries_handles_empty_session(tmp_path):
    """A session with no messages yet must not crash the picker."""
    Session.create(tmp_path, {"meta": "test"})
    out = Session.summaries(tmp_path)
    assert len(out) == 1
    assert out[0]["first_user"] == ""
    assert out[0]["message_count"] == 0
