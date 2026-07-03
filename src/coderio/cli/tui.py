"""Textual-based TUI for coderio — foldable thinking, scrollable history.

Implements the StreamHandler protocol (so agent/tools/harness call it
identically to the Rich version). Each round's thinking is rendered as a
Collapsible widget — collapsed by default, expandable via Ctrl+O (toggles the
most recent) or mouse click on the title. This gives true fold/unfold, which
RichLog (append-only) could not.

The agent runs in a background thread; UI updates dispatch via call_from_thread.
"""
from __future__ import annotations

import time
from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Collapsible, Input, ListItem, ListView, Static


class CommandMenu(Vertical):
    """Popup slash-command menu (Claude-Code-style autocomplete).

    Shown when the input starts with "/". Lists matching commands, filtered live
    as the user types. ↑↓ navigates, Tab/Enter fills the chosen command into the
    input, Esc or clearing "/" hides it. Unlike SuggestFromList (which shows a
    single inline grey suggestion), this is a visible, browsable menu — the user
    can see all candidates and pick one with the keyboard.
    """

    DEFAULT_CSS = """
    CommandMenu {
        display: none;          /* hidden until input starts with "/" */
        dock: bottom;
        height: auto; max-height: 12;
        layer: above;
        background: $surface;
        border: round $accent;
        padding: 0;
        margin: 0 1;
    }
    CommandMenu.-visible { display: block; }
    CommandMenu ListView { background: $surface; }
    CommandMenu ListItem { padding: 0 1; }
    CommandMenu ListItem > Widget :hover { background: $boost; }
    """

    def __init__(self, completions: list[str]) -> None:
        super().__init__()
        self._all = completions
        self._input: Input | None = None  # the Input this menu feeds
        # Value last accepted by Tab/Enter. refresh_for skips reopening while the
        # input still equals this — otherwise setting .value in accept() retriggers
        # on_input_changed and the menu pops right back open.
        self._accepted_value: str | None = None

    def compose(self) -> ComposeResult:
        yield ListView(id="cmd-list")

    def bind_input(self, inp: Input) -> None:
        self._input = inp

    def _matches(self, prefix: str) -> list[str]:
        if not prefix:
            return []
        p = prefix.lower()
        # rank: exact prefix match first, then substring contains.
        exact = [c for c in self._all if c.lower().startswith(p)]
        sub = [c for c in self._all if p in c.lower() and c not in exact]
        return exact + sub

    def refresh_for(self, value: str) -> None:
        """Re-filter and show/hide based on the current input value."""
        # If the user just accepted a command (Tab/Enter), the value now equals
        # the chosen command. Don't reopen the menu for that exact value — it
        # was set programmatically by accept(), not typed. Once the user edits
        # further (value differs), normal filtering resumes.
        if value == self._accepted_value:
            return
        self._accepted_value = None  # value changed -> clear the guard
        if not value.startswith("/"):
            self.remove_class("-visible")
            return
        matches = self._matches(value)
        if not matches:
            self.remove_class("-visible")
            return
        lv = self.query_one("#cmd-list", ListView)
        lv.clear()
        for c in matches:
            lv.append(ListItem(Static(c), name=c))
        self.add_class("-visible")
        # auto-select the first (top) match so Enter is immediately meaningful
        try:
            lv.index = 0
        except Exception:
            pass

    def visible(self) -> bool:
        return self.has_class("-visible")

    def move(self, delta: int) -> None:
        """Move the selection by delta (+1 down, -1 up); wraps around."""
        if not self.visible():
            return
        lv = self.query_one("#cmd-list", ListView)
        if not lv.children:
            return
        n = len(lv.children)
        idx = lv.index or 0
        lv.index = (idx + delta) % n

    def accept(self) -> bool:
        """Fill the selected command into the bound Input. Returns True if accepted."""
        if not self.visible() or self._input is None:
            return False
        lv = self.query_one("#cmd-list", ListView)
        if not lv.children or lv.index is None:
            return False
        chosen = lv.children[lv.index].name or ""
        if not chosen:
            return False
        # Record the accepted value so the resulting on_input_changed doesn't
        # reopen the menu (setting .value fires changed, value still starts "/").
        self._accepted_value = chosen
        self._input.value = chosen
        self.remove_class("-visible")
        self._input.focus()
        return True

    def hide(self) -> None:
        self.remove_class("-visible")


class SessionPickerScreen(ModalScreen[str | None]):
    """Interactive session picker (Claude-Code-style /resume).

    Shows recent sessions as a scrollable list — each row has the first user
    message (so the user recognizes the conversation by what they asked, not by
    an opaque id), the message count, and the time. ↑↓ navigates, Enter resumes,
    Esc cancels. Typing filters the list by the summary text. Dismisses with the
    chosen session id (string) or None (cancelled).
    """

    CSS = """
    SessionPickerScreen {
        align: center middle;
    }
    #picker-box {
        width: 80%; height: 70%; border: thick $accent; background: $surface;
        padding: 1 2;
    }
    #picker-title { text-align: center; color: $accent; margin-bottom: 1; }
    #picker-filter {
        dock: bottom; margin-top: 1; border: round $accent 50%;
    }
    #picker-filter:focus { border: round $accent; }
    #picker-list { height: 1fr; border: none; }
    .picker-row { padding: 0 1; }
    .picker-row:first-child { margin-top: 0; }
    .picker-summary { color: $text; }
    .picker-meta { color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=True),
    ]

    def __init__(self, summaries: list[dict]) -> None:
        super().__init__()
        self._all = summaries  # full list; filtered view derived on typing
        self._filter = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static("[bold]恢复会话[/bold]  ↑↓ 选择 · Enter 恢复 · Esc 取消 · 输入过滤",
                         id="picker-title")
            yield ListView(id="picker-list")
            yield Input(placeholder="输入关键字过滤（首条消息 / 时间）", id="picker-filter")

    def on_mount(self) -> None:
        self._populate()
        self.query_one("#picker-filter", Input).focus()

    def _populate(self) -> None:
        """Rebuild the list from the (filtered) summaries."""
        lv = self.query_one("#picker-list", ListView)
        lv.clear()
        f = self._filter.lower()
        for s in self._all:
            label = s["first_user"] or "(空会话)"
            if f and f not in label.lower() and f not in s["mtime"].lower() and f not in s["id"].lower():
                continue
            row = Static(
                f"[{s['mtime']}] {label}\n"
                f"  [dim]{s['message_count']} 条消息 · {s.get('model') or '?'} · {s['id']}[/dim]",
                classes="picker-row")
            lv.append(ListItem(row, name=s["id"]))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "picker-filter":
            self._filter = event.value
            self._populate()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter on a row → resume that session."""
        self.dismiss(event.item.name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CoderioTUI(App):
    """coderio's Textual app. Implements StreamHandler (duck-typed)."""

    CSS = """
    Screen { layout: vertical; }
    #history { border: round $accent; height: 1fr; min-height: 10; padding: 0 1; }
    #input-bar { height: auto; dock: bottom; border-top: solid $accent; }
    #input-bar Input { border: none; }
    /* Collapsible thinking blocks */
    Collapsible { border: round $boost 50%; margin: 0 0 0 0; }
    Collapsible > .collapsible__title { color: $text-muted; }
    /* Command menu sits above the input bar; input bar must allow overlay */
    Screen { layers: base above; }
    #input-bar { layer: base; }
    """

    BINDINGS = [
        Binding("ctrl+o", "toggle_thinking", "展开/收起思考", show=True),
        Binding("ctrl+c", "quit", "退出", show=False),
    ]

    name = "textual_tui"

    def __init__(self, on_input=None, show_tool_output: bool = True,
                 banner: str | None = None) -> None:
        super().__init__()
        self._on_input = on_input
        self.show_tool_output = show_tool_output
        self._banner = banner
        # StreamHandler state
        self.buffer = ""
        self.usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._last_collapsible = None  # the most recent thinking Collapsible widget

    # ----------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        from coderio.cli.commands import slash_completions
        yield VerticalScroll(id="history")
        # The command menu (popup, hidden by default; shown when input starts "/").
        # Layered above the input bar so it overlays the history pane.
        yield CommandMenu(slash_completions())
        with Vertical(id="input-bar"):
            yield Input(
                placeholder="输入消息, /help 看命令, Ctrl+O 展开思考",
                id="msg",
            )

    def on_mount(self) -> None:
        self.title = "coderio"
        self.sub_title = "skill-driven coding agent"
        if self._banner:
            self._add_static(Panel(self._banner, title="[bold magenta]coderio[/bold magenta]", border_style="magenta"))
        inp = self.query_one("#msg", Input)
        # Wire the popup command menu to the input.
        self.query_one(CommandMenu).bind_input(inp)
        inp.focus()

    # ----------------------------------------------------- command menu (autocomplete)
    def on_input_changed(self, event: Input.Changed) -> None:
        """Live-filter the command menu as the user types in the main input."""
        if event.input.id != "msg":
            return
        self.query_one(CommandMenu).refresh_for(event.value)

    def on_key(self, event) -> None:
        """Handle command-menu navigation (↑↓/Tab/Enter/Esc) when it's visible."""
        menu = self.query_one(CommandMenu)
        if not menu.visible():
            return
        if event.key == "up":
            menu.move(-1)
            event.prevent_default()
        elif event.key == "down":
            menu.move(1)
            event.prevent_default()
        elif event.key in ("tab", "enter"):
            if menu.accept():
                event.prevent_default()
        elif event.key == "escape":
            menu.hide()
            event.prevent_default()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        if not line:
            return
        event.input.value = ""
        self._add_text(f"▸ you {line}", style="bold cyan")
        if self._on_input:
            import threading
            def _run():
                try:
                    self._on_input(line)
                except SystemExit:
                    self.call_from_thread(self.exit)
                except Exception as e:
                    self.call_from_thread(self._add_text,
                                          f"运行错误: {type(e).__name__}: {e}", style="red")
            threading.Thread(target=_run, daemon=True).start()

    # ----------------------------------------------------- binding: Ctrl+O
    def action_toggle_thinking(self) -> None:
        """Ctrl+O — toggle the thinking block at the current focus.

        Walks up from the focused widget to find the enclosing Collapsible. If
        the focus is on (or inside) a thinking block, toggles THAT one — so the
        user can choose which round to expand by clicking into it first. If no
        thinking block is focused (e.g. focus is on the input bar), falls back
        to the most recent one.
        """
        target = self._focused_collapsible()
        if target is None:
            # Fallback: focus not on a thinking block → toggle the most recent.
            target = self._last_collapsible
        if target is None:
            self._add_text("最近一轮没有思考内容。", style="dim")
            return
        target.collapsed = not target.collapsed

    def _focused_collapsible(self):
        """Find the Collapsible enclosing the currently-focused widget, if any."""
        focused = self.focused
        if focused is None:
            return None
        # Walk up the widget tree looking for a Collapsible ancestor.
        node = focused
        while node is not None:
            if isinstance(node, Collapsible):
                return node
            node = node.parent
        return None

    # ----------------------------------------------------- StreamHandler protocol
    def on_step_start(self) -> None:
        self._flush_round_thinking()

    def on_token(self, text: str) -> None:
        self._flush_round_thinking()
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
        self._add_text(f"⏺ {name}({args_str})", style="green")

    def on_tool_end(self, name: str, result: str) -> None:
        if not self.show_tool_output:
            first = result.splitlines()[0][:60] if result.splitlines() else ""
            self._add_text(f"  → {first}{'…' if len(result) > 60 else ''}", style="dim")
            return
        lines = result.splitlines()
        shown = "\n".join(lines[:3])
        if len(lines) > 3:
            shown += f"\n…({len(lines) - 3} more lines)"
        self._add_text(shown, style="dim")

    def on_truncated(self, stop_reason: str) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._add_static(Panel(
            f"⚠ 输出被截断 (stop_reason: {stop_reason})。",
            title="截断警告", border_style="yellow"))

    def on_harness_warn(self, message: str) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._add_static(Panel(
            f"⚠ {message}\n\n产出可能未经验证，请人工复核。",
            title="⚠ harness 警告", border_style="red"))

    def on_finish(self) -> None:
        self._flush_round_thinking()
        self._flush_buffer()

    def add_usage(self, meta: dict[str, int]) -> None:
        for k in ("input_tokens", "output_tokens"):
            if k in meta:
                self.usage[k] += meta[k]

    # ----------------------------------------------------- thinking fold (true fold/unfold)
    def _flush_round_thinking(self) -> None:
        """Render the current round's thinking as a Collapsible widget (collapsed
        by default). Click the title or Ctrl+O to toggle."""
        if not self._round_thinking.strip():
            return
        secs = time.monotonic() - self._round_think_start if self._round_think_start else 0.0
        chars = len(self._round_thinking)
        title = f"💭 思考 · {secs:.1f}s · {chars} 字 · Ctrl+O / 点击展开"
        body = Static(Text(self._round_thinking))
        col = Collapsible(body, title=title, collapsed=True,
                          collapsed_symbol="▶", expanded_symbol="▼", classes="think-block")
        self._last_collapsible = col
        self._mount_widget(col)
        self._round_thinking = ""
        self._round_think_start = 0.0

    def show_last_thinking(self) -> bool:
        """Expand the most recent thinking (compat with /think command)."""
        if self._last_collapsible is None:
            self._add_text("最近一轮没有思考内容。", style="dim")
            return False
        self._last_collapsible.collapsed = False  # expand
        return True

    # ----------------------------------------------------- helpers: add content to history
    def _flush_buffer(self) -> None:
        if self.buffer.strip():
            self._add_static(Panel(Markdown(self.buffer), border_style="blue", title="coderio"))
        self.buffer = ""

    def _add_text(self, text: str, style: str = "") -> None:
        """Thread-safe: add a styled text line to the history."""
        import threading
        if threading.current_thread() is not threading.main_thread():
            self.call_from_thread(self._add_text_main, text, style)
        else:
            self._add_text_main(text, style)

    def _add_text_main(self, text: str, style: str = "") -> None:
        try:
            history = self.query_one("#history", VerticalScroll)
            history.mount(Static(Text(text, style=style) if style else Text(text)))
            history.scroll_end(animate=False)
        except Exception:
            pass

    def _add_static(self, renderable) -> None:
        """Thread-safe: add a Rich renderable (Panel/Markdown) to history."""
        import threading
        if threading.current_thread() is not threading.main_thread():
            self.call_from_thread(self._add_static_main, renderable)
        else:
            self._add_static_main(renderable)

    def _add_static_main(self, renderable) -> None:
        try:
            history = self.query_one("#history", VerticalScroll)
            history.mount(Static(renderable))
            history.scroll_end(animate=False)
        except Exception:
            pass

    def _mount_widget(self, widget) -> None:
        """Thread-safe: mount an arbitrary widget (e.g. Collapsible) to history."""
        import threading
        if threading.current_thread() is not threading.main_thread():
            self.call_from_thread(self._mount_widget_main, widget)
        else:
            self._mount_widget_main(widget)

    def _mount_widget_main(self, widget) -> None:
        try:
            history = self.query_one("#history", VerticalScroll)
            history.mount(widget)
            history.scroll_end(animate=False)
        except Exception:
            pass


def run_tui(
    provider_override: str | None = None,
    model_override: str | None = None,
    resume: str | None = None,
    continue_last: bool = False,
) -> None:
    """Launch the Textual TUI, wired to coderio's agent runtime.

    Builds the same runtime as the Rich REPL (config, model, tools, skills,
    session), then runs the CoderioTUI app. Each user submission drives
    run_agent in a background thread; the TUI stays interactive (Ctrl+O, scroll).

    ``resume`` / ``continue_last`` load a prior session into the conversation
    history (same semantics as the REPL's --resume/--continue).
    """
    from pathlib import Path
    from coderio.cli.repl import build_runtime, _resolve_resume
    from coderio.config import load_config
    from coderio.config.bootstrap import ensure_user_dirs

    ensure_user_dirs()
    search_from = "."
    creds_path = Path.home() / ".coderio" / "credentials"

    # Resolve a session to resume BEFORE building the runtime (so build_runtime
    # receives it instead of creating a fresh one).
    session = None
    if resume or continue_last:
        cfg = load_config(search_from=search_from)
        try:
            session = _resolve_resume(cfg, resume, continue_last)
        except SystemExit as e:
            # _resolve_resume raises SystemExit on "no previous session"; surface
            # it as a clean banner rather than a crash.
            tui = CoderioTUI()
            tui._banner = f"[red]{e}[/red]"
            tui.run()
            return

    try:
        cfg, store, model, tools, gate, session, active, _rich_stream = build_runtime(
            search_from=search_from, console=None, creds_path=creds_path,
            provider_override=provider_override, model_override=model_override,
            session=session,
        )
    except Exception as e:
        tui = CoderioTUI()
        tui._banner = (
            f"[red]启动失败:[/red] {type(e).__name__}: {e}\n\n"
            "常见原因: API key 未配置 / provider 无效 / 网络不通。\n"
            "运行 coderio config 检查配置, 或设置 ANTHROPIC_API_KEY 环境变量。"
        )
        tui.run()
        return

    banner = (
        f"[bold magenta]coderio[/bold magenta]  [dim]model=[/dim]{cfg.model.default}  "
        f"[dim]perm=[/dim]{gate.mode}"
        "\n[dim]模式:[/dim] [cyan]single-agent (Textual TUI)[/cyan]. "
        "大需求可用 [yellow]/crew[/yellow] 进 6-agent 流水线."
        "\n[dim]输入 /help 看命令, /exit 退出, Ctrl+O 展开/收起思考[/dim]"
    )

    # Mutable runtime holder — /model, /mode, /resume rebuild parts in place.
    rt = {"cfg": cfg, "model": model, "gate": gate, "session": session}

    def on_input(line: str) -> None:
        if line.startswith("/"):
            from coderio.cli.commands import handle_slash, ReplContext
            from coderio.session.store import Session
            from pathlib import Path as _P
            ctx = ReplContext(
                available_skills=store.names(),
                active_skills_names={s.name for s in active.all()},
                permission_mode=rt["gate"].mode,
                model_name=rt["cfg"].model.default,
                provider_id=rt["cfg"].model.provider_id,
                api_key="",
                base_url=rt["cfg"].model.base_url,
                recent_sessions=Session.list_recent(_P(rt["cfg"].session.save_dir).expanduser()),
                usage=tui.usage,
                stream=tui,
            )
            res = handle_slash(line, ctx)
            # /resume with no arg → open the interactive picker instead of printing.
            # push_screen MUST run on the main thread (it touches the Textual
            # event loop); on_input runs in the agent's background thread, so
            # dispatch via call_from_thread — same pattern as _add_text.
            if res.message == "__OPEN_PICKER__":
                summaries = Session.summaries(_P(rt["cfg"].session.save_dir).expanduser())
                def _on_picked(sid):
                    """Picker dismissed: sid is the chosen id, or None if cancelled."""
                    if sid is None:
                        return
                    _load_session(sid)
                tui.call_from_thread(tui.push_screen, SessionPickerScreen(summaries), _on_picked)
                return
            if res.message:
                tui._add_text(res.message)
            if not res.continue_loop:
                tui.call_from_thread(tui.exit)
                return
            # /resume <explicit-id> path: load straight from the result.
            if res.new_session_id:
                _load_session(res.new_session_id)
                return
            if res.reset_runtime:
                from dataclasses import replace as _replace
                from coderio.cli.repl import build_gate
                from coderio.llm import build_chat_model
                c = rt["cfg"]
                if res.new_permission_mode:
                    c = _replace(c, tools=_replace(c.tools, permission_mode=res.new_permission_mode))
                    rt["cfg"] = c
                    rt["gate"] = build_gate(c, console=None)
                cmd_name = line.strip().split(maxsplit=1)[0]
                if cmd_name == "/model":
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) > 1 and parts[1].strip():
                        c = _replace(c, model=_replace(c.model, default=parts[1].strip()))
                        rt["cfg"] = c
                        rt["model"] = build_chat_model(c, creds_path=creds_path)
            return
        from coderio.agent.loop import run_agent
        from coderio.cli.multimodal import build_user_content, extract_images
        imgs = extract_images(line)
        if imgs:
            tui._add_text(f"📎 已附加 {len(imgs)} 张图片: " + ", ".join(p for p, _, _ in imgs), style="dim")
        user_content = build_user_content(line)
        run_agent(
            user_input=user_content, model=rt["model"], tools=tools, gate=rt["gate"],
            skill_store=store, active_skills=active, session=rt["session"], stream=tui,
            max_rounds=rt["cfg"].tools.max_tool_rounds,
            stage_auto_inject=rt["cfg"].skills.stage_auto_inject,
            harness_enabled=rt["cfg"].skills.harness,
        )

    def _load_session(sid: str) -> None:
        """Swap the active session to a loaded one, clear skills, render history.

        Called after the picker picks a session (or /resume <id> is given). The
        old session's jsonl stays on disk; we just point the runtime at the new
        Session object so subsequent turns continue that conversation.
        """
        from coderio.session.store import Session
        from pathlib import Path as _P
        save_dir = _P(rt["cfg"].session.save_dir).expanduser()
        rt["session"] = Session.load_by_id(save_dir, sid)
        active.clear()
        # Render the resumed conversation into the history pane so the user sees
        # context they're continuing, not a blank screen.
        tui._add_text(f"↩ 已恢复会话 {sid}（{len(rt['session'].messages)} 条历史消息）", style="bold green")
        for m in rt["session"].messages:
            if m.role == "user":
                c = m.content
                if isinstance(c, list):
                    c = " ".join(b.get("text", "") for b in c
                                 if isinstance(b, dict) and b.get("type") == "text")
                tui._add_text(f"▸ you {c}", style="bold cyan")
            elif m.role == "assistant":
                tui._add_text(f"  {m.content[:200]}", style="blue")

    tui = CoderioTUI(on_input=on_input, show_tool_output=cfg.cli.show_tool_output, banner=banner)
    tui.run()
