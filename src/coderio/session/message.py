from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "args": self.args}

    @classmethod
    def from_dict(cls, d: dict) -> "ToolCall":
        return cls(id=d.get("id"), name=d.get("name"), args=d.get("args"))


@dataclass
class Message:
    role: Literal["user", "assistant", "tool", "system"]
    # content is normally a str; for multimodal user messages it may be a list of
    # content blocks (e.g. [{"type":"text",...},{"type":"image",...}]) which
    # langchain's HumanMessage accepts directly. For system messages with
    # kind="phase_timeline", content holds the serialized timeline JSON.
    content: str | list = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    # Sub-kind for system-role messages: "phase_timeline" (turn-end phase record),
    # "context_summary" (compacted history), "restart_checkpoint". Empty for
    # non-system messages. Lets loaders filter system messages by purpose.
    kind: str = ""
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def assistant(
        cls, content: str, tool_calls: list[ToolCall] | None = None
    ) -> "Message":
        return cls(role="assistant", content=content, tool_calls=tool_calls)

    @classmethod
    def tool_result(cls, tool_call_id: str, name: str, content: str) -> "Message":
        return cls(role="tool", content=content, tool_call_id=tool_call_id, name=name)

    @classmethod
    def system(cls, content: str, kind: str = "") -> "Message":
        """A system-role message: phase timeline, context summary, or checkpoint.
        Not shown in conversation history; filtered by ``kind``."""
        return cls(role="system", content=content, kind=kind)

    def to_dict(self) -> dict:
        d = {"role": self.role, "content": self.content, "timestamp": self.timestamp}
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        if self.kind:
            d["kind"] = self.kind
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(
            role=d["role"],
            content=d.get("content", ""),
            tool_calls=[ToolCall.from_dict(tc) for tc in d.get("tool_calls")]
            if d.get("tool_calls")
            else None,
            tool_call_id=d.get("tool_call_id"),
            name=d.get("name"),
            kind=d.get("kind", ""),
            timestamp=d.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
