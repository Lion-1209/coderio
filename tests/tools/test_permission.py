import pytest

from coderio.tools.permission import PermissionGate, PermissionMode


class _AlwaysAllow(PermissionGate):
    def __init__(self, mode):
        super().__init__(mode)

    def _ask(self, tool_name, args):
        return True


def test_readonly_tools_always_allowed():
    gate = _AlwaysAllow("plan")
    for t in ("read_file", "glob", "grep", "todo"):
        assert gate.check(t, {}) is True


def test_plan_mode_blocks_write_tools():
    gate = _AlwaysAllow("plan")
    for t in ("write_file", "edit_file", "bash", "web_fetch"):
        assert gate.check(t, {}) is False


def test_auto_mode_allows_all():
    gate = _AlwaysAllow("auto")
    assert gate.check("bash", {}) is True
    assert gate.check("write_file", {}) is True


def test_confirm_mode_asks():
    gate = _AlwaysAllow("confirm")
    assert gate.check("write_file", {}) is True


def test_invalid_mode():
    with pytest.raises(ValueError):
        PermissionMode("bogus")


# --- note tool: action-level permission (read/list are read-only) ---

def test_note_read_bypasses_gate():
    """note(action='read') is read-only — must not prompt even in plan mode."""
    gate = _AlwaysAllow("plan")
    assert gate.check("note", {"action": "read", "name": "x"}) is True


def test_note_list_bypasses_gate():
    """note(action='list') is read-only — must not prompt even in plan mode."""
    gate = _AlwaysAllow("plan")
    assert gate.check("note", {"action": "list"}) is True


def test_note_write_blocked_in_plan_mode():
    """note(action='write') mutates state — should be blocked in plan mode."""
    gate = _AlwaysAllow("plan")
    assert gate.check("note", {"action": "write", "name": "x", "content": "y"}) is False


def test_note_append_blocked_in_plan_mode():
    """note(action='append') mutates state — should be blocked in plan mode."""
    gate = _AlwaysAllow("plan")
    assert gate.check("note", {"action": "append", "name": "x", "content": "y"}) is False


def test_note_delete_blocked_in_plan_mode():
    """note(action='delete') mutates state — should be blocked in plan mode."""
    gate = _AlwaysAllow("plan")
    assert gate.check("note", {"action": "delete", "name": "x"}) is False


def test_note_write_prompts_in_confirm_mode():
    """note(action='write') in confirm mode should go through _ask (not bypass)."""
    gate = _AlwaysAllow("confirm")
    assert gate.check("note", {"action": "write", "name": "x"}) is True  # _ask returns True
