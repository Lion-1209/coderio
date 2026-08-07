from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel


@runtime_checkable
class Tool(Protocol):
    """Unified tool interface. Each tool declares a pydantic args_schema and a run()."""

    name: str
    description: str

    def run(self, **kwargs: Any) -> str: ...


def to_langchain_tool(tool: "Tool", args_schema: type[BaseModel]) -> StructuredTool:
    """Adapt a coderio Tool to a langchain StructuredTool (spec §3.1).

    The bound model then exposes the tool's JSON schema to the LLM and returns
    tool_calls whose args are validated against args_schema.
    """

    def _invoke(**kwargs: Any) -> str:
        return tool.run(**kwargs)

    return StructuredTool.from_function(
        _invoke,
        name=tool.name,
        description=tool.description,
        args_schema=args_schema,
    )


# Tool classification by permission risk level. Uses deepagents native tool
# names (execute/write_todos/ls) since the production engine is deepagents.
# The old ReAct engine names (bash/todo/list_dir) are no longer in use.

# Destructive tools: require permission in CONFIRM/AUTO_EDIT/PLAN modes.
# multi_edit is destructive (atomic multi-edit of one file) and must NOT be
# omitted — without it, CONFIRM/AUTO_EDIT modes treat multi_edit as read-only
# and let it through without asking (permission.py: check() returns True for
# any tool not in DESTRUCTIVE_TOOLS). The deepagents backend doesn't expose
# multi_edit, but this list is also the permission model's source of truth.
DESTRUCTIVE_TOOLS = {"write_file", "edit_file", "multi_edit", "execute", "web_fetch", "note"}

# Auto Edit mode: file edits auto-allowed (fast + reversible via git), but
# shell/network/note-writes still need confirmation (harder to undo side effects).
FILE_EDIT_TOOLS = {"write_file", "edit_file"}
