from __future__ import annotations

from pathlib import Path

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


def build_gate(cfg: Config, console=None):
    """Construct the permission gate with a workspace policy attached.

    The policy enforces path boundaries in ALL modes (including AUTO) — auto
    mode skips interactive confirmation, not the security floor. The root
    defaults to the process CWD when workspace_root is unset.
    """
    policy = WorkspacePolicy(root=cfg.tools.workspace_root)
    mode = cfg.tools.permission_mode
    if mode == PermissionMode.AUTO:
        return AutoPermissionGate(policy=policy)
    if mode == PermissionMode.PLAN:
        return PermissionGate(PermissionMode.PLAN, policy=policy)
    return RichPromptPermissionGate(console=console, policy=policy)


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
