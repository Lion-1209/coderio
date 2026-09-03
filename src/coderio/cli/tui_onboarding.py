"""TUI-based onboarding wizard — extracted from tui.py for modularity.

Contains OnboardingScreen (multi-step ModalScreen for provider/model/key setup),
_OnboardingApp (minimal App wrapper), and _run_onboarding_tui (entry point).

All coderio dependencies are lazy imports inside methods, so this module has
zero top-level coupling to the rest of coderio — it can be imported standalone.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView, Static


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
        Binding("escape", "cancel", "Cancel", show=True),
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
        # Context window size probed at verify time (0 = not probed / failed).
        # Persisted into the profile's context_limit so compaction uses the real
        # threshold — without this, a 256K model is mistreated as the 200K default.
        self._context_limit: int = 0
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
            yield Static("[bold magenta]coderio Setup Wizard[/bold magenta]", id="onboard-title")
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
        """Step 0 (only when profiles exist): choose "create new" or "edit" an existing one.

        One row to create a new profile, then one row per existing profile (with
        provider/model as a dim subtitle) to edit it. This is the /setup entry
        point when the user already has at least one configured profile.
        """
        self._step = "action"
        self.query_one("#onboard-input", Input).visible = False
        self.query_one("#onboard-hint").update("Choose an action (Up/Down · Enter to confirm · Esc to cancel)")
        lv = self.query_one("#onboard-list", ListView)
        lv.display = True
        lv.clear()
        self._action_items: list = []  # None=new, else the Profile to edit
        lv.append(ListItem(Static("  [green]➕[/green]  New configuration")))
        self._action_items.append(None)
        for p in self._existing_profiles:
            lv.append(ListItem(Static(f"  [yellow]✎[/yellow]  Edit  {p.name}  [dim]{p.provider_id} · {p.model}[/dim]")))
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
            self._chosen_provider = type(
                "_P",
                (),
                {
                    "id": profile.provider_id,
                    "label": profile.name,
                    "kind": profile.kind,
                    "base_url": profile.base_url,
                    "models": (),
                    "default_model": "",
                    "api_key_hint": "",
                    "plan": False,
                },
            )()
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
        hint = "Select a provider (Up/Down · Enter to confirm · Esc to cancel)" + (
            f"   [green]{configured_count} configured[/green]" if configured_count else ""
        )
        self.query_one("#onboard-hint").update(hint)
        self.query_one("#onboard-input", Input).visible = False
        lv = self.query_one("#onboard-list", ListView)
        lv.display = True
        lv.clear()
        self._provider_items = []

        # Build a flat list with group labels. Each item is a real provider.
        groups = [
            ("Subscription", [p for p in self._providers if p.plan]),
            (
                "China direct",
                [p for p in self._providers if not p.plan and p.id in ("bigmodel_api", "stepfun_api")],
            ),
            ("International", [p for p in self._providers if p.id in ("openai", "anthropic")]),
            ("Local", [p for p in self._providers if p.id == "ollama"]),
            ("Custom", [p for p in self._providers if p.id == "openai_custom"]),
        ]
        for group_name, providers in groups:
            for p in providers:
                ms = f" ({' / '.join(p.models[:2])}{'...' if len(p.models) > 2 else ''})" if p.models else ""
                check = "  [green]✓[/green]" if p.id in self._configured else "   "
                lv.append(ListItem(Static(f"  [dim]{group_name}[/dim]  {p.label}{ms}{check}")))
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
            self.query_one("#onboard-hint").update("Enter model name (e.g. qwen2.5-coder / gpt-4o):")
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
        # P3-2: the preset list ages fast — the last item opens a free-text
        # input so a user on a newer plan isn't locked to stale ids.
        lv.append(ListItem(Static("  [yellow]✎[/yellow] Type a model name…")))
        # When editing, highlight the profile's current model (if it's in the
        # preset list) so the user can just press Enter to keep it.
        start_idx = 0
        if self._editing_profile:
            if self._editing_profile.model in self._model_items:
                start_idx = self._model_items.index(self._editing_profile.model)
            else:
                # Custom model created via the free-text sentinel (P3-2):
                # highlight the SENTINEL, not the first preset — otherwise
                # "Enter to keep" silently swapped the model (audit 2026-09-02 P2).
                start_idx = len(self._model_items)
        try:
            lv.index = start_idx
        except Exception:
            pass
        self.query_one("#onboard-hint").update(
            f"Pick a model ({p.label}) — star = recommended (Up/Down · Enter; last item = type your own)"
        )
        lv.focus()

    def _show_base_url_step(self) -> None:
        """Step 2b: base_url input (openai_custom only)."""
        self._step = "base_url"
        self.query_one("#onboard-list", ListView).display = False
        self.query_one("#onboard-hint").update("Enter base URL (e.g. https://api.example.com/v1):")
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
            # P1-3 follow-up (audit): keep a freshly entered key across a
            # verify-failure retry — overwriting it with the stored value
            # discarded the user's new key AND let an empty submit skip
            # verification right after a failure (audit P2).
            if not self._api_key:
                from coderio.cli.credentials import get_key

                self._api_key = get_key(p.id) or ""
            self.query_one("#onboard-hint").update(
                f"Enter a new API key (leave empty to keep the current one) — {p.api_key_hint}:"
            )
        else:
            self.query_one("#onboard-hint").update(f"Enter API key ({p.api_key_hint}):")
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
            "Name this configuration (Enter to confirm; switch later with /profile):"
        )

    def _start_verification(self) -> None:
        """Step 4: verify the key via a minimal API request + probe context_limit."""
        self._step = "verifying"
        self.query_one("#onboard-input", Input).visible = False
        self.query_one("#onboard-hint").update("[bold cyan]Verifying connection...[/bold cyan]")

        def _verify():
            from coderio.cli.onboarding import _verify_and_probe

            ok, msg, context_limit = _verify_and_probe(
                self._chosen_provider, self._api_key, self._chosen_model, self._base_url
            )
            self.app.call_from_thread(self._on_verify_result, ok, msg, context_limit)

        import threading

        threading.Thread(target=_verify, daemon=True).start()

    def _on_verify_result(self, ok: bool, msg: str, context_limit: int = 0) -> None:
        if ok:
            # Store the probed context_limit so _finish can persist it into the
            # profile. Without this, /setup-configured models miss the compaction
            # threshold optimization (only CLI onboarding was probing).
            self._context_limit = context_limit
            self._last_failed_model = ""
            self.query_one("#onboard-status").update(f"[green]✅ {msg}[/green]")
            self._show_name_step()
        else:
            # P1-3 (2026-09-03): a failure can come from the KEY *or* the model
            # name — going back to the key alone was an inescapable loop for a
            # mistyped model id. Return to the MODEL step (sentinel prefilled
            # with the failed id); the key step later prefills this key.
            self.query_one("#onboard-status").update(f"[red]❌ {msg}[/red]")
            self._last_failed_model = self._chosen_model
            self._show_model_step()

    def _finish(self) -> None:
        """Save credentials + profile, then dismiss with result."""
        from pathlib import Path

        from coderio.cli.credentials import write_credentials
        from coderio.cli.onboarding import OnboardingResult, _save_profile_to_config

        creds_path = Path.home() / ".coderio" / "credentials"
        write_credentials({self._chosen_provider.id: self._api_key}, creds_path)
        result = OnboardingResult(
            provider_id=self._chosen_provider.id,
            model=self._chosen_model,
            base_url=self._base_url,
            kind=self._chosen_provider.kind,
            api_key=self._api_key,
            context_limit=self._context_limit,
        )
        config_path = creds_path.parent / "config.toml"
        name = self._profile_name or self._chosen_provider.label
        _save_profile_to_config(result, name, config_path)
        self.query_one("#onboard-status").update("[green]Setup complete![/green]")
        self.set_timer(
            0.8,
            lambda: self.dismiss(
                {
                    "provider_id": result.provider_id,
                    "model": result.model,
                    "profile_name": name,
                }
            ),
        )

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
            if idx == len(self._model_items):
                # "Type a model name…" sentinel (P3-2): switch to free text.
                # Pre-fill when editing (keep current = bare Enter) or after a
                # verify failure (retry with the corrected model id).
                self._step = "model_input"
                lv.display = False
                inp = self.query_one("#onboard-input", Input)
                inp.visible = True
                inp.password = False
                failed = getattr(self, "_last_failed_model", "")
                inp.value = failed or (self._editing_profile.model if self._editing_profile else "")
                inp.focus()
                self.query_one("#onboard-hint").update("Enter model name (any valid id for this provider works):")
                return
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
                self.query_one("#onboard-status").update("[red]Please enter a base URL[/red]")
        elif self._step == "model_input":
            if val:
                self._chosen_model = val
                self.query_one("#onboard-status").update("")
                self._show_key_step()
            else:
                self.query_one("#onboard-status").update("[red]Please enter a model name[/red]")
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
                self.query_one("#onboard-status").update("[red]Please enter an API key[/red]")
        elif self._step == "name":
            self._profile_name = val or self._chosen_provider.label
            self.query_one("#onboard-input", Input).visible = False
            self.query_one("#onboard-hint").update("[bold cyan]Saving...[/bold cyan]")
            self._finish()

    def action_cancel(self) -> None:
        self.dismiss(None)


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
