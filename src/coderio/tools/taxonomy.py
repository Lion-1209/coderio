"""Single source of truth for tool names and categories.

WHY THIS EXISTS (2026-08-28 audit finding A2): tool names were defined
ad-hoc in 6+ places — deep_loop._SKIP, harness_middleware._to_coderio_name,
harness.WRITE_TOOLS/VERIFY_TOOL, tools/base.DESTRUCTIVE_TOOLS, tui.py task
special-case — and the drift between them caused multiple recorded P0
regressions (VerifyGate matching "bash" while the engine emits "execute";
exit_code regex keyed to the wrong tool). This module is the ONE registry;
every other module imports from here.

Two naming domains exist by design:
- Engine names: what the deepagents production engine actually exposes
  (execute, write_todos, write_file, edit_file, ...).
- Harness names: what the legacy Harness logic (agent/harness.py) expects
  ("bash", "todo") — kept so the existing gate logic and prose work
  unmodified. :func:`to_harness_name` translates between the domains.

The harness prose still says "bash" in a few injection strings; the
``bash``→``execute`` regex rewrite in agent/harness_middleware.py is the
compatibility shim for that prose. Retiring it means renaming the concept
inside harness.py, tracked as cleanup — the registry makes that a
single-file change when it happens.
"""

from __future__ import annotations

# --- engine (deepagents) tool names — the production namespace ------------
SHELL = "execute"
WRITE_TODOS = "write_todos"
WRITE_FILE = "write_file"
EDIT_FILE = "edit_file"
MULTI_EDIT = "multi_edit"
READ_FILE = "read_file"
GLOB = "glob"
GREP = "grep"
LIST_DIR = "ls"
LEGACY_LIST_DIR = "list_dir"  # coderio's own list_dir tool name
TASK = "task"
WEB_FETCH = "web_fetch"
NOTE = "note"

# --- harness (legacy) names -----------------------------------------------
LEGACY_SHELL = "bash"
LEGACY_TODO = "todo"

# --- categories (the permission/harness model reads these) ----------------

# Tools that modify files. Harness treats these as "verification required"
# evidence; the permission model treats them as confirm-mode prompts.
WRITE_TOOLS = frozenset({WRITE_FILE, EDIT_FILE, MULTI_EDIT})

# File-edit subset: auto_edit mode auto-approves these (fast + reversible
# via git); shell/network/note-writes still confirm (harder to undo).
FILE_EDIT_TOOLS = frozenset({WRITE_FILE, EDIT_FILE})

# Destructive tools: require permission in CONFIRM/AUTO_EDIT/PLAN modes.
# task is the subagent-delegation tool (2026-08-14 report P0-2): delegating
# spawns a subagent with write/execute tools — without gating task itself,
# PLAN mode (nominally read-only) could delegate destructive work.
DESTRUCTIVE_TOOLS = frozenset({WRITE_FILE, EDIT_FILE, MULTI_EDIT, SHELL, WEB_FETCH, NOTE, TASK})

# The harness's verification gate keys on the shell tool: "wrote code but
# never ran the shell" is the anti-fake-done signal.
VERIFY_TOOL = SHELL

# Todo/planning tool (CompletionGate: pending todos block "done").
TODO_TOOL = WRITE_TODOS

# Old ReAct-engine tool names: still constructed by build_default_tools but
# SKIPPED when the production engine is deepagents (deep_loop._build_extra_tools
# drops them — deepagents ships its own implementations of the same names).
# Only detect_shell (BashTool module) and TodoStore (TodoTool module) are still
# used from those modules. Kept as an explicit registry entry so the skip list
# has one definition.
LEGACY_ENGINE_TOOLS = frozenset(
    {READ_FILE, WRITE_FILE, EDIT_FILE, GLOB, GREP, LEGACY_SHELL, LEGACY_TODO, LEGACY_LIST_DIR}
)


def to_harness_name(name: str) -> str:
    """Translate an engine tool name to the name the legacy Harness expects.

    The Harness's verification/todo gates were written against coderio's
    original tool names; the production engine renamed them. Unknown names
    pass through unchanged (non-translated tools share both domains).
    """
    if name == SHELL:
        return LEGACY_SHELL
    if name == WRITE_TODOS:
        return LEGACY_TODO
    if name == LIST_DIR:
        return LEGACY_LIST_DIR
    return name
