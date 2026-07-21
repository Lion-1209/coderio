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

# Human-readable labels for AgentState task-phase values (shown in StatusBar).
# Mirrors _PHASE_LABELS in cli/stream.py — duplicated rather than imported to
# keep tui.py decoupled from the Rich CLI stream module.
_TASK_PHASE_LABELS: dict[str, str] = {
    "explore": "探索",
    "plan": "规划",
    "implement": "实现",
    "verify": "验证",
    "complete": "完成",
}


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
        """Move the selection by delta (+1 down, -1 up); wraps around.

        After moving, proactively scrolls so the highlight stays at least one
        row inside the visible viewport (not flush against the edge). Textual's
        default scroll_to_widget only reacts once the highlighted item is FULLY
        off-screen, which makes the menu feel unresponsive — the user presses
        down several times before the view catches up. We compute the target
        scroll_y directly to trigger earlier.
        """
        if not self.visible():
            return
        lv = self.query_one("#cmd-list", ListView)
        if not lv.children:
            return
        n = len(lv.children)
        idx = lv.index or 0
        new_idx = (idx + delta) % n
        lv.index = new_idx

        # Proactive scroll: keep a 1-row margin so the highlight never sits on
        # the very bottom/top edge of the viewport.
        try:
            # visible viewport height (in rows)
            vp_h = lv.size.height
            if vp_h <= 0:
                vp_h = 1
            row = new_idx
            if delta > 0:
                # Target: highlight sits at most vp_h-2 rows below the viewport
                # top (leaving 1 row of margin below it). Clamped to >= 0.
                target = max(0, row - (vp_h - 2))
            else:
                # Target: highlight sits at least 1 row below the viewport top
                # (leaving 1 row of margin above it). Clamped to <= max_scroll_y.
                target = max(0, min(lv.max_scroll_y, row - 1))
            # Clamp to valid range and apply if it actually improves the view.
            target = max(0, min(lv.max_scroll_y, target))
            if target != lv.scroll_y:
                lv.scroll_y = target
        except Exception:
            # Fallback: at least ensure the item is visible.
            try:
                lv.scroll_to_widget(lv.children[new_idx], animate=False)
            except Exception:
                pass

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
        # Task-level phase (explore/plan/implement/verify/complete), orthogonal
        # to self.phase (which tracks model micro-activity: thinking/responding/tool).
        # Derived from harness ground truth via AgentStateTracker. Empty = unknown.
        self.task_phase: str = ""
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

    def set_task_phase(self, task_phase: str) -> None:
        """Update the task-level phase tag (explore/plan/implement/verify/...).

        Safe from any thread (plain attribute write, GIL-safe). Repainted by the
        heartbeat. Pass "" to clear.
        """
        self.task_phase = task_phase

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
        # Task-level phase tag (e.g. [实现]) derived from harness ground truth.
        # Shown before the micro-activity label so the user sees both axes:
        # "步骤3 · [实现] 思考中 · 12.4s".
        if self.task_phase:
            task_label = _TASK_PHASE_LABELS.get(self.task_phase, self.task_phase)
            parts.append(f"[{task_label}]")
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
        width: 72%; height: auto; max-height: 82%; border: round $accent;
        background: $surface; padding: 1 2;
    }
    #onboard-title { text-align: center; margin-bottom: 1; }
    #onboard-hint { color: $text-muted; }
    #onboard-status { color: $text-muted; margin-top: 1; }
    #onboard-input { margin-top: 1; border: round $accent; }
    #onboard-input:focus { border: round $accent; }
    #onboard-list { height: auto; max-height: 16; margin-top: 1; }
    OnboardingScreen ListItem { padding: 0 1; }
    OnboardingScreen ListItem > Widget :hover { background: $boost; }
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
        self._profile_name = ""
        self._provider_items: list = []  # parallel to ListView items
        # Which providers already have a saved key? (read once at open time)
        from coderio.cli.credentials import read_credentials
        try:
            self._configured = set(read_credentials().keys())
        except Exception:
            self._configured = set()
        # Existing profiles (for the new/edit action choice). Empty on first run.
        self._existing_profiles = self._load_existing_profiles()
        # When editing, the Profile being modified (None = creating new).
        self._editing_profile = None

    def compose(self) -> ComposeResult:
        with Vertical(id="onboard-box"):
            yield Static("[bold magenta]coderio 配置向导[/bold magenta]",
                         id="onboard-title")
            yield Static("", id="onboard-hint")
            yield ListView(id="onboard-list")
            yield Input(id="onboard-input")
            yield Static("", id="onboard-status")

    @staticmethod
    def _load_existing_profiles() -> list:
        """Read [[profiles]] from config.toml so /setup can offer to edit them.

        Returns an empty list on first run or any read error — the wizard then
        skips the new/edit choice and goes straight to provider selection.
        """
        from pathlib import Path
        try:
            from coderio.config import load_config
            cfg = load_config(search_from=".")
            return list(cfg.profiles or [])
        except Exception:
            return []

    def on_mount(self) -> None:
        if self._existing_profiles:
            self._show_action_step()
        else:
            self._show_provider_step()

    def _show_action_step(self) -> None:
        """Step 0 (only when profiles exist): choose 新建 or 修改 an existing one.

        One row to create a new profile, then one row per existing profile (with
        provider/model as a dim subtitle) to edit it. This is the /setup entry
        point when the user already has at least one configured profile.
        """
        self._step = "action"
        self.query_one("#onboard-input", Input).visible = False
        self.query_one("#onboard-hint").update(
            "选择操作（↑↓ · Enter 确认 · Esc 取消）")
        lv = self.query_one("#onboard-list", ListView)
        lv.display = True
        lv.clear()
        self._action_items: list = []  # None=new, else the Profile to edit
        lv.append(ListItem(Static("  [green]➕[/green]  新建配置")))
        self._action_items.append(None)
        for p in self._existing_profiles:
            lv.append(ListItem(Static(
                f"  [yellow]✎[/yellow]  修改  {p.name}  "
                f"[dim]{p.provider_id} · {p.model}[/dim]")))
            self._action_items.append(p)
        try:
            lv.index = 0
        except Exception:
            pass
        lv.focus()

    def _start_edit(self, profile) -> None:
        """Pre-fill the wizard with an existing profile's values.

        Resolves the provider from the registry (so model/key steps behave the
        same as a new config), carries over the profile's base_url for custom
        providers, and sets the profile name so the final name step shows it.
        Then jumps to model selection — the most common edit is changing the
        model or re-entering the key, not switching providers.
        """
        from coderio.cli.providers import get_provider
        info = get_provider(profile.provider_id)
        if info is not None:
            self._chosen_provider = info
        else:
            # Provider no longer in the registry — can't offer model presets.
            # Fall back to text-input model step with the profile's current model.
            self._chosen_provider = type("_P", (), {
                "id": profile.provider_id, "label": profile.name,
                "kind": profile.kind, "base_url": profile.base_url,
                "models": (), "default_model": "", "api_key_hint": "",
                "plan": False,
            })()
        self._base_url = profile.base_url or (info.base_url if info else "")
        self._chosen_model = profile.model
        self._profile_name = profile.name
        self._editing_profile = profile
        self._show_model_step()

    # --- step transitions ---

    def _show_provider_step(self) -> None:
        """Step 1: provider selection via ListView (↑↓ + Enter).

        All items are real providers (no header rows) — every selectable item
        maps to a ProviderInfo, so ListView navigation never lands on a dead row.
        Group context is shown via a dim prefix on each label; providers that
        already have a saved key are marked ✓."""
        self._step = "provider"
        configured_count = len(self._configured)
        hint = (
            f"选择模型 provider（↑↓ 选择 · Enter 确认 · Esc 取消）"
            + (f"   [green]{configured_count} 个已配置[/green]"
               if configured_count else "")
        )
        self.query_one("#onboard-hint").update(hint)
        self.query_one("#onboard-input", Input).visible = False
        lv = self.query_one("#onboard-list", ListView)
        lv.display = True
        lv.clear()
        self._provider_items = []

        # Build a flat list with group labels. Each item is a real provider.
        groups = [
            ("订阅制", [p for p in self._providers if p.plan]),
            ("国内直连", [p for p in self._providers if not p.plan and p.id in ("bigmodel_api", "stepfun_api")]),
            ("国际", [p for p in self._providers if p.id in ("openai", "anthropic")]),
            ("本地", [p for p in self._providers if p.id == "ollama"]),
            ("自定义", [p for p in self._providers if p.id == "openai_custom"]),
        ]
        for group_name, providers in groups:
            for p in providers:
                ms = f" ({' / '.join(p.models[:2])}{'...' if len(p.models) > 2 else ''})" if p.models else ""
                check = "  [green]✓[/green]" if p.id in self._configured else "   "
                lv.append(ListItem(Static(
                    f"  [dim]{group_name}[/dim]  {p.label}{ms}{check}")))
                self._provider_items.append(p)
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
        # When editing, highlight the profile's current model (if it's in the
        # preset list) so the user can just press Enter to keep it.
        start_idx = 0
        if self._editing_profile and self._editing_profile.model in self._model_items:
            start_idx = self._model_items.index(self._editing_profile.model)
        try:
            lv.index = start_idx
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
            self._show_name_step()
            return
        self._step = "key"
        self.query_one("#onboard-list", ListView).display = False
        if self._editing_profile:
            # Editing: key is optional — empty input keeps the existing key.
            from coderio.cli.credentials import get_key
            existing = get_key(p.id) or ""
            self._api_key = existing  # carry over until the user types a new one
            self.query_one("#onboard-hint").update(
                f"输入新 API key（留空保留现有 key）— {p.api_key_hint}：")
        else:
            self.query_one("#onboard-hint").update(
                f"输入 API key（{p.api_key_hint}）：")
        inp = self.query_one("#onboard-input", Input)
        inp.visible = True
        inp.password = True  # masked — shows dots
        inp.value = ""
        inp.focus()

    def _show_name_step(self) -> None:
        """Step 4: name this profile (so multiple configs can coexist).

        Pre-fills with the existing profile name when editing, or the provider's
        label when creating new — most users will just press Enter. The name is
        how /profile lists and switches between configs.
        """
        self._step = "name"
        p = self._chosen_provider
        self.query_one("#onboard-list", ListView).display = False
        inp = self.query_one("#onboard-input", Input)
        inp.visible = True
        inp.password = False
        # Editing: show the current name; new: default to the provider label.
        inp.value = self._profile_name or p.label
        inp.focus()
        self.query_one("#onboard-hint").update(
            "给这套配置起个名字（回车确认，稍后可用 /profile 切换）：")

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
            self._show_name_step()
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
        """Save credentials + profile, then dismiss with result."""
        from pathlib import Path
        from coderio.cli.credentials import write_credentials
        from coderio.cli.onboarding import _save_profile_to_config, OnboardingResult
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
        name = self._profile_name or self._chosen_provider.label
        _save_profile_to_config(result, name, config_path)
        self.query_one("#onboard-status").update("[green]配置完成！[/green]")
        self.set_timer(0.8, lambda: self.dismiss({
            "provider_id": result.provider_id,
            "model": result.model,
            "profile_name": name,
        }))

    # --- event handlers ---

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """ListView item selected (Enter pressed)."""
        lv = self.query_one("#onboard-list", ListView)
        idx = lv.index
        if idx is None:
            return
        if self._step == "action":
            if idx < len(self._action_items):
                chosen = self._action_items[idx]
                self.query_one("#onboard-status").update("")
                if chosen is None:
                    # New profile — fresh wizard from provider selection.
                    self._editing_profile = None
                    self._show_provider_step()
                else:
                    # Edit existing — pre-fill its values, jump to model step
                    # (provider stays the same in the common case).
                    self._start_edit(chosen)
        elif self._step == "provider":
            if idx < len(self._provider_items):
                p = self._provider_items[idx]
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
            elif self._editing_profile:
                # Editing + empty input → keep the existing key, skip verification
                # (it was already verified when first configured).
                self.query_one("#onboard-status").update("")
                self._show_name_step()
            else:
                self.query_one("#onboard-status").update("[red]请输入 API key[/red]")
        elif self._step == "name":
            self._profile_name = val or self._chosen_provider.label
            self.query_one("#onboard-input", Input).visible = False
            self.query_one("#onboard-hint").update("[bold cyan]正在保存...[/bold cyan]")
            self._finish()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProfilePickerScreen(ModalScreen[str | None]):
    """Interactive profile picker (/profile).

    Lists all saved [[profiles]] from config.toml as a ListView — each row shows
    the profile name with its provider/model as a dim subtitle, and the active
    profile is marked ★. ↑↓ navigates, Enter switches (dismisses with the chosen
    profile name), Esc cancels (dismisses None). Mirrors the SessionPickerScreen
    UX so both pickers feel the same.
    """

    CSS = """
    ProfilePickerScreen { align: center middle; }
    #profile-box {
        width: 70%; height: auto; max-height: 70%; border: round $accent;
        background: $surface; padding: 1 2;
    }
    #profile-title { text-align: center; margin-bottom: 1; }
    #profile-list { height: auto; max-height: 16; }
    ProfilePickerScreen ListItem { padding: 0 1; }
    ProfilePickerScreen ListItem > Widget :hover { background: $boost; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=True),
    ]

    def __init__(self, profiles: list, active_name: str = "") -> None:
        super().__init__()
        self._profiles = profiles
        self._active_name = active_name

    def compose(self) -> ComposeResult:
        with Vertical(id="profile-box"):
            yield Static(
                "[bold magenta]切换配置[/bold magenta]  "
                "↑↓ 选择 · Enter 切换 · Esc 取消",
                id="profile-title")
            yield ListView(id="profile-list")

    def on_mount(self) -> None:
        lv = self.query_one("#profile-list", ListView)
        for p in self._profiles:
            star = "★ " if p.name == self._active_name else "  "
            lv.append(ListItem(Static(
                f"{star}{p.name}  [dim]{p.provider_id} · {p.model}[/dim]"),
                name=p.name))
        try:
            lv.index = 0
        except Exception:
            pass
        lv.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter on a row → switch to that profile."""
        self.dismiss(event.item.name)

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
        # Agent-thread-local flag: has a think_start already been queued for the
        # CURRENT round? `_live_think_body` is set by the MAIN thread 60ms later
        # (next _drain_render_queue tick), so reading it from the agent thread is
        # a race — within one drain window many on_thinking chunks arrive before
        # the main thread has had a chance to mount the Collapsible, and each one
        # would otherwise queue a SEPARATE think_start, fragmenting one continuous
        # thinking stream into N tiny Collapsibles ("The" / "The user is" / ...).
        # This flag is owned entirely by the agent thread (set in on_thinking,
        # cleared in _flush_round_thinking / on_finish), so there's no race.
        self._round_think_started: bool = False
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

    def _clear_history(self) -> None:
        """Wipe all widgets from the history pane (used by /clear).

        The old session's jsonl stays on disk — this only clears what's visible
        on screen. Must run on the main thread (touches the DOM).
        """
        try:
            h = self.query_one("#history", VerticalScroll)
            h.remove_children()
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
                    self._round_think_started = False
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
        # Decide think_start vs think_update using an AGENT-THREAD-LOCAL flag,
        # NOT `_live_think_body`. The latter is set by the main thread only after
        # the next _drain_render_queue tick (60ms away), so reading it here is a
        # race: within one drain window many on_thinking chunks arrive, each one
        # would re-enter the "first chunk" branch and queue another think_start,
        # fragmenting one continuous thinking stream into many tiny Collapsibles.
        if not self._round_think_started:
            # First chunk of this round: queue think_start with the FULL text so
            # far, and mark the round as started. The main thread will mount ONE
            # Collapsible; subsequent chunks queue think_update against that same
            # widget.
            self._round_think_started = True
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

    def on_harness_continue(self, reason: str) -> None:
        """Surface a harness force-continue as a dim notice line.

        Distinct from on_harness_warn (red escalation panel): this is a normal
        control-flow event, not an error. The model produced what looked like a
        final answer but the harness found unfinished work and demanded more.
        Without this visible cue, the user sees the answer Panel appear, then
        more thinking/tool calls follow with no explanation — reads as a bug.
        Renders as a single dim line so it doesn't compete with real output."""
        self._flush_round_thinking()
        self._flush_buffer()
        first_line = reason.splitlines()[0] if reason else ""
        if len(first_line) > 120:
            first_line = first_line[:117] + "…"
        self._render_q.append((
            "static",
            f"↻ harness 要求继续：{first_line}",
            "dim italic",
        ))

    def on_finish(self) -> None:
        # Capture everything remaining and push ONE finalize instruction.
        # The main-thread drain will fold thinking + mount the final Markdown Panel.
        think_text = self._round_thinking
        secs = time.monotonic() - self._round_think_start if self._round_think_start else 0.0
        # had_live = a live Collapsible was mounted for this round. Use the
        # agent-thread flag (truthful at this moment) rather than _live_think_body
        # (which the main thread owns and may not have updated yet).
        had_live = self._round_think_started
        buf = self.buffer
        # Reset accumulated state.
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._live_think_body = None
        self._live_think_chars = 0
        self._round_think_started = False
        self.buffer = ""
        self._live_output_last_flush = 0.0
        self._render_q.append(("finalize", buf, think_text, secs, had_live))
        if self._status_bar:
            self._status_bar.set_phase("idle")

    def add_usage(self, meta: dict[str, int]) -> None:
        for k in ("input_tokens", "output_tokens"):
            if k in meta:
                self.usage[k] += meta[k]

    def on_phase_change(self, state: str, step: int, hint: str) -> None:
        """Task-level phase change (explore/plan/implement/verify/...).

        Called from Harness._track_phase on the agent's background thread. Just
        forwards to StatusBar.set_task_phase (plain attribute write, GIL-safe);
        the heartbeat repaints within ~100ms. 'complete' clears the tag.
        """
        if self._status_bar:
            self._status_bar.set_task_phase("" if state == "complete" else state)

    # ----------------------------------------------------- thinking fold (true fold/unfold)
    def _flush_round_thinking(self) -> None:
        """Push a think_fold instruction to the render queue (agent thread).

        Called whenever a round's thinking needs to be sealed off: before each
        tool call, before non-thinking output begins, and at turn end. Clears
        the agent-thread `_round_think_started` flag so the NEXT round's first
        on_thinking chunk queues a fresh think_start (one Collapsible per round,
        never per-chunk)."""
        if not self._round_thinking.strip():
            # Even if there's no text, drop the started flag so the next round
            # gets a clean start (a stray True here with no body to fold would
            # make the next on_thinking skip think_start).
            self._round_think_started = False
            return
        text = self._round_thinking
        secs = time.monotonic() - self._round_think_start if self._round_think_start else 0.0
        # had_live = a live Collapsible was mounted for THIS round. Read the
        # agent-thread flag, not _live_think_body (main-thread state, races).
        had_live = self._round_think_started
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._live_think_body = None
        self._live_think_chars = 0
        self._round_think_started = False
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
        streaming RichLog with a Markdown Panel.

        Marked as an INTERMEDIATE panel (distinct title + dim cyan border) so the
        user can visually tell this is mid-process output, NOT the final answer.
        The final answer is mounted by _mount_final_panel with title "coderio"
        and a blue border. Without this distinction, a model that emits text then
        continues with more tool calls looks like it "finished, then restarted"."""
        # Remove the live streaming widget first (if present).
        self._render_q.append(("clear_live",))
        if self.buffer.strip():
            self._render_q.append(("panel", Panel(
                Markdown(self.buffer),
                border_style="cyan",
                title="[dim]中间输出 · agent 仍在运行…[/dim]",
            )))
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


def _switch_active_profile(profile_name: str) -> str:
    """Write the chosen profile name to config.toml as active_profile.

    Read-modify-write so other sections and the profiles array are preserved.
    Returns the name written (empty string if it couldn't be written). Called by
    the /profile picker callback after the user picks a profile.
    """
    import tomllib
    import tomli_w
    from pathlib import Path
    config_path = Path.home() / ".coderio" / "config.toml"
    data: dict = {}
    if config_path.is_file():
        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            data = {}
    data["active_profile"] = profile_name
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "wb") as f:
        tomli_w.dump(data, f)
    return profile_name


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
                profiles=rt["cfg"].profiles,
                active_profile=rt["cfg"].active_profile,
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
            if res.message == "__OPEN_ONBOARDING__":
                # /setup → open the OnboardingScreen to reconfigure provider/model.
                # After it completes, rebuild the runtime with the new config.
                def _on_reconfigured(result):
                    if result is None:
                        return
                    # Reload config + rebuild model with the new provider/key.
                    from coderio.llm import build_chat_model as _build
                    from pathlib import Path as _Path
                    creds = _Path.home() / ".coderio" / "credentials"
                    new_cfg = load_config(search_from=".")
                    rt["cfg"] = new_cfg
                    rt["model"] = _build(new_cfg, creds_path=creds)
                    tui._add_text(
                        f"✅ 已重新配置 → {new_cfg.model.default}（{new_cfg.model.provider_id}）",
                        style="bold green")
                tui.call_from_thread(tui.push_screen, OnboardingScreen(), _on_reconfigured)
                return
            if res.message == "__OPEN_PROFILE_PICKER__":
                # /profile → open the ProfilePickerScreen. After the user picks,
                # write active_profile to config.toml and rebuild the model.
                profiles = rt["cfg"].profiles or []
                active_name = rt["cfg"].active_profile
                if not profiles:
                    tui._add_text("[yellow]还没有保存的 profile。用 /setup 添加一个配置。[/yellow]")
                    return
                def _on_profile_picked(name):
                    if name is None or name == active_name:
                        return  # cancelled or re-picked the same one
                    _switch_active_profile(name)
                    from coderio.llm import build_chat_model as _build
                    new_cfg = load_config(search_from=".")
                    rt["cfg"] = new_cfg
                    rt["model"] = _build(new_cfg, creds_path=creds_path)
                    tui._add_text(
                        f"✅ 已切换到配置 → {name}", style="bold green")
                tui.call_from_thread(
                    tui.push_screen,
                    ProfilePickerScreen(profiles, active_name),
                    _on_profile_picked)
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
                if cmd_name == "/clear":
                    # /clear: start a fresh session + wipe active skills + clear
                    # the history pane. Without this the old session's messages
                    # keep being fed to the model (it reads session.messages).
                    _clear_context()
                    return
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
            context_cfg=rt["cfg"].context,
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
        # Count only conversation messages (exclude system-role metadata like
        # phase_timeline / context_summary so the count matches what's displayed).
        convo_msgs = [m for m in rt["session"].messages if m.role != "system"]
        tui._add_text(f"↩ 已恢复会话 {sid}（{len(convo_msgs)} 条历史消息）", style="bold green")
        for m in rt["session"].messages:
            if m.role == "user":
                c = m.content
                if isinstance(c, list):
                    c = " ".join(b.get("text", "") for b in c
                                 if isinstance(b, dict) and b.get("type") == "text")
                tui._add_text(f"▸ you {c}", style="bold cyan")
            elif m.role == "assistant":
                tui._add_text(f"  {m.content[:200]}", style="blue")

    def _clear_context() -> None:
        """Start a fresh session + clear active skills + wipe the history pane.

        Backs the /clear command. Without this the old session's messages keep
        being fed to the model (loop.py reads session.messages), so 'context
        cleared' was previously a lie — the model still saw the full history.
        """
        from coderio.session.store import Session
        from pathlib import Path as _P
        save_dir = _P(rt["cfg"].session.save_dir).expanduser()
        rt["session"] = Session.create(save_dir, {
            "model": rt["cfg"].model.default,
            "provider": rt["cfg"].model.provider,
        })
        active.clear()
        # Wipe the visible history pane so the user sees a clean slate (the old
        # session's jsonl is preserved on disk — /resume can still get it back).
        tui._clear_history()
        tui._add_text("🆕 已开启新会话（历史已清空，可用 /resume 恢复）", style="bold green")

    tui = CoderioTUI(on_input=on_input, show_tool_output=cfg.cli.show_tool_output, banner=banner)
    tui.run()
