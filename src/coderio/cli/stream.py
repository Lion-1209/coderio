from __future__ import annotations

import time
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text


class RichStream:
    """StreamHandler impl: live token stream + final markdown re-render.

    Implements coderio.agent.stream.StreamHandler (duck-typed; no inherit needed).
    A single ALWAYS-ON busy indicator covers the whole model wait (thinking,
    generation, and the gaps between a tool result and the next model call) with
    an elapsed-time timer, so the UI never looks frozen.

    Thinking is captured per-round and rendered COLLAPSED by default (a one-line
    summary), with the full text available via show_last_thinking() (the /think
    command). This keeps the screen uncluttered while preserving auditability.
    """

    name = "rich_stream"

    def __init__(self, console: Console, show_tool_output: bool = True) -> None:
        self.console = console
        self.buffer = ""
        self._live = None

        self._busy_live = None
        self._busy_buf = ""
        self._busy_start = 0.0
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.show_tool_output = show_tool_output

        # Per-round thinking capture (for collapsed display + /think expand).
        self._round_thinking = ""      # accumulates the current round's thinking
        self._round_think_start = 0.0  # when thinking started this round
        self._last_thinking = ""       # the most recent completed round's full text
        self._last_think_secs = 0.0    # duration of that round's thinking

    # --------------------------------------------------------------- busy indicator
    def _start_busy(self) -> None:
        self._busy_buf = ""
        self._busy_start = time.monotonic()
        if self._busy_live is None:
            self._busy_live = Live(
                get_renderable=self._busy_renderable,
                console=self.console,
                refresh_per_second=10,
                transient=True,
            )
            self._busy_live.start()

    def _refresh_busy(self) -> None:
        if self._busy_live is not None:
            self._busy_live.refresh()

    def _busy_renderable(self) -> Spinner:
        elapsed = time.monotonic() - self._busy_start
        preview = self._busy_buf[-50:].replace("\n", " ")
        suffix = f" 正在思考… {elapsed:4.1f}s"
        if preview:
            suffix += f"  {preview}"
        return Spinner("dots", text=Text(suffix, style="dim"))

    def _stop_busy(self) -> None:
        if self._busy_live is not None:
            self._busy_live.stop()
            self._busy_live = None
            self._busy_buf = ""

    def _flush_round_thinking(self) -> None:
        """If the current round accumulated thinking text, render a collapsed
        summary and stash the full text for /think. Called when the thinking
        phase ends (first token, tool call, or finish)."""
        if not self._round_thinking.strip():
            return
        secs = time.monotonic() - self._round_think_start if self._round_think_start else 0.0
        chars = len(self._round_thinking)
        self._last_thinking = self._round_thinking
        self._last_think_secs = secs
        # Collapsed summary line (full text via show_last_thinking()).
        self.console.print(
            Text(f"💭 思考 · {secs:.1f}s · {chars} 字 · /think 展开", style="dim italic")
        )
        self._round_thinking = ""
        self._round_think_start = 0.0

    def show_last_thinking(self) -> bool:
        """Expand the most recent round's full thinking text. Returns True if
        there was something to show (used by the /think command)."""
        if not self._last_thinking.strip():
            self.console.print("[dim]最近一轮没有思考内容。[/dim]")
            return False
        secs = self._last_think_secs
        self.console.print(
            Panel(
                Text(self._last_thinking),
                title=f"[dim]💭 思考 · {secs:.1f}s[/dim]",
                border_style="dim",
            )
        )
        return True

    # -------------------------------------------------------- StreamHandler protocol
    def on_step_start(self) -> None:
        self._stop_live()
        self._start_busy()

    def on_token(self, text: str) -> None:
        # First visible token: the thinking phase is over — flush its summary.
        self._flush_round_thinking()
        self._stop_busy()
        self.buffer += text
        if self._live is None:
            self._live = Live(Text(self.buffer), console=self.console, refresh_per_second=15)
            self._live.start()
        else:
            self._live.update(Text(self.buffer))

    def on_thinking(self, text: str) -> None:
        """Accumulate thinking into the round buffer (for later collapsed display)
        AND into the busy preview (live feedback during the wait)."""
        if not self._round_thinking:
            self._round_think_start = time.monotonic()
        self._round_thinking += text
        self._busy_buf += text
        if self._busy_live is None:
            self._start_busy()
        else:
            self._refresh_busy()

    def on_tool_start(self, name: str, args: dict[str, Any]) -> None:
        # A tool call also ends the thinking phase.
        self._flush_round_thinking()
        self._stop_live()
        self._stop_busy()
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        if len(args_str) > 100:
            args_str = args_str[:100] + "…"
        self.console.print(f"[bold green]⏺[/bold green] [green]{name}[/green]({args_str})")

    def on_tool_end(self, name: str, result: str) -> None:
        if not self.show_tool_output:
            first = result.splitlines()[0][:60] if result.splitlines() else ""
            self.console.print(Text(f"  → {first}{'…' if len(result) > 60 else ''}", style="dim"))
            return
        lines = result.splitlines()
        shown = "\n".join(lines[:3])
        if len(lines) > 3:
            shown += f"\n…({len(lines) - 3} more lines)"
        self.console.print(Text(shown, style="dim"))

    def on_truncated(self, stop_reason: str) -> None:
        self._flush_round_thinking()
        self._stop_live()
        self._stop_busy()
        self.console.print(
            Panel(
                f"⚠ 输出被截断 (stop_reason: {stop_reason})。内容可能不完整 —— 可能需要分多次完成或调大 max_tokens。",
                title="截断警告",
                border_style="yellow",
            )
        )

    def on_harness_warn(self, message: str) -> None:
        self._flush_round_thinking()
        self._stop_live()
        self._stop_busy()
        self.console.print(
            Panel(
                f"⚠ {message}\n\n[dim]产出可能未经验证，请人工复核。[/dim]",
                title="[bold red]⚠ harness 警告[/bold red]",
                border_style="red",
            )
        )

    def on_finish(self) -> None:
        self._flush_round_thinking()
        self._stop_live()
        self._stop_busy()
        if self.buffer.strip():
            self.console.print(
                Panel(
                    Markdown(self.buffer),
                    border_style="blue",
                    title="[dim]coderio[/dim]",
                )
            )

    def add_usage(self, meta: dict[str, int]) -> None:
        for k in ("input_tokens", "output_tokens"):
            if k in meta:
                self.usage[k] += meta[k]

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
            self.buffer = ""
