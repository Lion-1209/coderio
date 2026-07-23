from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class TodoArgs(BaseModel):
    action: str = Field(description="One of: list, add, update, delete.")
    content: str = Field(default="", description="Todo text (for 'add').")
    priority: str = Field(default="medium", description="high | medium | low (for 'add').")
    index: int = Field(default=-1, description="Todo index (for 'update'/'delete').")
    status: str = Field(default="", description="New status (for 'update').")


@dataclass
class Todo:
    content: str
    status: str = "pending"
    priority: str = "medium"


@dataclass
class TodoStore:
    todos: list[Todo] = field(default_factory=list)


class TodoTool:
    name = "todo"
    description = "Manage a task list. actions: list, add, update, delete."
    args_schema = TodoArgs

    def __init__(self, store: TodoStore):
        self.store = store

    def run(
        self,
        action: str,
        content: str = "",
        priority: str = "medium",
        index: int = -1,
        status: str = "",
    ) -> str:
        if action == "list":
            if not self.store.todos:
                return "No todos."
            lines = []
            for i, t in enumerate(self.store.todos):
                if t.status == "completed":
                    mark = "✓"
                elif t.status == "in_progress":
                    mark = "→"
                else:
                    mark = " "
                lines.append(f"[{i}] {mark} ({t.priority}) {t.content}")
            return "\n".join(lines)
        elif action == "add":
            self.store.todos.append(Todo(content=content, priority=priority))
            return f"Added todo: {content}"
        elif action == "update":
            if not (0 <= index < len(self.store.todos)):
                return f"Error: index {index} out of range"
            if status:
                self.store.todos[index].status = status
            return f"Updated todo {index}"
        elif action == "delete":
            if not (0 <= index < len(self.store.todos)):
                return f"Error: index {index} out of range"
            self.store.todos.pop(index)
            return f"Deleted todo {index}"
        return f"Error: unknown action {action!r}"
