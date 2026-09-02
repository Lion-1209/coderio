"""User-configurable hooks as a deepagents AgentMiddleware + turn-level events.

Lets users run their own shell commands at well-defined points of the agent
lifecycle — "reformat every file after Edit", "block writes to .env", "inject
project conventions at session start". The config/IO contract follows the
Claude Code / ZCode / Codex interop core so existing hook scripts port over:

  - Events (v1): SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop
  - stdin: one JSON object with session/cwd/event fields (+ event-specific ones)
  - exit codes: 0 = pass (stdout may inject context for prompt/session events),
    2 = BLOCKING (stderr becomes the reason fed to the model), other = fail-open
  - matcher: regex matched against the tool name (tool events only)

SECURITY POSITIONING (documented in README too): hooks are an EXTENSIBILITY
point, not a security boundary. Timeout/crash/non-2 failures are FAIL-OPEN —
a dead hook must never brick the agent loop. Hard policy belongs to the
permission gate + command blacklist; hooks are for workflow glue. Repo-level
hooks live in config.toml, which the repo-config trust gate (config/trust.py)
already covers — cloning a hostile repo does not silently run its hooks.

Architecture: PreToolUse/PostToolUse ride a middleware inserted OUTERMOST
(before Harness/Permission/CommandReview) so a hook can deny a call before
the permission prompt appears, and observes exactly the args the rest of the
chain will see. SessionStart/UserPromptSubmit/Stop are turn-level and are
fired directly from run_deep_agent.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

_log = logging.getLogger(__name__)

# v1 event set: the intersection of Claude Code / ZCode / Codex events, plus
# Stop. SessionEnd has no injection point (TUI exits without a hook call —
# v2 candidate). Notification/SubagentStop/PreCompact deliberately deferred.
HOOK_EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"})

# Tool events are the only ones where a matcher against the tool name makes sense.
_TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse"})

DEFAULT_TIMEOUT = 60  # seconds; generous for linters, short enough not to brick a turn

# Per-EVENT total budget (v3 audit P1: serial hooks × 60s each would hang every
# tool call). Covers ALL matching hooks for one event fire; exhaustion skips
# the rest (fail-open). Overridable per HookRunner for tests.
DEFAULT_EVENT_BUDGET = 30.0

# stdout beyond this is dropped rather than injected (context budget guard).
_MAX_CONTEXT_CHARS = 10_000


@dataclass
class HookOutcome:
    """Aggregated result of firing one event (all matching hooks, serially).

    blocked=True means at least one hook exited 2 — for PreToolUse the caller
    denies the tool call; for UserPromptSubmit the prompt is rejected. context
    carries pass-through stdout (SessionStart/UserPromptSubmit injection).
    error carries fail-open diagnostics (non-blocking, logged + surfaced).
    """

    blocked: bool = False
    reason: str = ""
    context: str = ""
    error: str = ""


# Env whitelist for hook subprocesses (2026-09-02 audit P3-6): hooks are
# repo-configurable commands — trust.py gates them, but a hostile repo can
# still ship one, and a full os.environ copy leaks every secret in the
# environment (API keys, tokens, cloud credentials) to whatever the hook
# runs. Pass only what a hook reasonably needs: CODERIO_* (ours), the
# shell's executable-resolution basics per platform, and temp/locale vars.
_HOOK_ENV_KEYS = frozenset(
    {
        # executable resolution / shell basics
        "PATH",
        "PATHEXT",
        "COMSPEC",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "HOME",
        "USERPROFILE",
        "USER",
        "USERNAME",
        # temp + locale (hook scripts print localized text)
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "TERM",
    }
)


def _hook_env(project_dir: str) -> dict[str, str]:
    """Build the whitelisted environment for a hook subprocess."""
    import os

    env = {k: v for k, v in os.environ.items() if k.startswith("CODERIO_") or k in _HOOK_ENV_KEYS}
    env["CODERIO_PROJECT_DIR"] = project_dir
    return env


@dataclass
class HookSpec:
    """One [[hooks]] entry from config.toml."""

    event: str
    command: str
    matcher: str = ""  # regex against tool_name; "" matches all. Tool events only.
    timeout: int = DEFAULT_TIMEOUT

    def compiled_matcher(self) -> re.Pattern | None:
        """None = match everything; invalid regex = never match (not crash)."""
        if not self.matcher:
            return None
        try:
            return re.compile(self.matcher)
        except re.error:
            _log.warning("hook matcher %r is not a valid regex — hook never fires", self.matcher)
            return re.compile(r"(?!x)x")  # unmatchable

    def matches(self, tool_name: str) -> bool:
        rx = self.compiled_matcher()
        return rx is None or rx.search(tool_name) is not None


class HookRunner:
    """Loads [[hooks]] specs and fires events by running shell commands.

    One instance per run_deep_agent call (cheap); turn-level events share it
    with the middleware. SessionStart dedup lives at CLASS level: a fresh
    runner is built every turn, so instance state can't remember which
    sessions already fired (resume included by design — the lazy fire happens
    on the first turn of any loaded session).
    """

    # session ids that have already fired SessionStart (process-lifetime).
    _sessions_started: set[str] = set()

    def __init__(
        self,
        specs: list[HookSpec] | None,
        project_dir: str,
        session_id: str = "",
        permission_mode: str = "",
        event_budget: float = DEFAULT_EVENT_BUDGET,
    ) -> None:
        self.specs = [s for s in (specs or []) if s.event in HOOK_EVENTS]
        self.project_dir = project_dir
        self.session_id = session_id
        self.permission_mode = permission_mode
        self.event_budget = event_budget

    def has_event(self, event: str) -> bool:
        return any(s.event == event for s in self.specs)

    def fire(self, event: str, payload: dict[str, Any]) -> HookOutcome:
        """Run every matching hook for the event, serially, merging outcomes.

        Serial by design (v1): Claude Code's parallel execution makes
        multi-hook semantics non-deterministic; determinism wins for a coding
        agent. First blocker wins; later hooks still run (their side effects
        are the point — e.g. logging — even when the event is already denied).

        A per-EVENT budget (v3 audit P1: N hooks × 60s hanging on EVERY tool
        call) caps the total time all matching hooks may take; when exhausted,
        remaining hooks are skipped with an error note (fail-open — consistent
        with the module's positioning: budget exhaustion never blocks).
        """
        import time

        tool_name = str(payload.get("tool_name", ""))
        outcome = HookOutcome()
        deadline = time.monotonic() + self.event_budget
        try:
            for i, spec in enumerate(self.specs):
                if spec.event != event:
                    continue
                if event in _TOOL_EVENTS and not spec.matches(tool_name):
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Count the matching hooks NOT yet reached (self-audit
                    # 2026-08-18: the old slice self.specs[len(self.specs):]
                    # was always empty — the count never appeared). Filter by
                    # matcher too: a same-event hook whose matcher wouldn't
                    # have run anyway is not "skipped" (third-party audit
                    # caught the overcount).
                    pending = [
                        s
                        for s in self.specs[i:]
                        if s.event == event and (event not in _TOOL_EVENTS or s.matches(tool_name))
                    ]
                    outcome.error += (
                        f"event budget ({self.event_budget}s) exhausted — {len(pending)} hook(s) skipped (fail-open); "
                    )
                    break
                self._run_one(spec, event, payload, outcome, remaining)
        except Exception as e:  # noqa: BLE001 — a hook-layer bug must never break the turn
            outcome.error += f"hook engine error (fail-open): {e}; "
        # CONSUME the error (2026-08-14 v3 audit P1): a broken hook (missing
        # script → exit 127, bad interpreter, etc.) must be visible to the
        # user, not silently swallowed — the outcome.error field previously
        # had zero consumers anywhere in the codebase.
        if outcome.error:
            _log.warning("hooks[%s]: %s", event, outcome.error.strip())
        return outcome

    # ------------------------------------------------------------------ execution

    def _run_one(
        self,
        spec: HookSpec,
        event: str,
        payload: dict[str, Any],
        outcome: HookOutcome,
        remaining: float,
    ) -> None:
        stdin_json = json.dumps(
            {
                "session_id": self.session_id,
                "cwd": self.project_dir,
                "permission_mode": self.permission_mode,
                "hook_event_name": event,
                **payload,
            },
            default=str,
        )
        try:
            exit_code, stdout, stderr, timed_out, effective_timeout = self._spawn(spec, stdin_json, remaining)
        except Exception as e:  # noqa: BLE001 — a broken hook must never crash the agent
            outcome.error += f"hook {spec.command!r} failed to run: {e} (fail-open); "
            return

        if timed_out:
            outcome.error += f"hook {spec.command!r} timed out after {effective_timeout}s (fail-open); "
            return

        # New names, not reusing stdout/stderr: mypy pins a variable's type to
        # its first binding (bytes from _spawn), so rebinding to str is an error.
        out_text = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace")

        if exit_code == 2:
            # Blocking error: stderr (or stdout) is the reason for the model.
            # First blocker wins (subsequent blockers still RUN — their side
            # effects matter — but don't clobber the first reason).
            outcome.blocked = True
            if not outcome.reason:
                reason = (err_text.strip() or out_text.strip())[:_MAX_CONTEXT_CHARS]
                outcome.reason = reason or "blocked by hook (no reason given)"
            return
        if exit_code != 0:
            # Fail-open: log + surface, never block. This is the documented
            # trade-off — hooks are glue, not a security boundary.
            detail = (err_text.strip() or out_text.strip())[:200]
            outcome.error += f"hook {spec.command!r} exited {exit_code} (fail-open){': ' + detail if detail else ''}; "
            return
        # exit 0: stdout may inject context for prompt/session events.
        if event in ("UserPromptSubmit", "SessionStart") and out_text.strip():
            text = out_text.strip()
            if len(text) > _MAX_CONTEXT_CHARS:
                _log.warning("hook stdout exceeded %d chars — context dropped", _MAX_CONTEXT_CHARS)
            else:
                outcome.context += ("\n" if outcome.context else "") + text

    def _spawn(self, spec: HookSpec, stdin_json: str, remaining: float) -> tuple[int, bytes, bytes, bool, int]:
        """Run the hook command with JSON on stdin.

        Returns (exit_code, stdout, stderr, timed_out, effective_timeout).
        effective_timeout = min(spec.timeout, event-budget remaining) — the
        per-event budget tightens each hook so one slow hook can't starve the
        rest of the turn (v3 audit P1).

        On Windows prefers Git Bash (via detect_shell, same as the bash tool)
        so POSIX-style commands behave; POSIX uses /bin/sh.
        """

        from coderio.tools.win_job import kill_process_tree

        argv: list[str]
        if sys.platform == "win32":
            try:
                from coderio.tools.bash import detect_shell

                argv = [detect_shell(""), "-c", spec.command]
            except FileNotFoundError:
                argv = ["cmd", "/c", spec.command]
        else:
            argv = ["/bin/sh", "-c", spec.command]

        env = _hook_env(self.project_dir)

        # POSIX: the hook MUST get its own process group (start_new_session) —
        # kill_process_tree's POSIX branch kills via os.killpg, and without a
        # separate session the child shares OUR group, so a hook timeout would
        # SIGKILL the agent itself (caught by the Linux/macOS CI matrix after
        # the Windows-only local run passed; bash.py:242 has the same guard).
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.project_dir,
            env=env,
            start_new_session=(sys.platform != "win32"),
        )
        # ceil, not int(): remaining ≈ budget-ε on the first hook, and int()
        # truncation would cut a 2s budget to 1s (caught by the CI OS matrix —
        # deterministic on POSIX, timing-jitter-lucky on Windows).
        import math

        effective_timeout = max(1, min(spec.timeout, math.ceil(remaining)))
        try:
            stdout, stderr = proc.communicate(input=stdin_json.encode("utf-8"), timeout=effective_timeout)
            return proc.returncode, stdout or b"", stderr or b"", False, effective_timeout
        except subprocess.TimeoutExpired:
            kill_process_tree(proc)
            # One-second grace, then abandon (v3 audit P1: the old 10s drain
            # waited out pipe EOF that never comes when a pre-kill grandchild
            # holds the write end on Windows — a timeout=2 hook took 12s). The
            # output of a TIMED-OUT hook is never consumed anyway (hardcoded
            # empty return), so there is nothing to wait for. Known limitation:
            # kill_process_tree may miss Windows grandchildren forked before
            # the job assignment; they leak but can no longer stall the turn.
            try:
                proc.communicate(timeout=1)
            except Exception:  # noqa: S110 — grace expired; the kill already ran
                pass
            return 124, b"", b"", True, effective_timeout


class HooksMiddleware(AgentMiddleware):
    """PreToolUse / PostToolUse as the OUTERMOST middleware.

    Inserted before Harness/Permission/CommandReview (see run_deep_agent): a
    hook deny happens before the permission prompt can bother the user, and
    hooks observe exactly the args the rest of the chain will see. A deny
    returns a synthetic ToolMessage (same short-circuit shape as
    PermissionMiddleware) so the model learns why and can react.
    """

    def __init__(self, runner: HookRunner) -> None:
        self.runner = runner

    def wrap_tool_call(self, request, handler):
        tc = getattr(request, "tool_call", None) or {}
        name = tc.get("name", "")
        args = dict(tc.get("args", {}) or {})
        tool_call_id = tc.get("id", "")

        pre = self.runner.fire("PreToolUse", {"tool_name": name, "tool_input": args, "tool_use_id": tool_call_id})
        if pre.blocked:
            return ToolMessage(
                content=f"Blocked by hook: {pre.reason}",
                tool_call_id=tool_call_id,
                name=name,
            )

        result = handler(request)

        post = self.runner.fire(
            "PostToolUse",
            {
                "tool_name": name,
                "tool_input": args,
                "tool_use_id": tool_call_id,
                "tool_response": _result_to_text(result),
            },
        )
        # PostToolUse can't undo the executed tool; exit 2 appends feedback so
        # the model sees it alongside the real result (Claude Code semantics).
        if post.blocked:
            note = f"\n[hook] {post.reason}"
            content = getattr(result, "content", None)
            if isinstance(content, str):
                result.content = content + note
            elif isinstance(result, str):
                result = result + note
            else:
                out = getattr(result, "output", None)
                if isinstance(out, str):
                    result.output = out + note
        return result


def _result_to_text(result: Any) -> str:
    """Best-effort text extraction from ToolMessage / ExecuteResponse / str."""
    for attr in ("content", "output"):
        v = getattr(result, attr, None)
        if isinstance(v, str):
            return v
    if isinstance(result, str):
        return result
    return str(result)
