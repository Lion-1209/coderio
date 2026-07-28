from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from coderio.agent.prompts import ActiveSkills
from coderio.cli.stream import RichStream
from coderio.config import Config, load_config
from coderio.llm import build_chat_model
from coderio.session.store import Session
from coderio.skills.store import SkillStore, load_skill_store
from coderio.tools import build_default_tools
from coderio.tools.permission import (
    AutoPermissionGate,
    PermissionGate,
    PermissionMode,
    RichPromptPermissionGate,
)
from coderio.tools.workspace import WorkspacePolicy

BUNDLED_SKILLS = Path(__file__).resolve().parents[1] / "skills"


class TuiPermissionGate(PermissionGate):
    """Gate for CONFIRM and AUTO_EDIT modes in the TUI.

    Uses a Textual ModalScreen (via request_confirmation) instead of input(),
    which would deadlock against Textual's terminal takeover. The mode is
    passed through so check() applies the right tier logic before _ask fires.
    """

    def __init__(self, mode, tui, policy=None):
        super().__init__(mode, policy=policy)
        self._tui = tui

    def _ask(self, tool_name: str, args: dict[str, Any]) -> bool | str:
        if hasattr(self._tui, "request_confirmation"):
            return self._tui.request_confirmation(tool_name, args)
        # Fallback for tests without a real TUI: auto-allow.
        return True


def build_gate(cfg: Config, console=None, tui=None):
    """Construct the permission gate with a workspace policy attached.

    Four permission tiers (least → most permissive):
      plan      — read-only, blocks all writes/bash
      confirm   — prompts before each destructive action
      auto_edit — auto-allow file edits, bash/web/note still confirm
      full      — auto-allow everything

    The policy enforces path boundaries in ALL tiers. The root defaults to
    the process CWD when workspace_root is unset.

    When ``tui`` is provided, confirm/auto_edit modes use a Textual ModalScreen
    instead of input() (which deadlocks against Textual's terminal takeover).
    """
    policy = WorkspacePolicy(root=cfg.tools.workspace_root)
    mode = PermissionMode.normalize(cfg.tools.permission_mode)
    if mode == PermissionMode.FULL:
        return AutoPermissionGate(policy=policy)
    if mode == PermissionMode.PLAN:
        return PermissionGate(PermissionMode.PLAN, policy=policy)
    # CONFIRM and AUTO_EDIT both need _ask for some tools — use TuiPermissionGate
    # (with tui) or RichPromptPermissionGate (without tui, e.g. REPL/CLI mode).
    if tui is not None:
        return TuiPermissionGate(mode, tui=tui, policy=policy)
    # Non-TUI fallback: RichPromptPermissionGate for CONFIRM, or base
    # PermissionGate for AUTO_EDIT (check() handles tier logic, _ask uses
    # input() — fine in REPL where Textual isn't active).
    if mode == PermissionMode.AUTO_EDIT:
        return _ReplAutoEditGate(console=console, policy=policy)
    return RichPromptPermissionGate(console=console, policy=policy)


class _ReplAutoEditGate(PermissionGate):
    """AUTO_EDIT gate for the non-TUI REPL (uses input() for high-risk tools)."""

    def __init__(self, console=None, policy=None):
        super().__init__(PermissionMode.AUTO_EDIT, policy=policy)
        self._console = console

    def _ask(self, tool_name: str, args: dict[str, Any]) -> bool:
        return _default_prompt_repl(tool_name, args)


def _default_prompt_repl(tool_name: str, args) -> bool:
    """Simple input()-based prompt for the REPL (no Textual active)."""
    confirm = input(f"Allow {tool_name}({args})? [y/N] ").strip().lower()
    return confirm in {"yes", "y"}


def build_runtime(
    search_from: Path | str = ".",
    save_dir: Path | str | None = None,
    session: Session | None = None,
    console=None,
    creds_path: Path | str | None = None,
    mode_override: str | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
):
    cfg = load_config(search_from=search_from)

    if mode_override:
        from dataclasses import replace as _replace

        cfg = _replace(cfg, tools=_replace(cfg.tools, permission_mode=mode_override))

    if model_override:
        from dataclasses import replace as _replace

        cfg = _replace(cfg, model=_replace(cfg.model, default=model_override))

    if provider_override:
        from dataclasses import replace as _replace

        cfg = _replace(cfg, model=_replace(cfg.model, provider_id=provider_override))

    if cfg.skills.auto_load:
        store = load_skill_store(
            BUNDLED_SKILLS,
            Path.home() / ".coderio" / "skills",
            Path(search_from) / ".coderio" / "skills",
        )
    else:
        store = SkillStore()

    model = build_chat_model(cfg, creds_path=creds_path)
    tools = build_default_tools(cfg.tools.bash_shell)
    gate = build_gate(cfg, console=console)

    if session is None:
        save = save_dir or Path(cfg.session.save_dir).expanduser()
        session = Session.create(save, {"model": cfg.model.default, "provider": cfg.model.provider})

    active = ActiveSkills()
    stream = RichStream(console or Console())
    return cfg, store, model, tools, gate, session, active, stream


def _needs_onboarding(creds_path) -> bool:
    """Check whether the onboarding wizard should run.

    Returns False (skip onboarding) if ANY key source exists:
      - credentials file has at least one key
      - config.toml already has a provider_id
      - ANTHROPIC_API_KEY / OPENAI_API_KEY / Z_API_KEY env var is set
    """
    import os

    from coderio.cli.credentials import read_credentials

    creds = read_credentials(creds_path)
    if creds:
        return False
    config_path = Path(creds_path).parent / "config.toml"
    if config_path.is_file():
        try:
            import tomllib

            with open(config_path, "rb") as f:
                data = tomllib.load(f)
            if data.get("model", {}).get("provider_id"):
                return False
        except Exception:
            pass
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("Z_API_KEY"):
        return False
    return True


def _resolve_resume(cfg: Config, resume: str | None, continue_last: bool) -> Session:
    save_dir = Path(cfg.session.save_dir).expanduser()
    if resume:
        return Session.load_by_id(save_dir, resume)
    recent = Session.list_recent(save_dir, limit=1)
    if not recent:
        raise SystemExit("No previous session to continue.")
    return Session.load_by_id(save_dir, recent[0])
