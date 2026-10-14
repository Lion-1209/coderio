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

    ``set_deadline`` turns the wall-clock timeout into the SAME graceful
    interrupt path the TUI's Esc uses (is_interrupted → InterruptedError →
    deterministic generator close). Before this, the timeout exit reaped the
    daemon thread mid-flight and langgraph's executor raced interpreter
    teardown — every long task died with "cannot schedule new futures after
    shutdown" (Terminal-Bench 2026-10-10 autopsy: 6 of 11 failures).
    """

    def __init__(self, quiet: bool = False) -> None:
        super().__init__()
        self.quiet = quiet
        self._deadline: float | None = None

    def set_deadline(self, seconds: float) -> None:
        """Arm the wall-clock deadline; is_interrupted() flips once it passes."""
        import time

        self._deadline = time.monotonic() + seconds

    def is_interrupted(self) -> bool:
        if self._deadline is None:
            return False
        import time

        return time.monotonic() > self._deadline

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
    from coderio.tools.permission import PermissionMode

    # Validate the RAW value BEFORE the skip override rewrites it (WhaleDock
    # incident follow-up, 2026-09-23): the override used to run first, so
    # `--permission auto --dangerously-skip-permissions` never reached
    # normalize() — the retired zero-prompt alias sailed through the CLI even
    # after config-side retirement. A retired value is rejected regardless
    # of flags.
    try:
        PermissionMode.normalize(permission)
    except ValueError as e:
        typer.secho(f"Invalid --permission {permission!r}: {e}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    if skip_permissions:
        permission = "full"

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

    # Wall-clock timeout (v3 audit #14): SIGALRM doesn't exist on Windows, so
    # thread + join is the only portable OUTER mechanism. The timeout itself
    # is delivered GRACEFULLY: the stream's is_interrupted() flips at the
    # deadline and the engine's abort path (the same one the TUI's Esc uses)
    # closes the stream generator deterministically — no daemon-thread
    # reaping, no langgraph "cannot schedule new futures after shutdown"
    # crash (Terminal-Bench 2026-10-10 autopsy: every long-task timeout died
    # that way, losing all in-flight work to a teardown race).
    import threading

    _stream = HeadlessStream(quiet=quiet)

    def _execute() -> str:
        return run_deep_agent(
            user_input=task,
            spec=spec,
            session=session,
            stream=_stream,
        )

    if timeout > 0:
        _stream.set_deadline(timeout)

    result: dict = {}

    def _worker() -> None:
        try:
            result["final"] = _execute()
        except BaseException as e:  # noqa: BLE001 — captured, re-raised below
            result["error"] = e

    t = threading.Thread(target=_worker, daemon=True, name="coderio-run")
    t.start()
    # Two-tier join: (1) wait the requested budget; if the thread is still
    # alive the deadline has already flipped is_interrupted() — the engine
    # unwinds at its next chunk boundary. (2) a BOUNDED grace window for that
    # unwind (in-flight model call + generator close + checkpoint cleanup),
    # overridable for tests. Beyond the grace, hard-exit 124 as the last
    # resort (daemon reaping — the pre-graceful behavior, now only for a
    # stuck engine that never honors the interrupt).
    t.join(timeout=timeout if timeout > 0 else None)
    if timeout > 0 and t.is_alive():
        import os

        grace = float(os.environ.get("CODERIO_TIMEOUT_GRACE", "60"))
        t.join(timeout=grace)
    if timeout > 0 and t.is_alive():
        _log.warning(
            "coderio run timed out after %ds and graceful interrupt did not land "
            "within the grace window; the agent thread is a daemon and cannot "
            "be killed, and shell children it spawned may outlive this run — set "
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
        if isinstance(err, InterruptedError):
            # The deadline fired and the engine unwound cleanly — this is the
            # timeout, not an execution failure. The session jsonl retains the
            # partial work; partial streamed output is already above.
            typer.secho(
                f"Timed out after {timeout}s (--timeout). Interrupted cleanly; partial output above; exit 124.",
                err=True,
                fg=typer.colors.RED,
            )
            raise typer.Exit(124)
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
