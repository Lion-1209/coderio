"""Harness state control for the single-agent loop (spec: 2026-06-26-coderio-harness-state-control).

This module turns the "write code then claim done without verifying" failure mode
from a soft prompt rule into a STRUCTURAL constraint. The harness sits inside
``_execute_turn`` and controls the one thing the model cannot override: whether a
"no tool_calls" response actually TERMINATES the turn, or gets force-continued with
an injected message. All decisions are based on observed tool calls/results
(ground truth), never on the model's self-report.

Three gates (spec §1):
  * PlanGate (soft)      — nudge to decompose before writing, when no todos exist.
  * VerifyGate (hard)    — block "done" while code is written-but-not-run.
  * CompletionGate (hard)— block "done" while non-trivial todos remain pending.

VerifyGate + CompletionGate use progressive escalation: force-continue twice, then
release with a visible warning (never silently, never infinite-loop).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coderio.tools.todo import TodoStore

# Tools that mutate files on disk. A successful one of these creates an
# "unverified write" that the VerifyGate watches.
WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file", "multi_edit"})

# Running a shell command counts as a verification attempt — even a failing one
# means the agent tried to run its code, so we stop nagging. This avoids both
# "wrote then claimed done" and infinite "it errored, keep trying" loops.
VERIFY_TOOL: str = "bash"

# After this many forced-continues a gate gives up and releases with a warning.
_MAX_GATE_ATTEMPTS: int = 2


def _is_success(result: str) -> bool:
    """A write tool result counts as a real write unless it errored.

    All three write tools report failures as ``"Error: ..."`` (verified against
    write_file.py / edit_file.py / multi_edit.py). A failed write changed nothing
    on disk, so it must not trigger the verify gate.
    """
    return not result.startswith("Error")


@dataclass
class HarnessState:
    """Per-turn harness state, derived purely from observed tool calls.

    No field here is writable by the model — it is all ground truth accumulated
    by ``Harness.observe`` as tools execute.
    """
    # File paths written since the last verification (bash). Non-empty here means
    # "there is unverified code on disk".
    writes_since_verify: list[str] = field(default_factory=list)
    # Times the verify gate has force-continued this turn (resets to 0 on bash).
    verify_attempts: int = 0
    # Times the completion gate has force-continued this turn.
    completion_attempts: int = 0
    # Whether the plan gate has nudged already this turn (nudge at most once).
    plan_nudged: bool = False


@dataclass
class Harness:
    """The structural constraint layer. Stateless across turns except via state.

    ``enabled`` short-circuits every method to a no-op/passthrough, so the loop
    can always hold a Harness object and just flip this flag (or pass harness=None).
    """
    state: HarnessState
    todos: "TodoStore"
    enabled: bool = True

    # ------------------------------------------------------------------ observe
    def observe(self, name: str, args: dict, result: str) -> None:
        """Update internal state from a tool execution (called after every tool).

        - successful write tool  -> record path as an unverified write
        - bash (any result)      -> writes are now "verified attempt", clear + reset
        """
        if not self.enabled:
            return
        if name in WRITE_TOOLS and _is_success(result):
            path = str(args.get("path", ""))
            if path and path not in self.state.writes_since_verify:
                self.state.writes_since_verify.append(path)
        elif name == VERIFY_TOOL:
            # Any bash call counts as a verification attempt: the agent ran its
            # code. Clear the pending writes and reset the gate counter.
            self.state.writes_since_verify.clear()
            self.state.verify_attempts = 0

    # ------------------------------------------------------- after_tool_call (plan gate)
    def after_tool_call(self, name: str, args: dict, result: str) -> str | None:
        """PlanGate (soft): if writing code with no task list, append a one-time nudge.

        Returns text to APPEND to the tool result (the tool still ran), or None.
        Soft = never blocks; only nudges. The hard check on todo completion lives
        in check_termination (CompletionGate).
        """
        if not self.enabled:
            return None
        if name not in WRITE_TOOLS:
            return None
        if self.state.plan_nudged:
            return None
        if self.todos.todos:  # already has a plan -> no nudge
            return None
        self.state.plan_nudged = True
        return (
            "\n[nudge] You're writing code but have no task list yet. For non-trivial "
            "work, call todo(action=\"add\", ...) first to decompose the task into "
            "verifiable steps. (Trivial fixes like a typo can ignore this.)"
        )

    # ------------------------------------------------------ check_termination (hard gates)
    def check_termination(self, final_text: str) -> tuple[bool, str | None, str | None]:
        """Decide whether a "no tool_calls" response may actually end the turn.

        Returns ``(should_continue, inject_message, warn_message)``:
          * should_continue=True, inject set  -> loop must NOT return; append inject
            as a user message and keep going.
          * should_continue=False, warn set   -> loop returns normally, but stream
            must show the warning (escalation release).
          * should_continue=False, warn None  -> truly done, return silently.
        """
        if not self.enabled:
            return (False, None, None)

        # VerifyGate has priority: code written but never run is the core failure.
        cont, inject, warn = self._verify_gate()
        if cont or warn:
            return (cont, inject, warn)

        # CompletionGate: only meaningful when a non-trivial todo list exists.
        return self._completion_gate()

    # --------------------------------------------------------------- VerifyGate
    def _verify_gate(self) -> tuple[bool, str | None, str | None]:
        """Hard gate: unverified writes may not silently end the turn."""
        if not self.state.writes_since_verify:
            return (False, None, None)

        attempt = self.state.verify_attempts
        self.state.verify_attempts += 1

        if attempt >= _MAX_GATE_ATTEMPTS:
            # Escalation exhausted: release, but loudly. Never silent.
            files = ", ".join(self.state.writes_since_verify)
            return (False, None,
                    f"agent wrote code to [{files}] but never ran it to verify, "
                    "then declared completion. Output is UNVERIFIED — please review.")
        if attempt == 0:
            return (True,
                    "[harness] You wrote code but haven't verified it. You MUST run it "
                    "(use bash to execute/test/lint the files you changed) before "
                    "declaring done. Do not summarize or claim completion — call bash now.",
                    None)
        # attempt == 1: second interception, name the files and tighten the screws.
        files = ", ".join(self.state.writes_since_verify)
        return (True,
                f"[harness] STILL no verification. Files written but not run: {files}. "
                "Run them with bash now. Do NOT reply with text — call bash.",
                None)

    # ----------------------------------------------------------- CompletionGate
    def _completion_gate(self) -> tuple[bool, str | None, str | None]:
        """Hard gate: pending todos may not silently end the turn.

        Skipped entirely when there is no todo list (trivial-task exemption): a
        small Q&A or typo fix that never built a plan should not be blocked here.
        The PlanGate's soft nudge is the only pressure in that case.
        """
        if not self.todos.todos:
            return (False, None, None)
        pending = [t for t in self.todos.todos if t.status != "completed"]
        if not pending:
            return (False, None, None)

        attempt = self.state.completion_attempts
        self.state.completion_attempts += 1

        if attempt >= _MAX_GATE_ATTEMPTS:
            return (False, None,
                    f"agent declared completion with {len(pending)} unfinished todo(s). "
                    "Some planned work may be incomplete — please review the task list.")
        return (True,
                f"[harness] Your task list has {len(pending)} unfinished item(s). Mark "
                "them complete via todo(action=\"update\", status=\"completed\") only if "
                "truly done, or finish the remaining work. Do not claim overall "
                "completion with pending todos.",
                None)
