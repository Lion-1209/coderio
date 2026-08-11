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
    for t in ("read_file", "glob", "grep", "write_todos", "ls"):
        assert gate.check(t, {}) is True


def test_plan_mode_blocks_write_tools():
    gate = _AlwaysAllow("plan")
    for t in ("write_file", "edit_file", "execute", "web_fetch"):
        assert gate.check(t, {}) is False


def test_plan_mode_blocks_multi_edit():
    """REGRESSION (2026-08-07 report P1-1): multi_edit was missing from
    DESTRUCTIVE_TOOLS, so plan/confirm/auto_edit modes treated it as read-only
    and let it through without asking — a permission-model hole. multi_edit is
    an atomic multi-edit of one file; it must be gated like edit_file."""
    gate = _AlwaysAllow("plan")
    assert gate.check("multi_edit", {}) is False, "multi_edit must be destructive"
    # And confirm mode must ASK (trigger _ask), not auto-allow.
    confirm_gate = _AlwaysAllow_AskTracker("confirm")
    confirm_gate.check("multi_edit", {})
    assert "multi_edit" in confirm_gate.asked, "confirm mode must prompt for multi_edit"


def test_auto_mode_allows_all():
    """Full access mode (legacy 'auto' maps to 'full') allows everything."""
    gate = _AlwaysAllow("full")
    assert gate.check("execute", {}) is True
    assert gate.check("write_file", {}) is True


def test_auto_legacy_maps_to_full():
    """Old configs with permission_mode='auto' should silently upgrade to FULL."""
    gate = _AlwaysAllow("auto")  # normalize() maps "auto" -> FULL
    assert gate.check("execute", {}) is True
    assert gate.check("write_file", {}) is True
    assert gate.mode == PermissionMode.FULL


def test_auto_edit_mode_allows_file_edits():
    """Auto Edit mode auto-approves file edits without prompting."""
    gate = _AlwaysAllow("auto_edit")
    assert gate.check("write_file", {}) is True
    assert gate.check("edit_file", {}) is True


def test_auto_edit_mode_confirms_high_risk():
    """Auto Edit mode still prompts for execute/web_fetch/note-writes."""
    gate = _AlwaysAllow_AskTracker("auto_edit")
    gate.check("execute", {})
    gate.check("web_fetch", {})
    gate.check("note", {"action": "write"})
    assert gate.asked == ["execute", "web_fetch", "note"]


def test_auto_edit_mode_does_not_ask_for_file_edits():
    """File edits in Auto Edit mode must NOT trigger _ask."""
    gate = _AlwaysAllow_AskTracker("auto_edit")
    gate.check("write_file", {})
    gate.check("edit_file", {})
    assert gate.asked == [], f"file edits should not ask, got: {gate.asked}"


def test_full_mode_allows_all():
    """Full access allows everything without asking."""
    gate = _AlwaysAllow_AskTracker("full")
    gate.check("execute", {})
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


# --- MCP tool heuristic classification (P1-5c, 2026-08-10 report) ---
# MCP tools arrive with server-prefixed names (filesystem_write_file, etc.) that
# aren't in DESTRUCTIVE_TOOLS. Without heuristic classification, PLAN mode would
# let a destructive MCP tool through — a permission-model hole.


def test_mcp_write_tool_blocked_in_plan_mode():
    """An MCP tool whose name contains 'write' must be gated in PLAN mode.

    Regression guard: before the _is_mcp_destructive heuristic, a PLAN-mode
    agent could call filesystem_write_file freely because the name wasn't in
    DESTRUCTIVE_TOOLS and the old check() returned True for anything unknown.
    """
    gate = _AlwaysAllow("plan")
    assert gate.check("filesystem_write_file", {}) is False
    assert gate.check("github_create_pr", {}) is False
    assert gate.check("db_delete_row", {}) is False


def test_mcp_execute_tool_blocked_in_plan_mode():
    """MCP execute/run/shell tools are gated in PLAN mode."""
    gate = _AlwaysAllow("plan")
    assert gate.check("custom_exec_command", {}) is False
    assert gate.check("sandbox_run_script", {}) is False
    assert gate.check("cloud_shell_exec", {}) is False


def test_mcp_network_tool_blocked_in_plan_mode():
    """MCP fetch/request/post tools (network egress) are gated in PLAN mode."""
    gate = _AlwaysAllow("plan")
    assert gate.check("api_fetch_url", {}) is False
    assert gate.check("http_request", {}) is False


def test_mcp_read_tool_allowed_in_plan_mode():
    """MCP read-only tools (no destructive keyword) pass in PLAN mode."""
    gate = _AlwaysAllow("plan")
    assert gate.check("filesystem_read_file", {}) is True
    assert gate.check("github_get_issue", {}) is True
    assert gate.check("db_query", {}) is True


def test_mcp_write_tool_allowed_in_full_mode():
    """FULL mode auto-approves MCP destructive tools (same as built-in)."""
    gate = _AlwaysAllow_AskTracker("full")
    assert gate.check("filesystem_write_file", {}) is True
    assert gate.asked == [], "FULL mode should never call _ask (even for MCP)"


def test_mcp_destructive_confirms_in_confirm_mode():
    """CONFIRM mode prompts for MCP destructive tools (goes through _ask)."""
    gate = _AlwaysAllow_AskTracker("confirm")
    gate.check("filesystem_write_file", {})
    gate.check("github_create_pr", {})
    assert "filesystem_write_file" in gate.asked
    assert "github_create_pr" in gate.asked


def test_mcp_destructive_confirms_in_auto_edit_mode():
    """AUTO_EDIT mode is conservative for MCP: destructive MCP tools always
    confirm (we can't reliably distinguish an MCP write from an MCP execute,
    so we don't auto-allow either)."""
    gate = _AlwaysAllow_AskTracker("auto_edit")
    gate.check("filesystem_write_file", {})
    assert "filesystem_write_file" in gate.asked, (
        "AUTO_EDIT should confirm MCP destructive tools (conservative default)"
    )


def test_mcp_keyword_substring_matching():
    """The keyword match is substring-based (case-insensitive), so 'ReWrite'
    and 'WRITE_FILE' both match 'write'."""
    from coderio.tools.permission import _is_mcp_destructive

    assert _is_mcp_destructive("filesystem_write_file")
    assert _is_mcp_destructive("GitHub_Create_PR")
    assert _is_mcp_destructive("DB_DELETE_Row")
    assert not _is_mcp_destructive("read_file")
    assert not _is_mcp_destructive("get_status")
    assert not _is_mcp_destructive("list_items")


def test_write_todos_not_misclassified_as_destructive():
    """REGRESSION GUARD: write_todos (deepagents' planning tool) contains
    'write' but must NOT be flagged destructive — it only updates the in-memory
    todo list, it doesn't write files. Without this exclusion, PLAN mode would
    block the agent from ever creating a todo list, breaking the harness's
    CompletionGate (which relies on todos being present)."""
    from coderio.tools.permission import _is_mcp_destructive

    assert not _is_mcp_destructive("write_todos"), "write_todos must be excluded from the MCP destructive heuristic"
    # And in actual gate behavior: PLAN mode must allow write_todos.
    gate = _AlwaysAllow("plan")
    assert gate.check("write_todos", {}) is True
