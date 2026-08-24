"""Plan artifact: the agent's todo list materialized as an editable file.

The plan lives in graph state (deepagents' ``write_todos``), mirrored by
HarnessMiddleware into its TodoStore. That state is invisible to the user
editing in their editor — this module mirrors it to
``<project>/.coderio/plan.md`` so the plan becomes a real ARTIFACT:

- **materialize** (todos → file): called after every successful write_todos,
  skipped when content is unchanged (no mtime churn).
- **adopt_if_edited** (file → todos): called at each turn start BEFORE the
  model runs. If the user edited the file between turns, their version WINS —
  the human override signal — and the caller injects a note so the model knows
  its task list changed under it.

Format is a forgiving markdown checklist; anything that isn't a checklist item
(prose, headers, our HTML comment) is ignored on parse, so hand-edits can't
corrupt unrelated parts of the file. A file with ZERO checklist items parses
to None — adoption then does nothing rather than wiping the task list because
the user (or the agent) scribbled prose over it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from coderio.tools.todo import Todo, TodoStore

PLAN_FILENAME = "plan.md"

_HEADER = (
    "# coderio plan\n"
    "\n"
    "<!-- Agent-managed todo list. Edit freely (one '- [ ] task' per line);\n"
    "changes are adopted at the start of your next message. -->\n"
    "\n"
)

# "- [ ] content" / "- [x] content `(high)`" — X case-insensitive.
_ITEM_RE = re.compile(r"^-\s+\[( |x|X)\]\s+(.+?)\s*$")
_PRIORITY_RE = re.compile(r"\s*`(?:\(high\)|\(medium\)|\(low\))`\s*$")


def serialize_plan(todos: list[Todo]) -> str:
    lines = [_HEADER.rstrip("\n"), ""]
    for t in todos:
        mark = "x" if t.status == "completed" else " "
        pri = f" `({t.priority})`" if t.priority != "medium" else ""
        lines.append(f"- [{mark}] {t.content}{pri}")
    return "\n".join(lines) + "\n"


def parse_plan(text: str) -> list[Todo] | None:
    """Parse checklist items out of a (possibly hand-edited) plan file.

    Returns None when the text contains NO checklist items — callers must not
    replace a live task list with nothing just because the file was rewritten
    as prose.
    """
    todos: list[Todo] = []
    for line in text.splitlines():
        m = _ITEM_RE.match(line.strip())
        if not m:
            continue
        status = "completed" if m.group(1).lower() == "x" else "pending"
        content = m.group(2)
        priority = "medium"
        pm = _PRIORITY_RE.search(content)
        if pm:
            priority = pm.group(0).strip("`() ")
            content = _PRIORITY_RE.sub("", content).rstrip()
        todos.append(Todo(content=content, status=status, priority=priority))
    return todos or None


class PlanArtifact:
    """Two-way sync between a TodoStore and <anchor>/plan.md."""

    def __init__(self, anchor: Path | str, store: TodoStore) -> None:
        # anchor is the .coderio DIRECTORY (caller joins project root, same
        # convention as skills/commands/agents layer dirs).
        self.path = Path(anchor) / PLAN_FILENAME
        self.store = store

    # ------------------------------------------------------------ file → todos
    def adopt_if_edited(self) -> int:
        """Adopt external edits. Returns the number of tasks adopted (0 when
        the file is missing, unchanged, or has no parseable items)."""
        try:
            on_disk = self.path.read_text(encoding="utf-8")
        except OSError:
            return 0
        parsed = parse_plan(on_disk)
        if parsed is None:
            return 0
        if serialize_plan(parsed) == serialize_plan(self.store.todos):
            return 0  # same plan, different formatting — don't churn state
        self.store.todos = parsed
        return len(parsed)

    # ------------------------------------------------------------ todos → file
    def materialize(self) -> bool:
        """Write the current todo list to disk. Returns True when a write
        happened (False = missing dir or byte-identical content already there)."""
        try:
            rendered = serialize_plan(self.store.todos)
            if self.path.is_file() and self.path.read_text(encoding="utf-8") == rendered:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(rendered, encoding="utf-8")
            return True
        except OSError:
            return False


@dataclass(frozen=True)
class AdoptionNote:
    """Context injected into the user message when external edits were adopted,
    so the model doesn't keep executing a stale plan."""

    count: int
    path: Path

    def render(self) -> str:
        return (
            f"[plan artifact] {self.path} was modified externally and adopted: "
            f"{self.count} task(s) now define the plan (was mirrored from your "
            "todo list). Re-check todo state before continuing."
        )
