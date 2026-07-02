from __future__ import annotations

from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text


def mask_key(key: str) -> str:
    """Show only the last 4 chars for keys long enough that 4 chars isn't most of it;
    never leak the full key. Short keys (<=8 chars) are fully masked."""
    if not key or len(key) <= 8:
        return "****"
    return f"****{key[-4:]}"


def render_markdown(text: str):
    return Panel(Markdown(text), title="coderio", border_style="blue")


def render_error(message: str):
    return Panel(Text(message, style="red"), title="error", border_style="red")


def render_tool_call(name: str, args: dict[str, Any]):
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return Text(f"● {name}({args_str})", style="cyan")
