"""Textual-based TUI for coderio — interactive expand (Ctrl+O / mouse), scrollable history.

This is the Textual upgrade from the Rich-only REPL. It implements the same
StreamHandler protocol (so agent/tools/harness call it identically), but via a
real Textual App with:
  - A scrollable RichLog for the conversation history (panels, thinking, tools).
  - An Input at the bottom for user messages + slash commands.
  - Ctrl+O binding to expand the last collapsed thinking.
  - Mouse-clickable regions (Textual supports mouse events natively).

The agent layer (run_agent) is unchanged — it calls StreamHandler hooks; this
class just implements them with Textual widgets instead of Rich Live/Console.
"""
from __future__ import annotations

import time
from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, RichLog


class CoderioTUI(App):
    """coderio's Textual app. Implements StreamHandler (duck-typed).

    Usage:
        tui = CoderioTUI()
        tui.run()  # blocks until exit; the on_input callback drives run_agent
    """

    CSS = """
    Screen { layout: vertical; }
    #log { border: round $accent; height: 1fr; min-height: 10; }
    #log:focus { border: round $accent; }
    #input-bar { height: auto; dock: bottom; border-top: solid $accent; }
    #input-bar Input { border: none; }
    #input-bar Label { color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        Binding("ctrl+o", "expand_thinking", "展开思考", show=True),
        Binding("ctrl+c", "quit", "退出", show=False),
    ]

    name = "textual_tui"  # StreamHandler protocol compat

    def __init__(self, on_input=None, show_tool_output: bool = True,
                 banner: str | None = None) -> None:
        super().__init__()
        self._on_input = on_input  # callback(line: str) called on each user submit
        self.show_tool_output = show_tool_output
        self._banner = banner  # startup info (model/perm/mode), shown on mount
        # StreamHandler state
        self.buffer = ""
        self.usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._last_thinking = ""
        self._last_think_secs = 0.0
        self._busy_active = False

    # ----------------------------------------------------- Textual layout
    def compose(self) -> ComposeResult:
        yield RichLog(id="log", wrap=True, markup=True, auto_scroll=True)
        with Vertical(id="input-bar"):
            yield Input(placeholder="输入消息, /help 看命令, Ctrl+O 展开思考", id="msg")

    def on_mount(self) -> None:
        self.title = "coderio"
        self.sub_title = "skill-driven coding agent"
        if self._banner:
            self._write_main(Panel(self._banner, title="[bold magenta]coderio[/bold magenta]", border_style="magenta"))
        self.query_one("#msg", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """User pressed Enter in the input box."""
        line = event.value.strip()
        if not line:
            return
        event.input.value = ""
        # Echo the user's input into the log
        self._write(Text(f"▸ you {line}", style="bold cyan"))
        if self._on_input:
            import threading
            # run_agent is synchronous and may take a long time (model calls,
            # tool loops). Run it in a background thread so the Textual event loop
            # stays responsive (Ctrl+O, scrolling, etc.). StreamHandler methods
            # are called from this thread; they dispatch UI updates via
            # call_from_thread (thread-safe).
            def _run():
                try:
                    self._on_input(line)
                except SystemExit:
                    self.call_from_thread(self.exit)
                except Exception as e:
                    self.call_from_thread(self._write,
                                          Text(f"运行错误: {type(e).__name__}: {e}", style="red"))
            t = threading.Thread(target=_run, daemon=True)
            t.start()

    # ----------------------------------------------------- binding actions
    def action_expand_thinking(self) -> None:
        """Ctrl+O — expand the last round's thinking."""
        self.show_last_thinking()

    # ----------------------------------------------------- StreamHandler protocol
    def on_step_start(self) -> None:
        self._flush_round_thinking()
        self._busy_active = True
        # Textual doesn't have a spinner widget like Rich Live, so we show a
        # transient line; the actual thinking text streams via on_thinking.
        self._round_think_start = time.monotonic()

    def on_token(self, text: str) -> None:
        self._flush_round_thinking()
        self._busy_active = False
        self.buffer += text

    def on_thinking(self, text: str) -> None:
        if not self._round_thinking:
            self._round_think_start = time.monotonic()
        self._round_thinking += text

    def on_tool_start(self, name: str, args: dict[str, Any]) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        if len(args_str) > 100:
            args_str = args_str[:100] + "…"
        self._write(Text(f"⏺ {name}({args_str})", style="green"))

    def on_tool_end(self, name: str, result: str) -> None:
        if not self.show_tool_output:
            first = result.splitlines()[0][:60] if result.splitlines() else ""
            self._write(Text(f"  → {first}{'…' if len(result) > 60 else ''}", style="dim"))
            return
        lines = result.splitlines()
        shown = "\n".join(lines[:3])
        if len(lines) > 3:
            shown += f"\n…({len(lines) - 3} more lines)"
        self._write(Text(shown, style="dim"))

    def on_truncated(self, stop_reason: str) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._write(Panel(
            f"⚠ 输出被截断 (stop_reason: {stop_reason})。",
            title="截断警告", border_style="yellow"))

    def on_harness_warn(self, message: str) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._write(Panel(
            f"⚠ {message}\n\n[dim]产出可能未经验证，请人工复核。[/dim]",
            title="[bold red]⚠ harness 警告[/bold red]", border_style="red"))

    def on_finish(self) -> None:
        self._flush_round_thinking()
        self._flush_buffer()

    def add_usage(self, meta: dict[str, int]) -> None:
        for k in ("input_tokens", "output_tokens"):
            if k in meta:
                self.usage[k] += meta[k]

    # ----------------------------------------------------- thinking fold
    def _flush_round_thinking(self) -> None:
        if not self._round_thinking.strip():
            return
        secs = time.monotonic() - self._round_think_start if self._round_think_start else 0.0
        chars = len(self._round_thinking)
        self._last_thinking = self._round_thinking
        self._last_think_secs = secs
        self._write(Text(
            f"💭 思考 · {secs:.1f}s · {chars} 字 · Ctrl+O 展开", style="dim italic"))
        self._round_thinking = ""
        self._round_think_start = 0.0

    def show_last_thinking(self) -> bool:
        if not self._last_thinking.strip():
            self._write(Text("最近一轮没有思考内容。", style="dim"))
            return False
        self._write(Panel(
            Text(self._last_thinking),
            title=f"[dim]💭 思考 · {self._last_think_secs:.1f}s[/dim]",
            border_style="dim"))
        return True

    # ----------------------------------------------------- helpers
    def _flush_buffer(self) -> None:
        """Render accumulated assistant text (buffer) as a Markdown panel."""
        if self.buffer.strip():
            self._write(Panel(Markdown(self.buffer), border_style="blue", title="[dim]coderio[/dim]"))
        self.buffer = ""

    def _write(self, renderable) -> None:
        """Write a Rich renderable to the scrollable log. Thread-safe: if called
        from the agent worker thread, dispatches to the main Textual thread."""
        import threading
        if threading.current_thread() is not threading.main_thread():
            # Called from the agent background thread — marshal to main thread.
            self.call_from_thread(self._write_main, renderable)
        else:
            self._write_main(renderable)

    def _write_main(self, renderable) -> None:
        """Actual widget write — must run on the Textual main thread."""
        try:
            log = self.query_one("#log", RichLog)
            log.write(renderable)
        except Exception:
            # App not mounted yet or shutting down — best-effort
            pass


def run_tui(provider_override: str | None = None, model_override: str | None = None) -> None:
    """Launch the Textual TUI, wired to coderio's agent runtime.

    Builds the same runtime as the Rich REPL (config, model, tools, skills,
    session), then runs the CoderioTUI app. Each user submission drives
    run_agent in a background thread; the TUI stays interactive (Ctrl+O, scroll).
    """
    from pathlib import Path
    from coderio.cli.repl import build_runtime
    from coderio.config.bootstrap import ensure_user_dirs
    from coderio.config import load_config
    from coderio.llm import build_chat_model
    from coderio.skills.store import load_skill_store
    from coderio.tools import build_default_tools
    from coderio.tools.permission import AutoPermissionGate

    ensure_user_dirs()
    search_from = "."
    creds_path = Path.home() / ".coderio" / "credentials"
    try:
        cfg, store, model, tools, gate, session, active, _rich_stream = build_runtime(
            search_from=search_from, console=None, creds_path=creds_path,
            provider_override=provider_override, model_override=model_override,
        )
    except Exception as e:
        # Init errors must not crash with a raw traceback. Show a minimal TUI
        # with just the error, so the user sees it instead of a stack trace.
        tui = CoderioTUI()
        tui._banner = (
            f"[red]启动失败:[/red] {type(e).__name__}: {e}\n\n"
            "[dim]常见原因: API key 未配置 / provider 无效 / 网络不通。[/dim]\n"
            "[dim]运行 coderio config 检查配置, 或设置 ANTHROPIC_API_KEY 环境变量。[/dim]"
        )
        tui.run()
        return

    banner = (
        f"[bold magenta]coderio[/bold magenta]  [dim]model=[/dim]{cfg.model.default}  "
        f"[dim]perm=[/dim]{gate.mode}"
        "\n[dim]模式:[/dim] [cyan]single-agent (Textual TUI)[/cyan]. "
        "大需求可用 [yellow]/crew[/yellow] 进 6-agent 流水线."
        "\n[dim]输入 /help 看命令, /exit 退出, Ctrl+O 展开思考[/dim]"
    )

    def on_input(line: str) -> None:
        """Called (in a background thread) when the user submits input."""
        if line.startswith("/"):
            # Slash commands reuse the Rich REPL's handler (it prints to console;
            # for TUI we capture the result and write to the log instead).
            from coderio.cli.commands import handle_slash, ReplContext
            from coderio.session.store import Session
            from pathlib import Path as _P
            ctx = ReplContext(
                available_skills=store.names(),
                active_skills_names={s.name for s in active.all()},
                permission_mode=gate.mode,
                model_name=cfg.model.default,
                provider_id=cfg.model.provider_id,
                api_key="",
                base_url=cfg.model.base_url,
                recent_sessions=Session.list_recent(_P(cfg.session.save_dir).expanduser()),
                usage=tui.usage,
                stream=tui,
            )
            res = handle_slash(line, ctx)
            if res.message:
                tui._write(Text(res.message))
            if not res.continue_loop:
                tui.exit()
            return
        # Normal agent turn — multimodal image detection + run_agent.
        from coderio.agent.loop import run_agent
        from coderio.cli.multimodal import build_user_content, extract_images
        imgs = extract_images(line)
        if imgs:
            tui._write(Text(f"📎 已附加 {len(imgs)} 张图片: " + ", ".join(p for p, _, _ in imgs), style="dim"))
        user_content = build_user_content(line)
        run_agent(
            user_input=user_content, model=model, tools=tools, gate=gate,
            skill_store=store, active_skills=active, session=session, stream=tui,
            max_rounds=cfg.tools.max_tool_rounds,
            stage_auto_inject=cfg.skills.stage_auto_inject,
            harness_enabled=cfg.skills.harness,
        )

    tui = CoderioTUI(on_input=on_input, show_tool_output=cfg.cli.show_tool_output, banner=banner)
    tui.run()
