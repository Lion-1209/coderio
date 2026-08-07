"""Tests for the CommandReviewMiddleware (content-level tool blocking).

Verifies the middleware intercepts execute/web tools at the right time and
returns ToolMessages that the model can react to. The policy logic itself is
tested in tests/tools/test_command_policy.py — these tests focus on the
middleware integration (when does it block, what does it return).
"""

from __future__ import annotations

from coderio.agent.command_review import CommandReviewMiddleware
from coderio.tools.command_policy import CommandPolicy


class _FakeRequest:
    """Minimal stand-in for deepagents' ToolCallRequest — just .tool_call."""

    def __init__(self, name: str, args: dict, tc_id: str = "tc1") -> None:
        self.tool_call = {"name": name, "args": args, "id": tc_id}


class _CallTracker:
    """Records whether the downstream handler was invoked."""

    def __init__(self) -> None:
        self.called = False

    def __call__(self, request):
        self.called = True
        return f"handler-processed-{request.tool_call['name']}"


def _make_mw(**policy_kwargs) -> tuple[CommandReviewMiddleware, _CallTracker]:
    """Build a middleware + a handler tracker. Returns (mw, tracker)."""
    policy = CommandPolicy(**policy_kwargs)
    tracker = _CallTracker()
    return CommandReviewMiddleware(policy), tracker


# --------------------------------------------------------------- execute blocking


def test_execute_blocked_command_returns_toolmessage():
    """A blacklisted command must NOT reach the handler. Returns a ToolMessage
    whose content explains WHY, so the model can reformulate."""
    mw, tracker = _make_mw()
    req = _FakeRequest("execute", {"command": "rm -rf /"})
    result = mw.wrap_tool_call(req, tracker)
    assert not tracker.called, "handler must not run for a blocked command"
    assert "Blocked" in result.content
    assert "rm" in result.content.lower() or "root" in result.content.lower()
    assert result.name == "execute"


def test_execute_safe_command_reaches_handler():
    """A safe command passes through to the handler unchanged."""
    mw, tracker = _make_mw()
    req = _FakeRequest("execute", {"command": "pytest -q"})
    result = mw.wrap_tool_call(req, tracker)
    assert tracker.called, "handler must run for a safe command"
    assert result == "handler-processed-execute"


def test_execute_with_no_command_arg_passes():
    """Missing/empty command arg — don't crash, pass through (the backend will
    handle the empty case). The policy's job is pattern matching, not arg
    validation."""
    mw, tracker = _make_mw()
    req = _FakeRequest("execute", {})
    result = mw.wrap_tool_call(req, tracker)
    assert tracker.called
    assert result == "handler-processed-execute"


def test_user_blocklist_also_blocks_in_middleware():
    """A user-supplied pattern (e.g. 'git push --force') blocks at the middleware."""
    mw, tracker = _make_mw(extra_blocked=[r"git\s+push\s+--force"])
    req = _FakeRequest("execute", {"command": "git push --force origin main"})
    result = mw.wrap_tool_call(req, tracker)
    assert not tracker.called
    assert "Blocked" in result.content


# --------------------------------------------------------------- network control


def test_web_fetch_blocked_when_network_disabled():
    """network_allowed=False blocks web_fetch."""
    mw, tracker = _make_mw(network_allowed=False)
    req = _FakeRequest("web_fetch", {"url": "https://example.com"})
    result = mw.wrap_tool_call(req, tracker)
    assert not tracker.called
    assert "network" in result.content.lower()


def test_web_search_blocked_when_network_disabled():
    """network_allowed=False blocks web_search too."""
    mw, tracker = _make_mw(network_allowed=False)
    req = _FakeRequest("web_search", {"query": "python docs"})
    result = mw.wrap_tool_call(req, tracker)
    assert not tracker.called
    assert "network" in result.content.lower()


def test_web_fetch_allowed_when_network_enabled():
    """network_allowed=True (default) lets web_fetch through."""
    mw, tracker = _make_mw()
    req = _FakeRequest("web_fetch", {"url": "https://example.com"})
    result = mw.wrap_tool_call(req, tracker)
    assert tracker.called


# --------------------------------------------------------------- non-reviewed tools pass through


def test_read_file_not_inspected():
    """read_file is not a shell/network tool — passes through untouched."""
    mw, tracker = _make_mw()
    req = _FakeRequest("read_file", {"path": "/etc/passwd"})
    result = mw.wrap_tool_call(req, tracker)
    assert tracker.called
    assert result == "handler-processed-read_file"


def test_write_file_not_inspected():
    """write_file is isolated by virtual_mode; the content review doesn't apply."""
    mw, tracker = _make_mw()
    req = _FakeRequest("write_file", {"path": "/foo.py", "content": "x"})
    result = mw.wrap_tool_call(req, tracker)
    assert tracker.called


def test_note_not_inspected():
    """note writes to jsonl memory, not arbitrary execution — passes through."""
    mw, tracker = _make_mw()
    req = _FakeRequest("note", {"action": "write", "key": "k", "value": "v"})
    result = mw.wrap_tool_call(req, tracker)
    assert tracker.called


# --------------------------------------------------------------- mode independence


def test_blocks_regardless_of_permission_mode():
    """The command review is INDEPENDENT of the permission mode — even FULL mode
    blocks rm -rf /. (PermissionMiddleware is a separate middleware; this one
    doesn't consult the mode at all.)"""
    mw, tracker = _make_mw()  # no mode param — the middleware has no concept of mode
    req = _FakeRequest("execute", {"command": "mkfs.ext4 /dev/sda"})
    result = mw.wrap_tool_call(req, tracker)
    assert not tracker.called
    assert "Blocked" in result.content
