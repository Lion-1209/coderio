"""coderio tools."""

from coderio.tools.base import (
    Tool,
    to_langchain_tool,
    DESTRUCTIVE_TOOLS,
    READONLY_TOOLS,
)
from coderio.tools.permission import (
    PermissionGate,
    PermissionMode,
    RichPromptPermissionGate,
    AutoPermissionGate,
)
from coderio.tools.workspace import WorkspacePolicy
from coderio.tools.read_file import ReadFileTool, ReadFileArgs
from coderio.tools.write_file import WriteFileTool, WriteFileArgs
from coderio.tools.edit_file import EditFileTool, EditFileArgs
from coderio.tools.multi_edit import MultiEditTool, MultiEditArgs
from coderio.tools.list_dir import ListDirTool, ListDirArgs
from coderio.tools.bash import BashTool, BashArgs
from coderio.tools.glob_tool import GlobTool, GlobArgs
from coderio.tools.grep_tool import GrepTool, GrepArgs
from coderio.tools.todo import TodoStore, TodoTool, TodoArgs
from coderio.tools.web_search import WebSearchTool, WebSearchArgs
from coderio.tools.web_fetch import WebFetchTool, WebFetchArgs
from coderio.tools.note import NoteTool, NoteArgs

__all__ = [
    "Tool", "to_langchain_tool", "DESTRUCTIVE_TOOLS", "READONLY_TOOLS",
    "PermissionGate", "PermissionMode", "RichPromptPermissionGate", "AutoPermissionGate",
    "WorkspacePolicy",
    "TodoStore", "build_default_tools", "to_langchain_tools",
]


def build_default_tools(bash_shell: str = "", **_) -> list:
    """Return the default tool set (12 tools)."""
    return [
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        MultiEditTool(),
        ListDirTool(),
        BashTool(shell=bash_shell),
        GlobTool(),
        GrepTool(),
        TodoTool(TodoStore()),
        WebSearchTool(),
        WebFetchTool(),
        NoteTool(),
    ]


_ARGS_SCHEMAS: dict = {
    "read_file": ReadFileArgs,
    "write_file": WriteFileArgs,
    "edit_file": EditFileArgs,
    "multi_edit": MultiEditArgs,
    "list_dir": ListDirArgs,
    "bash": BashArgs,
    "glob": GlobArgs,
    "grep": GrepArgs,
    "todo": TodoArgs,
    "web_search": WebSearchArgs,
    "web_fetch": WebFetchArgs,
    "note": NoteArgs,
}


def to_langchain_tools(tools: list, extra: dict | None = None) -> list:
    """Adapt coderio tools (+ optional extras like activate_skill) to langchain tools.

    `extra` maps tool name -> args_schema for tools not in the default set.
    """
    schemas = dict(_ARGS_SCHEMAS)
    if extra:
        schemas.update(extra)
    out = []
    for t in tools:
        schema = schemas.get(t.name)
        if schema is None:
            continue
        out.append(to_langchain_tool(t, schema))
    return out
