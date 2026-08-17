"""``coderio run`` — headless one-shot agent execution.

Non-interactive counterpart to the TUI: same runtime (config, model, tools,
skills, session), no Textual app. Purpose-built for CI, scripting, and
benchmark harnesses (Terminal-Bench / Harbor agents call exactly this shape:
give a task string, get the final result).

Design rules:
- NEVER block on interactive prompts. Onboarding missing → error exit.
  Untrusted repo config → error exit (run interactive ``coderio`` once to
  confirm). Permission mode defaults to ``full`` because there is no TTY to
  answer confirm prompts; users who want a stricter tier pass
  ``--permission plan`` (plan never prompts — it just denies).
- Default permission is FULL but the command blacklist still applies
  (CommandReviewMiddleware is independent of the permission gate —
  ``rm -rf /`` stays blocked), and the harness four gates stay active.
- Streams tokens to stdout so long tasks show progress; ``--quiet`` silences
  everything except the final result.
"""

from __future__ import annotations

import sys
from typing import Any

from coderio.agent.stream import NullStream


class HeadlessStream(NullStream):
    """Stream for non-interactive runs: tokens to stdout, progress to stderr.

    NullStream base keeps every other hook a no-op. ``quiet=True`` disables
    all streaming — the caller only prints the final result.
    """

    def __init__(self, quiet: bool = False) -> None:
        super().__init__()
        self.quiet = quiet

    def on_token(self, text: str) -> None:
        if not self.quiet:
            sys.stdout.write(text)
            sys.stdout.flush()

    def on_tool_start(
        self,
        name: str,
        args: dict[str, Any],
        step: int = 1,
        tool_index: int = 0,
        tool_total: int = 0,
    ) -> None:
        if not self.quiet:
            brief = args.get("command") or args.get("path") or args.get("pattern") or ""
            brief = str(brief)[:80]
            print(f"[tool] {name} {brief}", file=sys.stderr)

    def on_finish(self) -> None:
        if not self.quiet:
            sys.stdout.write("\n")
            sys.stdout.flush()


def run_headless(
    task: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    permission: str = "full",
    session_id: str | None = None,
    quiet: bool = False,
) -> None:
    """Entry point behind ``coderio run``. Prints the final agent reply to
    stdout (or errors to stderr + exit 1). Always returns None — failures
    raise SystemExit via typer, handled by the CLI wrapper."""
    from pathlib import Path

    import typer

    from coderio.config.bootstrap import ensure_user_dirs
    from coderio.config.loader import _find_project_dir
    from coderio.config.trust import existing_repo_configs, is_repo_trusted

    ensure_user_dirs()
    creds_path = Path.home() / ".coderio" / "credentials"

    # Interactive onboarding would hang forever without a TTY — fail loudly.
    from coderio.cli.repl import _needs_onboarding

    if _needs_onboarding(creds_path):
        typer.secho(
            "No credentials configured. Run `coderio` once interactively to "
            "complete onboarding, or set ANTHROPIC_API_KEY / OPENAI_API_KEY / "
            "Z_API_KEY in the environment.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    # Repo-config trust: same gate as the TUI, but non-interactive — an
    # untrusted repo config is a hard error (prompting would hang headless
    # runs). The user confirms once via interactive `coderio`, then this
    # passes forever (until the repo config content changes).
    user_dir = Path.home() / ".coderio"
    project_dir = _find_project_dir(Path(".").resolve())
    if existing_repo_configs(project_dir) and not is_repo_trusted(project_dir, user_dir):
        typer.secho(
            f"This repository has untrusted coderio config ({project_dir}).\n"
            "Run interactive `coderio` once in this directory and confirm the "
            "config, then retry `coderio run`.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    try:
        from coderio.cli.repl import build_runtime
        from coderio.config import load_config

        cfg_early = load_config()
        session = None
        if session_id:
            from coderio.session.store import Session

            try:
                session = Session.load_by_id(cfg_early.session.save_dir, session_id)
            except Exception as e:  # noqa: BLE001 — surface as a clean CLI error
                typer.secho(f"Could not load session {session_id!r}: {e}", err=True, fg=typer.colors.RED)
                raise typer.Exit(1)

        cfg, store, chat_model, tools, gate, session, active, _rich_stream = build_runtime(
            console=None,
            mode_override=permission,
            model_override=model,
            provider_override=provider,
            session=session,
        )
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — config/model errors → clean exit
        typer.secho(f"Runtime setup failed: {e}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)

    from coderio.agent.deep_loop import run_deep_agent
    from coderio.tools.command_policy import CommandPolicy

    cmd_policy = CommandPolicy(
        extra_blocked=cfg.tools.blocked_commands,
        network_allowed=cfg.tools.network_allowed,
        whitelist_mode=cfg.tools.whitelist_mode,
        allowed_commands=cfg.tools.allowed_commands,
    )
    final = run_deep_agent(
        user_input=task,
        model=chat_model,
        session=session,
        stream=HeadlessStream(quiet=quiet),
        gate=gate,
        skill_store=store,
        active_skills=active,
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
    if quiet:
        print(final)
