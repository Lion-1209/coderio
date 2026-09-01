from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

# Tool classification by permission risk level. Single source of truth lives
# in tools/taxonomy.py (2026-08-28 audit found 6+ ad-hoc copies of these sets
# drifting apart). Re-exported here for the permission model's existing
# import path.
from coderio.tools.taxonomy import DESTRUCTIVE_TOOLS, FILE_EDIT_TOOLS  # noqa: F401


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
