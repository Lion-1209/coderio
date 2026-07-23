"""coderio tools."""

from coderio.tools.base import (
    DESTRUCTIVE_TOOLS,
    READONLY_TOOLS,
    Tool,
    to_langchain_tool,
)
from coderio.tools.bash import BashArgs, BashTool
from coderio.tools.edit_file import EditFileArgs, EditFileTool
from coderio.tools.glob_tool import GlobArgs, GlobTool
from coderio.tools.grep_tool import GrepArgs, GrepTool
from coderio.tools.list_dir import ListDirArgs, ListDirTool
from coderio.tools.multi_edit import MultiEditArgs, MultiEditTool
from coderio.tools.note import NoteArgs, NoteTool
from coderio.tools.permission import (
    AutoPermissionGate,
    PermissionGate,
    PermissionMode,
    RichPromptPermissionGate,
)
from coderio.tools.read_file import ReadFileArgs, ReadFileTool
from coderio.tools.todo import TodoArgs, TodoStore, TodoTool
from coderio.tools.web_fetch import WebFetchArgs, WebFetchTool
from coderio.tools.web_search import WebSearchArgs, WebSearchTool
from coderio.tools.workspace import WorkspacePolicy
from coderio.tools.write_file import WriteFileArgs, WriteFileTool

__all__ = [
    "Tool",
    "to_langchain_tool",
    "DESTRUCTIVE_TOOLS",
    "READONLY_TOOLS",
    "PermissionGate",
    "PermissionMode",
    "RichPromptPermissionGate",
    "AutoPermissionGate",
    "WorkspacePolicy",
    "TodoStore",
    "build_default_tools",
    "to_langchain_tools",
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
