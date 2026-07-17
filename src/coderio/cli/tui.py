"""Textual-based TUI for coderio — foldable thinking, scrollable history.

Implements the StreamHandler protocol (so agent/tools/harness call it
identically to the Rich version). Each round's thinking is rendered as a
Collapsible widget — collapsed by default, expandable via Ctrl+O (toggles the
most recent) or mouse click on the title. This gives true fold/unfold, which
RichLog (append-only) could not.

The agent runs in a background thread; UI updates flow through a thread-safe
render queue drained by a main-thread timer (see _drain_render_queue).
"""
from __future__ import annotations

import time
from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.console import RenderableType
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Collapsible, Input, ListItem, ListView, RichLog, Static


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


class StatusBar(Widget):
    """Live status bar: animated spinner + phase + step + elapsed timer.

    Modeled on Claude Code's bottom indicator: a braille-dot spinner that
    ANIMATES while the agent is working (cycling ⠋⠙⠹⠸⠼⠴⠦⠧ at ~12fps), followed by
    a concrete phase label ("步骤1 · 执行 read_file(1/3)") and a live elapsed
    timer. When idle the spinner stops and shows a static "(就绪)".

    Owns its own background-thread heartbeat (~80ms) that drives BOTH the spinner
    animation and the timer refresh via call_from_thread(refresh, layout=False).
    The phase/tool/step attributes are written by the agent thread (plain
    attribute writes, GIL-safe); render() reads them on the main thread.
    """

    # Claude Code's spinner frames (reverse-engineered, ~80ms each).
    _SPINNER = "⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏".split()
    _BEAT_MS = 0.08  # ~12.5fps — smooth animation, matches Claude Code

    DEFAULT_CSS = """
    StatusBar {
        height: 1; padding: 0 1;
        background: $boost; color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.phase: str = "idle"
        self.phase_start: float = 0.0
        self.tool_name: str = ""
        self.step: int = 0
        self.tool_index: int = 0
        self.tool_total: int = 0
        self._app = None
        self._spin_frame = 0  # cycles through _SPINNER each heartbeat while active

    def on_mount(self) -> None:
        self._app = self.app
        import threading
        self._beat_stop = threading.Event()
        t = threading.Thread(target=self._heartbeat_loop, daemon=True)
        t.start()

    def _heartbeat_loop(self) -> None:
        """Background thread: wake ~12x/sec, advance the spinner frame, and force
        a repaint. This drives the ANIMATION (the spinner visibly cycles) and the
        elapsed timer — both update in lockstep. Runs off the main thread; the
        only main-thread touch is call_from_thread(refresh)."""
        while not self._beat_stop.wait(self._BEAT_MS):
            try:
                if self._app is None or not self._app.is_running:
                    break
                if self.phase != "idle":
                    self._spin_frame = (self._spin_frame + 1) % len(self._SPINNER)
                self._app.call_from_thread(self.refresh, layout=False)
            except Exception:
                break

    def on_unmount(self) -> None:
        self._beat_stop.set()

    def set_phase(self, phase: str, tool_name: str = "", step: int = 0,
                  tool_index: int = 0, tool_total: int = 0) -> None:
        """Update the displayed phase (safe to call from ANY thread).

        Only mutates plain attributes (GIL-safe). Does NOT call refresh() — that
        is widget-state mutation requiring the main thread. The background
        heartbeat picks up the new phase within ~100ms and repaints.
        """
        self.phase = phase
        self.phase_start = time.monotonic() if phase != "idle" else 0.0
        if tool_name:
            self.tool_name = tool_name
        if step:
            self.step = step
        if tool_total:
            self.tool_index = tool_index
            self.tool_total = tool_total

    def render(self) -> RenderableType:
        # Build a phase label that shows WHERE in the task the agent is, so the
        # user can distinguish "still working, step 3" from "frozen". The step
        # number + tool index give concrete progress, not just a vague spinner.
        step_tag = f"步骤{self.step}" if self.step else ""
        if self.phase == "tool" and self.tool_total > 1:
            tool_tag = f"{self.tool_name}({self.tool_index+1}/{self.tool_total})"
        elif self.phase == "tool":
            tool_tag = self.tool_name or "工具"
        else:
            tool_tag = ""
        labels = {
            "idle": "(就绪)",
            "thinking": "思考中",
            "responding": "输出中",
            "tool": f"执行 {tool_tag}",
        }
        if self.phase == "idle":
            return Text(labels["idle"], style="dim")
        elapsed = time.monotonic() - self.phase_start if self.phase_start else 0.0
        label = labels.get(self.phase, self.phase)
        # The spinner ANIMATES: each heartbeat advances _spin_frame.
        spin = self._SPINNER[self._spin_frame]
        # Build the Text by APPENDING separate spans. The braille spinner and the
        # CJK text are in different segments so Textual computes cell widths
        # independently (mixing them in one f-string miscalculates width and can
        # eat the adjacent CJK char). Separate spans + overflow='ellipsis' + no_wrap.
        parts = []
        if step_tag:
            parts.append(step_tag)
        parts.append(label)
        parts.append(f"{elapsed:.1f}s")
        body = " · ".join(parts)
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(spin + " ", style="bold cyan")
        t.append(body)
        return t


class OnboardingScreen(ModalScreen[dict | None]):
    """TUI-based onboarding wizard (multi-step ModalScreen).

    Uses ListView (↑↓ + Enter) for provider/model selection, Input with
    password masking for API key. Dismisses with a result dict on success,
    or None if cancelled.
    """

    CSS = """
    OnboardingScreen { align: center middle; }
    #onboard-box {
        width: 70%; height: auto; max-height: 80%; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    #onboard-title { text-align: center; color: $accent; margin-bottom: 1; }
    #onboard-status { color: $text-muted; margin-top: 1; }
    #onboard-input { margin-top: 1; border: round $accent; }
    #onboard-input:focus { border: round $accent; }
    #onboard-list { height: auto; max-height: 16; margin-top: 1; }
    .onboard-group-header { color: $accent; margin-top: 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        from coderio.cli.providers import PROVIDERS
        self._providers = PROVIDERS
        self._step = "provider"
        self._chosen_provider = None
        self._chosen_model = ""
        self._base_url = ""
        self._api_key = ""
        self._provider_items: list = []  # parallel to ListView items

    def compose(self) -> ComposeResult:
        with Vertical(id="onboard-box"):
            yield Static("[bold]coderio 配置向导[/bold]", id="onboard-title")
            yield Static("", id="onboard-hint")
            yield ListView(id="onboard-list")
            yield Input(id="onboard-input")
            yield Static("", id="onboard-status")

    def on_mount(self) -> None:
        self._show_provider_step()

    # --- step transitions ---

    def _show_provider_step(self) -> None:
        """Step 1: provider selection via ListView (↑↓ + Enter)."""
        self._step = "provider"
        self.query_one("#onboard-hint").update(
            "选择你的模型 provider（↑↓ 选择 · Enter 确认 · Esc 取消）")
        self.query_one("#onboard-input", Input).visible = False
        lv = self.query_one("#onboard-list", ListView)
        lv.display = True
        lv.clear()
        self._provider_items = []
        plan = [p for p in self._providers if p.plan]
        cn = [p for p in self._providers if not p.plan and p.id in ("bigmodel_api", "stepfun_api")]
        intl = [p for p in self._providers if p.id in ("openai", "anthropic")]
        local = [p for p in self._providers if p.id == "ollama"]
        custom = [p for p in self._providers if p.id == "openai_custom"]

        def _add(title, ps):
            if ps:
                lv.append(ListItem(Static(f"[bold]{title}[/bold]")))
                self._provider_items.append(None)  # header, not selectable
                for p in ps:
                    ms = f"  ({' / '.join(p.models[:2])}{'...' if len(p.models) > 2 else ''})" if p.models else ""
                    lv.append(ListItem(Static(f"  {p.label}{ms}")))
                    self._provider_items.append(p)

        _add("Coding Plan（订阅制）", plan)
        _add("国内 API Key 直连", cn)
        _add("国际", intl)
        _add("本地模型", local)
        _add("自定义", custom)
        try:
            lv.index = 0
        except Exception:
            pass
        lv.focus()

    def _show_model_step(self) -> None:
        """Step 2: model selection via ListView."""
        p = self._chosen_provider
        if not p.models:
            # No preset models (ollama/custom) — text input
            self._step = "model_input"
            self.query_one("#onboard-list", ListView).display = False
            self.query_one("#onboard-hint").update("输入模型名（例如 qwen2.5-coder / gpt-4o）：")
            inp = self.query_one("#onboard-input", Input)
            inp.visible = True
            inp.password = False
            inp.value = ""
            inp.focus()
            return
        self._step = "model"
        self.query_one("#onboard-input", Input).visible = False
        lv = self.query_one("#onboard-list", ListView)
        lv.display = True
        lv.clear()
        self._model_items = list(p.models)
        for m in p.models:
            star = " ★" if m == p.default_model else ""
            lv.append(ListItem(Static(f"  {m}{star}")))
        try:
            lv.index = 0
        except Exception:
            pass
        self.query_one("#onboard-hint").update(
            f"选择模型（{p.label}）— ★ = 推荐（↑↓ · Enter）")
        lv.focus()

    def _show_base_url_step(self) -> None:
        """Step 2b: base_url input (openai_custom only)."""
        self._step = "base_url"
        self.query_one("#onboard-list", ListView).display = False
        self.query_one("#onboard-hint").update(
            "输入 base_url（例如 https://api.example.com/v1）：")
        inp = self.query_one("#onboard-input", Input)
        inp.visible = True
        inp.password = False
        inp.value = ""
        inp.focus()

    def _show_key_step(self) -> None:
        """Step 3: API key input with password masking (dots)."""
        p = self._chosen_provider
        if p.id == "ollama":
            self._api_key = "ollama"
            self._finish()
            return
        self._step = "key"
        self.query_one("#onboard-list", ListView).display = False
        self.query_one("#onboard-hint").update(
            f"输入 API key（{p.api_key_hint}）：")
        inp = self.query_one("#onboard-input", Input)
        inp.visible = True
        inp.password = True  # masked — shows dots
        inp.value = ""
        inp.focus()

    def _start_verification(self) -> None:
        """Step 4: verify the key via a minimal API request."""
        self._step = "verifying"
        self.query_one("#onboard-input", Input).visible = False
        self.query_one("#onboard-hint").update("[bold cyan]正在验证连接...[/bold cyan]")
        def _verify():
            from coderio.cli.onboarding import _verify_key
            ok, msg = _verify_key(self._chosen_provider, self._api_key,
                                  self._chosen_model, self._base_url)
            self.app.call_from_thread(self._on_verify_result, ok, msg)
        import threading
        threading.Thread(target=_verify, daemon=True).start()

    def _on_verify_result(self, ok: bool, msg: str) -> None:
        if ok:
            self.query_one("#onboard-status").update(f"[green]✅ {msg}[/green]")
            self._finish()
        else:
            self.query_one("#onboard-status").update(f"[red]❌ {msg}[/red]")
            self._step = "key"
            self.query_one("#onboard-hint").update("验证失败，重新输入 API key（或 Esc 取消）：")
            inp = self.query_one("#onboard-input", Input)
            inp.password = True
            inp.visible = True
            inp.value = ""
            inp.focus()

    def _finish(self) -> None:
        """Save credentials + config, then dismiss with result."""
        from pathlib import Path
        from coderio.cli.credentials import write_credentials
        from coderio.cli.onboarding import _save_to_config, OnboardingResult
        creds_path = Path.home() / ".coderio" / "credentials"
        write_credentials({self._chosen_provider.id: self._api_key}, creds_path)
        result = OnboardingResult(
            provider_id=self._chosen_provider.id,
            model=self._chosen_model,
            base_url=self._base_url,
            kind=self._chosen_provider.kind,
            api_key=self._api_key,
        )
        config_path = creds_path.parent / "config.toml"
        _save_to_config(result, config_path)
        self.query_one("#onboard-status").update("[green]配置完成！[/green]")
        self.set_timer(0.8, lambda: self.dismiss({
            "provider_id": result.provider_id,
            "model": result.model,
        }))

    # --- event handlers ---

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """ListView item selected (Enter pressed)."""
        lv = self.query_one("#onboard-list", ListView)
        idx = lv.index
        if idx is None:
            return
        if self._step == "provider":
            # Skip header rows (None entries) — find the actual provider at/before idx
            if idx < len(self._provider_items):
                p = self._provider_items[idx]
                if p is None:
                    return  # header row, ignore
                self._chosen_provider = p
                self.query_one("#onboard-status").update("")
                if p.id == "openai_custom":
                    self._show_base_url_step()
                else:
                    self._base_url = p.base_url
                    self._show_model_step()
        elif self._step == "model":
            if idx < len(self._model_items):
                self._chosen_model = self._model_items[idx]
                self.query_one("#onboard-status").update("")
                self._show_key_step()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "onboard-input":
            return
        val = event.value.strip()
        if self._step == "base_url":
            if val:
                self._base_url = val
                self._show_model_step()
            else:
                self.query_one("#onboard-status").update("[red]请输入 base_url[/red]")
        elif self._step == "model_input":
            if val:
                self._chosen_model = val
                self.query_one("#onboard-status").update("")
                self._show_key_step()
            else:
                self.query_one("#onboard-status").update("[red]请输入模型名[/red]")
        elif self._step == "key":
            if val:
                self._api_key = val
                self.query_one("#onboard-status").update("")
                self._start_verification()
            else:
                self.query_one("#onboard-status").update("[red]请输入 API key[/red]")

    def action_cancel(self) -> None:
        self.dismiss(None)


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
    #history Static { height: auto; }
    #input-bar { height: auto; dock: bottom; border-top: solid $accent; }
    #input-bar Input { border: none; }
    /* Collapsible thinking blocks */
    Collapsible { border: round $boost 50%; margin: 0 0 0 0; }
    Collapsible > .collapsible__title { color: $text-muted; }
    /* NOTE: do NOT define `Screen { layers }` here — it changes how Textual
       renders the scrollable region (bottom rows of scrolled content stop
       rendering). The CommandMenu popup uses display:none/block for show/hide
       and dock:bottom for positioning, so it does NOT need layers. */
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
        # Live thinking: the Static body of the IN-PROGRESS thinking block. While
        # non-None, on_thinking appends to it in real time (the user sees thinking
        # stream live, not dumped all at once when it ends). Set to None when a
        # round's thinking is flushed/folded.
        self._live_think_body: Static | None = None
        self._live_think_chars = 0  # chars shown so far (to append only the delta)
        # Live output: a RichLog widget for streaming the answer as it arrives.
        # RichLog is append-only — each token chunk is written once, avoiding the
        # full-widget layout recompute that Static.update(Text(full_buffer)) would
        # trigger on every batch. On finish, the RichLog is replaced by the final
        # Markdown Panel.
        self._live_output: Static | None = None
        self._live_output_chars = 0
        self._live_output_last_flush: float = 0.0  # throttle: only flush >=80ms apart
        # Cached StatusBar reference: query_one() is a main-thread DOM query, but
        # _set_phase runs on the agent's BACKGROUND thread. Cache the widget once
        # (on_mount, main thread) and read the plain attribute thereafter
        # (GIL-safe from any thread).
        self._status_bar: StatusBar | None = None
        # RENDER QUEUE: the agent's background thread pushes render instructions
        # here (thread-safe deque append/popleft). A main-thread set_interval
        # timer drains the queue and executes the instructions on the main thread.
        # This avoids call_from_thread, whose callbacks are not reliably delivered
        # in a real terminal. The deque + timer pattern matches the StatusBar
        # heartbeat approach.
        import collections
        self._render_q: collections.deque = collections.deque()
        self._live_out_widget: RichLog | None = None  # streaming output RichLog (main thread)
        self._live_rendered_len: int = 0  # chars already written to the RichLog

    # ----------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        from coderio.cli.commands import slash_completions
        yield VerticalScroll(id="history")
        # The command menu (popup, hidden by default; shown when input starts "/").
        # Layered above the input bar so it overlays the history pane.
        yield CommandMenu(slash_completions())
        # input-bar holds BOTH the StatusBar and the Input — StatusBar sits above
        # the input inside the same docked container, so they stack instead of
        # overlapping at the dock:bottom edge.
        with Vertical(id="input-bar"):
            yield StatusBar()
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
        # Cache the StatusBar reference NOW (main thread) — _set_phase runs on
        # the agent's background thread where query_one() can't run.
        self._status_bar = self.query_one(StatusBar)
        inp.focus()
        # Render-queue drain timer: runs on the MAIN thread (set_interval), pops
        # all queued render instructions and executes them. This is the only path
        # from background-thread data to main-thread widgets.
        self.set_interval(0.06, self._drain_render_queue)

    def _drain_render_queue(self) -> None:
        """MAIN THREAD (set_interval): drain the render queue and execute all
        pending instructions, then scroll to bottom.

        Two scroll strategies depending on what was rendered:
          - STREAMING updates (text/think_update): fire ONE immediate scroll_end
            via call_after_refresh. These fire every ~60ms during output, so a
            single deferred scroll avoids timer pile-up.
          - FINAL render (finalize/static/panel/think_fold): these mount new widgets
            whose height needs multiple layout passes to settle, so use the
            multi-stage delayed scroll (0.15/0.3/0.5s).
        """
        did_streaming = False
        did_final = False
        while self._render_q:
            action, *args = self._render_q.popleft()
            try:
                if action == "text":
                    self._render_live_output(args[0])
                    did_streaming = True
                elif action == "finalize":
                    self._render_finalize(*args)
                    did_final = True
                elif action == "think_start":
                    self._render_think_start(args[0])
                    did_streaming = True
                elif action == "think_update":
                    self._render_think_update(args[0])
                    did_streaming = True
                elif action == "think_fold":
                    self._render_think_fold(*args)
                    did_final = True
                elif action == "static":
                    self._add_text_main(args[0], args[1] if len(args) > 1 else "")
                    did_final = True
                elif action == "panel":
                    self._add_static_main(args[0])
                    did_final = True
                elif action == "clear_live":
                    if self._live_out_widget is not None:
                        try:
                            self._live_out_widget.remove()
                        except Exception:
                            pass
                        self._live_out_widget = None
                        self._live_rendered_len = 0
                elif action == "exit":
                    self.exit()
            except Exception:
                pass
        # Scroll strategy: streaming = single deferred scroll; final = multi-stage
        # delayed scroll (large Panels need multiple layout passes to settle).
        if did_final:
            try:
                self.set_timer(0.15, self._scroll_history_end)
                self.set_timer(0.3, self._scroll_history_end)
                self.set_timer(0.5, self._scroll_history_end)
            except Exception:
                pass
        elif did_streaming:
            try:
                # Single deferred scroll — runs after this tick's layout settles,
                # without piling up timers across 60ms cycles.
                self.call_after_refresh(self._scroll_history_end)
            except Exception:
                pass

    # ----------------------------------------------------- render methods (MAIN THREAD, called by _drain_render_queue)
    def _scroll_history_end(self) -> None:
        """Scroll the history pane to the bottom."""
        try:
            h = self.query_one("#history", VerticalScroll)
            h.scroll_end(animate=False)
        except Exception:
            pass

    def _render_live_output(self, full_text: str) -> None:
        """MAIN THREAD: append the NEW part of the streaming text to a RichLog.

        RichLog is append-only: each call writes just the delta (the new chars
        since the last write), NOT the full buffer re-rendered. This avoids the
        layout recompute that Static.update(Text(full_buffer)) would trigger on
        every batch (re-laying-out the entire history)."""
        if self._live_out_widget is None:
            self._live_out_widget = RichLog(wrap=True, markup=False, auto_scroll=True)
            self._mount_widget_main(self._live_out_widget)
            self._live_rendered_len = 0
        # Write only the delta (new chars since last write).
        delta = full_text[self._live_rendered_len:]
        if delta:
            self._live_rendered_len = len(full_text)
            self._live_out_widget.write(delta)

    def _render_think_start(self, full_text: str) -> None:
        """MAIN THREAD: mount the live (expanded) thinking block."""
        self._live_think_body = Static(Text(full_text))
        col = Collapsible(self._live_think_body, title="💭 思考中…",
                          collapsed=False, collapsed_symbol="▶", expanded_symbol="▼",
                          classes="think-block")
        self._last_collapsible = col
        self._mount_widget_main(col)

    def _render_think_update(self, full_text: str) -> None:
        """MAIN THREAD: update the live thinking body."""
        if self._live_think_body is not None:
            self._live_think_body.update(Text(full_text))

    def _render_think_fold(self, text: str, secs: float, had_live: bool) -> None:
        """MAIN THREAD: fold the thinking Collapsible."""
        chars = len(text)
        title = f"💭 思考 · {secs:.1f}s · {chars} 字 · Ctrl+O / 点击展开"
        if had_live and self._last_collapsible is not None:
            self._last_collapsible.title = title
            self._last_collapsible.collapsed = True
        else:
            body = Static(Text(text))
            col = Collapsible(body, title=title, collapsed=True,
                              collapsed_symbol="▶", expanded_symbol="▼", classes="think-block")
            self._last_collapsible = col
            self._mount_widget_main(col)

    def _render_finalize(self, buf: str, think_text: str, secs: float, had_live: bool) -> None:
        """MAIN THREAD: fold thinking + replace live output with final Markdown Panel.

        The final answer is rendered as Static(Panel(Markdown(buf))) (see
        _mount_final_panel). scroll_end lands on the real content bottom once the
        layout settles (note the layers caveat in the CSS).
        """
        if think_text.strip():
            self._render_think_fold(think_text, secs, had_live)
        if buf.strip():
            # Remove the streaming RichLog and replace with the final Markdown Panel.
            if self._live_out_widget is not None:
                try:
                    self._live_out_widget.remove()
                except Exception:
                    pass
                self._live_out_widget = None
                self._live_rendered_len = 0
            self.call_after_refresh(self._mount_final_panel, buf)

    def _mount_final_panel(self, buf: str) -> None:
        """MAIN THREAD: mount the final Markdown Panel and scroll to the bottom.

        Mounts Static(Panel(Markdown(buf))) and schedules a multi-stage delayed
        scroll (the Panel's height needs a few layout passes to settle).
        """
        history = self.query_one("#history")
        widget = Static(Panel(Markdown(buf), border_style="blue", title="coderio"))
        history.mount(widget)
        self.set_timer(0.15, self._scroll_history_end)
        self.set_timer(0.3, self._scroll_history_end)
        self.set_timer(0.5, self._scroll_history_end)

    # ----------------------------------------------------- status bar (phase routing)
    def _set_phase(self, phase: str, tool_name: str = "", step: int = 0,
                   tool_index: int = 0, tool_total: int = 0) -> None:
        """Forward a phase change to the StatusBar widget.

        Called from the agent's BACKGROUND thread (StreamHandler callbacks). Do
        NOT use query_one here — it's a main-thread DOM query. Use the cached
        reference instead (plain attribute read, GIL-safe). StatusBar.set_phase
        also only writes plain attributes.
        """
        bar = self._status_bar
        if bar is None:
            return  # not mounted yet
        bar.set_phase(phase, tool_name, step=step, tool_index=tool_index, tool_total=tool_total)

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
            # Use a Textual WORKER (thread=True), not a raw threading.Thread. A
            # worker is managed by Textual, so the main event loop stays alive and
            # keeps draining pending UI updates while the worker runs AND after it
            # completes — a raw daemon thread would not.
            def _run():
                try:
                    self._on_input(line)
                except SystemExit:
                    self._render_q.append(("exit",))
                except Exception as e:
                    # Reset the streaming state + status bar so the TUI doesn't
                    # get stuck in 'thinking' phase when the agent errors out
                    # (e.g. API auth failure, network error). on_finish is never
                    # called when run_agent raises, so we must clean up here.
                    self._round_thinking = ""
                    self._round_think_start = 0.0
                    self._live_think_body = None
                    self._live_think_chars = 0
                    self.buffer = ""
                    self._live_output_last_flush = 0.0
                    if self._status_bar:
                        self._status_bar.set_phase("idle")
                    self._render_q.append(("static", f"运行错误: {type(e).__name__}: {e}", "red"))
            self.run_worker(_run, thread=True, exclusive=True,
                            name="agent_turn", exit_on_error=False)

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
    # ALL callbacks run on the agent's BACKGROUND thread. They ONLY push render
    # instructions to self._render_q (a thread-safe deque). The main-thread timer
    # _drain_render_queue (set_interval 60ms) pops and executes them — no
    # call_from_thread here.
    def on_step_start(self, step: int = 1) -> None:
        self._flush_round_thinking()
        self._set_phase("thinking", step=step)

    def on_token(self, text: str) -> None:
        self._flush_round_thinking()
        bar = self._status_bar
        if bar is None or bar.phase != "responding":
            self._set_phase("responding")
        # Accumulate in buffer (agent thread). Push a "text" render instruction
        # with the FULL buffer so the main thread can update the live widget.
        # Throttle: only push at most once per ~60ms to avoid flooding the queue.
        self.buffer += text
        now = time.monotonic()
        if self._live_output_last_flush == 0.0 or (now - self._live_output_last_flush) >= 0.06:
            self._live_output_last_flush = now
            self._render_q.append(("text", self.buffer))

    def on_thinking(self, text: str) -> None:
        if not self._round_thinking:
            self._round_think_start = time.monotonic()
        self._round_thinking += text
        now = time.monotonic()
        if self._live_think_body is None:
            # First chunk: push a think_start
            self._live_think_chars = len(self._round_thinking)
            self._render_q.append(("think_start", self._round_thinking))
            self._live_output_last_flush = now  # reuse throttle timer for thinking
        else:
            delta_len = len(self._round_thinking) - self._live_think_chars
            if delta_len > 0 and (now - self._live_output_last_flush) >= 0.06:
                self._live_think_chars = len(self._round_thinking)
                self._live_output_last_flush = now
                self._render_q.append(("think_update", self._round_thinking))

    def on_tool_start(self, name: str, args: dict[str, Any], step: int = 1,
                      tool_index: int = 0, tool_total: int = 0) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._set_phase("tool", tool_name=name, step=step,
                        tool_index=tool_index, tool_total=tool_total)
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        if len(args_str) > 100:
            args_str = args_str[:100] + "…"
        self._render_q.append(("static", f"⏺ {name}({args_str})", "green"))

    def on_tool_end(self, name: str, result: str) -> None:
        self._set_phase("thinking")
        if not self.show_tool_output:
            first = result.splitlines()[0][:60] if result.splitlines() else ""
            self._render_q.append(("static", f"  → {first}{'…' if len(result) > 60 else ''}", "dim"))
            return
        lines = result.splitlines()
        shown = "\n".join(lines[:3])
        if len(lines) > 3:
            shown += f"\n…({len(lines) - 3} more lines)"
        self._render_q.append(("static", shown, "dim"))

    def on_truncated(self, stop_reason: str) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._render_q.append(("panel", Panel(
            f"⚠ 输出被截断 (stop_reason: {stop_reason})。",
            title="截断警告", border_style="yellow")))

    def on_harness_warn(self, message: str) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._render_q.append(("panel", Panel(
            f"⚠ {message}\n\n产出可能未经验证，请人工复核。",
            title="⚠ harness 警告", border_style="red")))

    def on_finish(self) -> None:
        # Capture everything remaining and push ONE finalize instruction.
        # The main-thread drain will fold thinking + mount the final Markdown Panel.
        think_text = self._round_thinking
        secs = time.monotonic() - self._round_think_start if self._round_think_start else 0.0
        had_live = self._live_think_body is not None
        buf = self.buffer
        # Reset accumulated state.
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._live_think_body = None
        self._live_think_chars = 0
        self.buffer = ""
        self._live_output_last_flush = 0.0
        self._render_q.append(("finalize", buf, think_text, secs, had_live))
        if self._status_bar:
            self._status_bar.set_phase("idle")

    def add_usage(self, meta: dict[str, int]) -> None:
        for k in ("input_tokens", "output_tokens"):
            if k in meta:
                self.usage[k] += meta[k]

    # ----------------------------------------------------- thinking fold (true fold/unfold)
    def _flush_round_thinking(self) -> None:
        """Push a think_fold instruction to the render queue (agent thread)."""
        if not self._round_thinking.strip():
            return
        text = self._round_thinking
        secs = time.monotonic() - self._round_think_start if self._round_think_start else 0.0
        had_live = self._live_think_body is not None
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._live_think_body = None
        self._live_think_chars = 0
        self._render_q.append(("think_fold", text, secs, had_live))

    def show_last_thinking(self) -> bool:
        """Expand the most recent thinking (compat with /think command)."""
        if self._last_collapsible is None:
            self._add_text("最近一轮没有思考内容。", style="dim")
            return False
        self._last_collapsible.collapsed = False  # expand
        return True

    # ----------------------------------------------------- helpers: add content to history
    def _flush_buffer(self) -> None:
        """Push the accumulated output to the render queue, replacing the live
        streaming RichLog with a final Markdown Panel (mid-turn tool calls)."""
        # Remove the live streaming widget first (if present).
        self._render_q.append(("clear_live",))
        if self.buffer.strip():
            self._render_q.append(("panel", Panel(Markdown(self.buffer), border_style="blue", title="coderio")))
        self.buffer = ""

    def _add_text(self, text: str, style: str = "") -> None:
        """Push a text line to the render queue."""
        self._render_q.append(("static", text, style))

    def _add_text_main(self, text: str, style: str = "") -> None:
        try:
            history = self.query_one("#history", VerticalScroll)
            history.mount(Static(Text(text, style=style) if style else Text(text)))
            history.call_after_refresh(history.scroll_end, animate=False)
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
            widget = Static(renderable)
            history.mount(widget)
            # Scroll to end AFTER the mount + layout settle. Calling scroll_end
            # synchronously right after mount races the layout (the new widget's
            # height isn't computed yet), so scroll_end lands above the true
            # bottom. call_after_refresh defers the scroll until the layout pass
            # finishes.
            history.call_after_refresh(history.scroll_end, animate=False)
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
            history.call_after_refresh(history.scroll_end, animate=False)
        except Exception:
            pass


class _OnboardingApp(App):
    """Minimal app that just shows the OnboardingScreen and exits.

    Runs before the main CoderioTUI so the terminal is in Textual mode during
    onboarding (masked key input, proper rendering) rather than raw console.
    """

    CSS = """
    Screen { background: $surface; }
    """

    def on_mount(self) -> None:
        def _on_done(result):
            self._result = result
            self.exit()
        self._result = None
        self.push_screen(OnboardingScreen(), _on_done)


def _run_onboarding_tui() -> dict | None:
    """Run the TUI onboarding wizard. Returns the result dict or None if cancelled."""
    app = _OnboardingApp()
    app.run()
    return getattr(app, "_result", None)


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
    from coderio.cli.repl import build_runtime, _resolve_resume, _needs_onboarding
    from coderio.config import load_config
    from coderio.config.bootstrap import ensure_user_dirs

    ensure_user_dirs()
    search_from = "."
    creds_path = Path.home() / ".coderio" / "credentials"

    # Run TUI-based onboarding if needed (replaces the old console wizard).
    if _needs_onboarding(creds_path):
        result = _run_onboarding_tui()
        if result is None:
            return  # user cancelled

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
