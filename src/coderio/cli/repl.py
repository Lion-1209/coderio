from __future__ import annotations

import getpass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from coderio.agent.prompts import ActiveSkills
from coderio.config import Config, load_config
from coderio.llm import build_chat_model
from coderio.session.store import Session
from coderio.skills.store import load_skill_store, SkillStore
from coderio.tools import build_default_tools
from coderio.tools.permission import PermissionGate, PermissionMode, RichPromptPermissionGate, AutoPermissionGate

from coderio.cli.stream import RichStream

BUNDLED_SKILLS = Path(__file__).resolve().parents[1] / "skills"


def build_gate(cfg: Config, console=None):
    mode = cfg.tools.permission_mode
    if mode == PermissionMode.AUTO:
        return AutoPermissionGate()
    if mode == PermissionMode.PLAN:
        return PermissionGate(PermissionMode.PLAN)
    return RichPromptPermissionGate(console=console)


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
        store = load_skill_store(BUNDLED_SKILLS, Path.home() / ".coderio" / "skills", Path(search_from) / ".coderio" / "skills")
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


def _maybe_onboard(console, creds_path):
    """Run onboarding if no key configured. Returns provider_id or None."""
    from coderio.cli.credentials import read_credentials
    creds = read_credentials(creds_path)
    if creds:
        return next(iter(creds))
    from coderio.cli.onboarding import run_onboarding
    result = run_onboarding(
        prompt_fn=lambda msg: console.input(f"{msg} "),
        password_fn=lambda: getpass.getpass("API key: "),
        creds_file=creds_path,
    )
    return result.provider_id if result else None


def _resolve_resume(cfg: Config, resume: str | None, continue_last: bool) -> Session:
    save_dir = Path(cfg.session.save_dir).expanduser()
    if resume:
        return Session.load_by_id(save_dir, resume)
    recent = Session.list_recent(save_dir, limit=1)
    if not recent:
        raise SystemExit("No previous session to continue.")
    return Session.load_by_id(save_dir, recent[0])

