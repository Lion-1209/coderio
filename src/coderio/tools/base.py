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
DESTRUCTIVE_TOOLS = {"write_file", "edit_file", "execute", "web_fetch", "note"}
# Read-only tools: always allowed in all modes (backend virtual_mode handles
# path isolation — these tools cannot access files outside the workspace root).
READONLY_TOOLS = {"ls", "read_file", "glob", "grep", "write_todos", "web_search"}

# Auto Edit mode: file edits auto-allowed, high-risk tools still prompt.
# File edits are fast and reversible (git), but shell/network/note-writes
# have side effects that are harder to undo.
FILE_EDIT_TOOLS = {"write_file", "edit_file"}
HIGH_RISK_TOOLS = {"execute", "web_fetch", "note"}
