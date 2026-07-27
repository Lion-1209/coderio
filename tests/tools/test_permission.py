import pytest

from coderio.tools.permission import PermissionGate, PermissionMode


class _AlwaysAllow(PermissionGate):
    def __init__(self, mode):
        super().__init__(mode)

    def _ask(self, tool_name, args):
        return True


class _AlwaysAllow_AskTracker(PermissionGate):
    """Like _AlwaysAllow but records which tools triggered _ask."""

    def __init__(self, mode):
        super().__init__(mode)
        self.asked: list[str] = []

    def _ask(self, tool_name, args):
        self.asked.append(tool_name)
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
    """Full access mode (legacy 'auto' maps to 'full') allows everything."""
    gate = _AlwaysAllow("full")
    assert gate.check("bash", {}) is True
    assert gate.check("write_file", {}) is True


def test_auto_legacy_maps_to_full():
    """Old configs with permission_mode='auto' should silently upgrade to FULL."""
    gate = _AlwaysAllow("auto")  # normalize() maps "auto" -> FULL
    assert gate.check("bash", {}) is True
    assert gate.check("write_file", {}) is True
    assert gate.mode == PermissionMode.FULL


def test_auto_edit_mode_allows_file_edits():
    """Auto Edit mode auto-approves file edits without prompting."""
    gate = _AlwaysAllow("auto_edit")
    assert gate.check("write_file", {}) is True
    assert gate.check("edit_file", {}) is True
    assert gate.check("multi_edit", {}) is True


def test_auto_edit_mode_confirms_high_risk():
    """Auto Edit mode still prompts for bash/web_fetch/note-writes."""
    gate = _AlwaysAllow_AskTracker("auto_edit")
    gate.check("bash", {})
    gate.check("web_fetch", {})
    gate.check("note", {"action": "write"})
    assert gate.asked == ["bash", "web_fetch", "note"]


def test_auto_edit_mode_does_not_ask_for_file_edits():
    """File edits in Auto Edit mode must NOT trigger _ask."""
    gate = _AlwaysAllow_AskTracker("auto_edit")
    gate.check("write_file", {})
    gate.check("edit_file", {})
    gate.check("multi_edit", {})
    assert gate.asked == [], f"file edits should not ask, got: {gate.asked}"


def test_full_mode_allows_all():
    """Full access allows everything without asking."""
    gate = _AlwaysAllow_AskTracker("full")
    gate.check("bash", {})
    gate.check("write_file", {})
    gate.check("web_fetch", {})
    assert gate.asked == [], "FULL mode should never call _ask"


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
