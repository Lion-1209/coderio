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


# --- whitelist degradation (REGRESSION: was dead code before this fix) ---
# These tests verify the whitelist_miss branch ACTUALLY does something. The
# original implementation had `if mode == "full": pass` followed by nothing —
# the whitelist reason was computed by check_whitelist then silently discarded.
# These tests guard against regressing to that dead-code state.


class _FakeGate:
    """Minimal PermissionGate stub with a .mode property."""

    def __init__(self, mode: str):
        self.mode = mode


def _make_whitelist_mw(mode: str, allowed: list[str] | None = None):
    """Build middleware with whitelist_mode=True + a gate with the given mode."""
    policy = CommandPolicy(whitelist_mode=True, allowed_commands=allowed or [])
    gate = _FakeGate(mode)

    class _Tracker:
        def __init__(self):
            self.called = False
            self.result = "executed"

        def __call__(self, req):
            self.called = True
            return self.result

    tracker = _Tracker()
    return CommandReviewMiddleware(policy, gate=gate), tracker


def test_whitelist_miss_in_plan_mode_hard_blocks():
    """PLAN mode + whitelist miss → hard block (returns ToolMessage, doesn't run)."""
    mw, tracker = _make_whitelist_mw("plan")
    req = _FakeRequest("execute", {"command": "terraform apply"})
    result = mw.wrap_tool_call(req, tracker)
    assert not tracker.called, "terraform (non-whitelisted) must NOT run in plan mode"
    assert "whitelist" in result.content.lower()


def test_whitelist_miss_in_confirm_mode_annotates_result():
    """CONFIRM mode + whitelist miss → tool runs, but result carries the note.

    REGRESSION GUARD: before the fix, check_whitelist computed the reason then
    discarded it (the `if mode == "full": pass` + empty non-FULL branch was dead
    code). This test fails if that regression returns: without annotation, the
    result is the bare 'executed' string with no [whitelist] marker.
    """
    mw, tracker = _make_whitelist_mw("confirm")
    req = _FakeRequest("execute", {"command": "terraform apply"})
    result = mw.wrap_tool_call(req, tracker)
    assert tracker.called, "tool should still run in confirm mode (PermissionMiddleware prompted)"
    # The result MUST carry the whitelist annotation.
    # tracker.result is a plain str — check it has the marker.
    assert "[whitelist]" in str(result), f"whitelist miss must annotate the result, got: {result!r}"


def test_whitelist_hit_in_confirm_mode_no_annotation():
    """CONFIRM mode + whitelisted command → runs normally, no [whitelist] marker."""
    mw, tracker = _make_whitelist_mw("confirm")
    req = _FakeRequest("execute", {"command": "git status"})
    result = mw.wrap_tool_call(req, tracker)
    assert tracker.called
    assert "[whitelist]" not in str(result)


def test_whitelist_miss_in_full_mode_no_annotation():
    """FULL mode + whitelist miss → runs normally, no annotation (FULL = trust)."""
    mw, tracker = _make_whitelist_mw("full")
    req = _FakeRequest("execute", {"command": "terraform apply"})
    result = mw.wrap_tool_call(req, tracker)
    assert tracker.called, "FULL mode must run even non-whitelisted commands"
    assert "[whitelist]" not in str(result), "FULL mode should not annotate — explicit trust, no warning"


def test_whitelist_user_allowed_command_runs_clean():
    """A command in allowed_commands runs without annotation even in confirm mode."""
    mw, tracker = _make_whitelist_mw("confirm", allowed=["terraform"])
    req = _FakeRequest("execute", {"command": "terraform plan"})
    result = mw.wrap_tool_call(req, tracker)
    assert tracker.called
    assert "[whitelist]" not in str(result)
