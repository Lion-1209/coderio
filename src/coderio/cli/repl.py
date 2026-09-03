from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console

from coderio.agent.prompts import ActiveSkills
from coderio.cli.stream import RichStream
from coderio.config import load_config
from coderio.config.loader import _find_project_dir
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

if TYPE_CHECKING:
    from coderio.config import Config

BUNDLED_SKILLS = Path(__file__).resolve().parents[1] / "skills"


class TuiPermissionGate(PermissionGate):
    """Gate for CONFIRM and AUTO_EDIT modes in the TUI.

    Uses a Textual ModalScreen (via request_confirmation) instead of input(),
    which would deadlock against Textual's terminal takeover.

    ``workdir`` is the engine's backend root (workspace_root or launch dir) —
    relative file_path args in the confirmation's diff preview resolve against
    it (P3-1, 2026-09-02).
    """

    def __init__(self, mode, tui, auto_allow_execute: bool = False, workdir=None):
        super().__init__(mode, auto_allow_execute=auto_allow_execute)
        self._tui = tui
        self._workdir = workdir

    def _ask(self, tool_name: str, args: dict[str, Any]) -> bool | str:
        if hasattr(self._tui, "request_confirmation"):
            detail = None
            try:
                # Best effort — a preview failure must never block confirmation.
                from coderio.tools.confirm_diff import build_diff_preview

                detail = build_diff_preview(tool_name, args, self._workdir)
            except Exception:  # noqa: BLE001
                detail = None
            return self._tui.request_confirmation(tool_name, args, detail=detail)
        return True


def build_turn_spec(
    cfg: Config,
    *,
    model,
    gate,
    skill_store,
    active_skills,
    tools,
):
    """Build the engine TurnSpec from a loaded config (P2-1, 2026-09-02 audit
    finding 8: run_cmd.py and tui_runtime.py carried two field-identical
    CommandPolicy + TurnSpec constructions — a config-layer drift surface).

    Rebuilt per turn by callers whose runtime objects can change between
    turns (TUI /model swaps the model, /clear swaps the session): TurnSpec
    fields are snapshots.
    """
    from coderio.agent.deep_loop import TurnSpec
    from coderio.tools.command_policy import CommandPolicy

    cmd_policy = CommandPolicy(
        extra_blocked=cfg.tools.blocked_commands,
        network_allowed=cfg.tools.network_allowed,
        whitelist_mode=cfg.tools.whitelist_mode,
        allowed_commands=cfg.tools.allowed_commands,
    )
    return TurnSpec(
        model=model,
        gate=gate,
        skill_store=skill_store,
        active_skills=active_skills,
        tools=tools,
        workdir=cfg.tools.workspace_root or None,
        harness_enabled=cfg.skills.harness,
        command_policy=cmd_policy,
        sandbox_mode=cfg.tools.sandbox_mode,
        network_allowed=cfg.tools.network_allowed,
        fs_config=cfg.tools.sandbox_fs,
        bash_shell=cfg.tools.bash_shell,
        hooks=cfg.hooks,
    )


def build_gate(cfg: Config, console=None, tui=None):
    """Construct the permission gate.

    Four permission tiers (least → most permissive):
      plan      — read-only, blocks all writes/shell
      confirm   — prompts before each destructive action
      auto_edit — auto-allow file edits, shell/web/note still confirm
      full      — auto-allow everything

    Path isolation is handled by deepagents' backend virtual_mode, not by
    this gate. This gate only controls WHICH tool types may execute.

    Sandbox联动 (Claude Code "autoAllowBashIfSandboxed" design): when
    sandbox_mode != "off" AND cfg.tools.auto_allow_if_sandboxed is True, the
    execute tool auto-approves without prompting (the sandbox provides the real
    isolation boundary, so per-command prompts become noise). The blacklist
    still applies via CommandReviewMiddleware. PLAN mode is unaffected.
    """
    mode = PermissionMode.normalize(cfg.tools.permission_mode)
    # auto_allow_execute is meaningful only when a sandbox is active + the
    # user opted in. FULL mode already allows everything; PLAN stays read-only.
    sandbox_active = cfg.tools.sandbox_mode != "off"
    auto_exec = sandbox_active and cfg.tools.auto_allow_if_sandboxed
    # HONEST WARNING (2026-08-28 audit, extended 2026-09-03): with auto-allow
    # on, the user believes a filesystem boundary protects them while execute
    # runs with the parent's full permissions. That belief is wrong on TWO
    # platforms: Windows (neither sandbox mode isolates file writes) and
    # macOS (no OS-level sandbox at all — bwrap is Linux-only). Warn on every
    # gate build so "zero isolation + zero confirmation" is never silent.
    warn = None
    if auto_exec and cfg.tools.sandbox_mode in ("job", "write"):
        if sys.platform == "win32":
            warn = (
                "⚠ [sandbox] Windows 下沙箱暂无文件写隔离（job/write 均为资源限制）。"
                "auto_allow_if_sandboxed 已开启，execute 将无确认执行（黑名单仍生效）。"
            )
        elif sys.platform == "darwin":
            warn = (
                "⚠ [sandbox] macOS 暂无 OS 级沙箱（bwrap 仅支持 Linux）。"
                "auto_allow_if_sandboxed 已开启，execute 将无确认执行（黑名单仍生效）。"
            )
    if warn:
        if console is not None:
            console.print(warn, style="yellow")
        else:
            print(warn, file=sys.stderr)
    if mode == PermissionMode.FULL:
        return AutoPermissionGate()
    if mode == PermissionMode.PLAN:
        return PermissionGate(PermissionMode.PLAN)
    if tui is not None:
        return TuiPermissionGate(mode, tui=tui, auto_allow_execute=auto_exec, workdir=cfg.tools.workspace_root or None)
    if mode == PermissionMode.AUTO_EDIT:
        return _ReplAutoEditGate(console=console, auto_allow_execute=auto_exec)
    return RichPromptPermissionGate(console=console, auto_allow_execute=auto_exec)


class _ReplAutoEditGate(PermissionGate):
    """AUTO_EDIT gate for the non-TUI REPL (uses input() for high-risk tools)."""

    def __init__(self, console=None, auto_allow_execute: bool = False):
        super().__init__(PermissionMode.AUTO_EDIT, auto_allow_execute=auto_allow_execute)
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
    search_from = Path(search_from).resolve()
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
        _proj = _find_project_dir(search_from)
        store = load_skill_store(
            BUNDLED_SKILLS,
            Path.home() / ".coderio" / "skills",
            _proj / ".coderio" / "skills",
        )
    else:
        store = SkillStore()

    model = build_chat_model(cfg, creds_path=creds_path)
    tools = build_default_tools(cfg.tools.bash_shell)
    gate = build_gate(cfg, console=console)

    # Load MCP tools from .mcp.json (project + user scope). Connection failures
    # are logged and skipped — they never block startup. This is a one-time
    # async operation bridged to sync via asyncio.run.
    from coderio.mcp_loader import load_mcp_tools_sync

    mcp_tools = load_mcp_tools_sync(search_from=search_from)
    if mcp_tools:
        tools = tools + mcp_tools

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
