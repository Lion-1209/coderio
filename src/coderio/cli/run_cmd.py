"""``coderio run`` — headless one-shot agent execution.

Non-interactive counterpart to the TUI: same runtime (config, model, tools,
skills, session), no Textual app. Purpose-built for CI, scripting, and
benchmark harnesses (Terminal-Bench / Harbor agents call exactly this shape:
give a task string, get the final result).

Design rules:
- NEVER block on interactive prompts. Onboarding missing → error exit.
  Untrusted repo config → error exit (run interactive ``coderio`` once to
  confirm). Permission defaults to read-only PLAN; full access requires the
  explicit ``--dangerously-skip-permissions`` flag. Any value that resolves
  to full (including the legacy ``auto`` alias) is gated the same way;
  confirm/auto_edit are not valid headless values at all.
- The command blacklist still applies in every mode (CommandReviewMiddleware
  is independent of the permission gate — ``rm -rf /`` stays blocked), and
  the harness four gates stay active.
- Streams tokens to stdout so long tasks show progress; ``--quiet`` silences
  everything except the final result.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from coderio.agent.stream import NullStream

_log = logging.getLogger(__name__)


class HeadlessStream(NullStream):
    """Stream for non-interactive runs: tokens to stdout, progress to stderr.

    NullStream base keeps every other hook a no-op. ``quiet=True`` disables
    all streaming — the caller only prints the final result.

    stdout writes are guarded: after a ``--timeout`` kill the agent's daemon
    thread can still be draining the model stream while the interpreter tears
    down stdout (a closed stream raises ValueError, a closed pipe OSError) —
    a late write must not crash the thread with a noisy traceback.
    """

    def __init__(self, quiet: bool = False) -> None:
        super().__init__()
        self.quiet = quiet

    def on_token(self, text: str) -> None:
        if not self.quiet:
            try:
                sys.stdout.write(text)
                sys.stdout.flush()
            except (ValueError, OSError):  # stdout closed during interpreter teardown
                pass

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
            try:
                sys.stdout.write("\n")
                sys.stdout.flush()
            except (ValueError, OSError):  # stdout closed during interpreter teardown
                pass


def run_headless(
    task: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    permission: str = "plan",
    session_id: str | None = None,
    quiet: bool = False,
    skip_permissions: bool = False,
    timeout: int = 0,
) -> None:
    """Entry point behind ``coderio run``. Prints the final agent reply to
    stdout (or errors to stderr + the documented exit code).

    Exit codes: 0 success / 1 config or environment error / 2 agent execution
    failure / 124 wall-clock timeout.
    """
    from pathlib import Path

    import typer

    from coderio.config.bootstrap import ensure_user_dirs
    from coderio.config.trust import existing_repo_configs, is_repo_trusted

    ensure_user_dirs()

    # Permission validation + safety gates (v3 audit #7, hardened 2026-08-18
    # self-audit BUG A/B + third-party audit): default is PLAN. The gates
    # check the NORMALIZED mode, not the literal string — the first BUG A fix
    # compared == "full" and the legacy alias "auto" (normalize() maps it to
    # FULL) sailed through. Any mode that RESOLVES to full requires
    # --dangerously-skip-permissions; confirm/auto_edit (and anything else
    # that prompts) are not valid headless values at all.
    if skip_permissions:
        permission = "full"
    from coderio.tools.permission import PermissionMode

    try:
        normalized = PermissionMode.normalize(permission)
    except ValueError as e:
        typer.secho(f"Invalid --permission {permission!r}: {e}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    if normalized is PermissionMode.FULL and not skip_permissions:
        typer.secho(
            f"--permission {permission!r} resolves to full and requires "
            "--dangerously-skip-permissions. Headless runs are read-only (plan) "
            "by default; full access is an explicit opt-in.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    if permission in ("confirm", "auto_edit"):
        typer.secho(
            f"--permission {permission!r} is not available in headless mode "
            "(prompts need an interactive TTY). Use plan (default), full via "
            "--dangerously-skip-permissions, or the interactive TUI.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

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
    # search_from-based discovery (v3 audit P0): walks up per-file like the
    # loaders, so a repo whose root only has .mcp.json can't slip past by
    # launching from a subdirectory.
    user_dir = Path.home() / ".coderio"
    if existing_repo_configs(".") and not is_repo_trusted(".", user_dir):
        typer.secho(
            "This repository has untrusted coderio config.\n"
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
    except typer.Exit:
        # typer.Exit is a RuntimeError subclass (click's Exit) — nested exits
        # (e.g. session-load failure) already printed their message; swallowing
        # them here printed a misleading "Runtime setup failed: 1" second line
        # (2026-08-18 self-audit ⚠️). Pass through with the original code.
        raise
    except Exception as e:  # noqa: BLE001 — config/model errors → clean exit
        typer.secho(f"Runtime setup failed: {e}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)

    from coderio.agent.deep_loop import run_deep_agent
    from coderio.cli.repl import build_turn_spec

    # Single construction path (P2-1): field-identical to the TUI's — drift
    # between the two would silently give headless runs a different sandbox
    # or permission wiring than interactive ones.
    spec = build_turn_spec(
        cfg,
        model=chat_model,
        gate=gate,
        skill_store=store,
        active_skills=active,
        tools=tools,
    )

    def _execute() -> str:
        return run_deep_agent(
            user_input=task,
            spec=spec,
            session=session,
            stream=HeadlessStream(quiet=quiet),
        )

    # Wall-clock timeout (v3 audit #14): SIGALRM doesn't exist on Windows, so
    # thread + join is the only portable mechanism. The agent thread is a
    # daemon — on timeout we return 124 and let the process exit reap it
    # (mid-flight subprocesses die with the parent on POSIX process groups /
    # Windows job objects where wired; this is CI-safety, not precise cancel).
    import threading

    result: dict = {}

    def _worker() -> None:
        try:
            result["final"] = _execute()
        except BaseException as e:  # noqa: BLE001 — captured, re-raised below
            result["error"] = e

    t = threading.Thread(target=_worker, daemon=True, name="coderio-run")
    t.start()
    t.join(timeout=timeout if timeout > 0 else None)
    if t.is_alive():
        _log.warning(
            "coderio run timed out after %ds; the agent thread is a daemon and cannot be "
            "killed, and shell children it spawned may outlive this run — set "
            '[tools].sandbox_mode = "job" so the job object reaps the whole process tree',
            timeout,
        )
        typer.secho(
            f"Timed out after {timeout}s (--timeout). Partial output above; exit 124.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(124)
    if "error" in result:
        # mypy [misc]: `e` was bound by the worker's except clause; rebinding an
        # exception variable outside except is flagged, so alias it here.
        err = result["error"]
        typer.secho(f"Agent execution failed: {err}", err=True, fg=typer.colors.RED)
        raise typer.Exit(2)

    final = result["final"]
    # Always print the final result (v3 audit P2): in non-quiet mode the token
    # stream already showed it, but a non-streamed final message would be lost
    # without this — and scripts parsing stdout need exactly one final line.
    # --quiet relies on this print alone; non-quiet gets it after a separator.
    if quiet:
        print(final)
    else:
        print(f"\n{final}")
