"""coderio tools."""

from coderio.tools.base import (
    DESTRUCTIVE_TOOLS,
    Tool,
    to_langchain_tool,
)
from coderio.tools.bash import BashTool
from coderio.tools.edit_file import EditFileTool
from coderio.tools.glob_tool import GlobTool
from coderio.tools.grep_tool import GrepTool
from coderio.tools.list_dir import ListDirTool
from coderio.tools.multi_edit import MultiEditTool
from coderio.tools.note import NoteTool
from coderio.tools.permission import (
    AutoPermissionGate,
    PermissionGate,
    PermissionMode,
    RichPromptPermissionGate,
)
from coderio.tools.read_file import ReadFileTool
from coderio.tools.todo import TodoStore, TodoTool
from coderio.tools.web_fetch import WebFetchTool
from coderio.tools.web_search import WebSearchTool
from coderio.tools.write_file import WriteFileTool

__all__ = [
    "Tool",
    "to_langchain_tool",
    "DESTRUCTIVE_TOOLS",
    "PermissionGate",
    "PermissionMode",
    "RichPromptPermissionGate",
    "AutoPermissionGate",
    "TodoStore",
    "build_default_tools",
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
