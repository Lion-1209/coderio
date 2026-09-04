"""Small rendering helpers shared by the REPL/TUI.

P2 cleanup (2026-09-04): render_markdown / render_error / render_tool_call
were removed — they had zero production callers (only their own test file
imported them); the real panels live inline in stream.py / tui.py with
context-specific styling.
"""

from __future__ import annotations


def mask_key(key: str) -> str:
    """Show only the last 4 chars for keys long enough that 4 chars isn't most of it;
    never leak the full key. Short keys (<=8 chars) are fully masked."""
    if not key or len(key) <= 8:
        return "****"
    return f"****{key[-4:]}"
