"""Explicit agent state machine — observability for execution phases.

Inspired by Step-Realtime-CLI's AgentStateMachine, but unlike theirs (which only
RECORDS transitions for telemetry), this tracker derives phase from the harness's
ground-truth signals (writes/verifications/todos) so the displayed phase reflects
what the agent is actually doing, not just what it claims.

The timeline persists to the session jsonl at turn end (via a system-role
Message) so a past turn's phase progression can be replayed for debugging.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class AgentState(StrEnum):
    """Structural phases of a coding-agent turn.

    Derived from harness ground truth (never from model self-report):
      EXPLORE   — reading code (read_file/grep/list_dir), no writes yet
      PLAN      — a write happened but no todo list exists (PlanGate signal)
      IMPLEMENT — writes exist, not yet verified
      VERIFY    — bash ran a verifying command (test/build/lint)
      COMPLETE  — turn ended (cleanly or max-rounds)
    """

    EXPLORE = "explore"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    COMPLETE = "complete"


@dataclass
class StateSnapshot:
    """One point in the phase timeline."""

    state: AgentState
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    step: int = 0  # ReAct round number when the transition fired
    hint: str = ""  # optional context (e.g. "wrote loader.py", "ran pytest")

    def to_dict(self) -> dict:
        return {
            "state": str(self.state),
            "timestamp": self.timestamp,
            "step": self.step,
            "hint": self.hint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StateSnapshot":
        return cls(
            state=AgentState(d.get("state", "explore")),
            timestamp=d.get("timestamp", ""),
            step=d.get("step", 0),
            hint=d.get("hint", ""),
        )


class AgentStateTracker:
    """Records the phase timeline for one agent turn.

    Phase is derived: ``derive_phase()`` maps harness state to an AgentState.
    ``transition()`` is called from Harness.observe / check_termination; if the
    derived phase matches the current one, nothing is recorded (debounce — a
    turn that does 10 read_files produces ONE explore snapshot, not ten).
    """

    def __init__(self) -> None:
        self._current: AgentState = AgentState.EXPLORE
        self.timeline: list[StateSnapshot] = []

    @property
    def current(self) -> AgentState:
        return self._current

    def derive_phase(
        self,
        writes_since_verify: list[str],
        todos_exist: bool,
        just_verified: bool,
    ) -> AgentState:
        """Map harness ground truth to a phase.

        Called from inside Harness (which has the live state); the tracker does
        NOT reach into Harness to avoid a circular dependency.

        Args:
            writes_since_verify: files written but not yet verified (non-empty ⇒ IMPLEMENT)
            todos_exist: whether a todo list exists (False + first write ⇒ PLAN)
            just_verified: whether a verifying bash ran this tool call (⇒ VERIFY)
        """
        if just_verified:
            return AgentState.VERIFY
        if writes_since_verify:
            # Writes exist without a plan → still in the plan-implicit phase
            # (PlanGate would nudge here). Once a todo list exists, it's full IMPLEMENT.
            return AgentState.PLAN if not todos_exist else AgentState.IMPLEMENT
        return AgentState.EXPLORE

    def transition(self, state: AgentState, step: int = 0, hint: str = "") -> bool:
        """Record a phase change. No-op if ``state`` equals the current phase
        (debounce: repeated same-phase observations don't bloat the timeline).

        Returns True if a new snapshot was recorded, False if debounced. Callers
        that also notify a stream should use this to avoid spamming on_phase_change
        for repeated same-phase observations.
        """
        if state == self._current and self.timeline:
            return False  # same phase, already recorded — skip
        self._current = state
        self.timeline.append(StateSnapshot(state=state, step=step, hint=hint))
        return True

    def finish(self, step: int = 0, hint: str = "") -> None:
        """Mark the turn as complete. Always records (even if already COMPLETE)
        so the final step number is captured for timeline replay."""
        self._current = AgentState.COMPLETE
        self.timeline.append(StateSnapshot(state=AgentState.COMPLETE, step=step, hint=hint))

    def to_payload(self) -> list[dict]:
        """Serialize the timeline for persistence (system-role Message content)."""
        return [s.to_dict() for s in self.timeline]

    @staticmethod
    def from_payload(payload: list[dict]) -> "AgentStateTracker":
        """Reconstruct a timeline from persisted data (for session replay)."""
        t = AgentStateTracker()
        for d in payload:
            snap = StateSnapshot.from_dict(d)
            t.timeline.append(snap)
            t._current = snap.state
        return t

    def summary(self) -> str:
        """One-line human-readable summary for UI display."""
        if not self.timeline:
            return str(self._current)
        phases = " → ".join(str(s.state) for s in self.timeline)
        return f"{phases} ({len(self.timeline)} transitions)"
