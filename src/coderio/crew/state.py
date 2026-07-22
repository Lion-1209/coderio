from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, TypedDict


@dataclass
class ProjectState:
    """Cross-agent shared state. Each agent reads prior artifacts, writes its own.

    Kept as the public return type of CrewOrchestrator.run() and the type the
    _build_prompt/_run_role helpers consume (they use attribute access). The
    LangGraph graph uses CrewState (a TypedDict) internally for its state-channel
    mechanism; _state_to_projectstate bridges the two.

    ``status`` reflects the final outcome of the crew run:
      - "success": verify passed (or no verify needed) → commit is authoritative.
      - "partial": verify failed but the fix budget was exhausted, so the crew
        committed the best-effort result. The user should review before trusting.
      - "failed": verify failed and the crew could not produce any commit, OR an
        unexpected error aborted the run. Check `errors` for details.
    This lets the CLI distinguish green/yellow/red outcomes instead of always
    showing "✓ crew 完成" regardless of what happened under the hood.
    """

    request: str
    current_stage: str = "clarification"
    clarification: str = ""
    spec: str = ""
    task_list: str = ""
    implementation: str = ""
    verification: str = ""
    commit_message: str = ""
    user_clarification_answer: str = ""
    user_spec_approval: str = ""
    errors: list[str] = field(default_factory=list)
    fix_feedback: str = ""
    fix_attempts: int = 0
    status: str = "success"  # "success" | "partial" | "failed"


# --- LangGraph state ---------------------------------------------------------
#
# LangGraph's StateGraph requires a TypedDict whose fields are Annotated with a
# reducer (the function that merges a node's update into the accumulated state).
# For crew, each role OVERWRITES its own field (new value replaces old), except
# `errors` which accumulates. This mirrors the pre-LangGraph behavior where
# setattr(state, field, value) simply replaced.


def _overwrite(_old, new):
    """Reducer: a role's write replaces the field's prior value entirely."""
    return new


def _append_list(old, new):
    """Reducer: errors accumulate across stages."""
    return (old or []) + (new or [])


class CrewState(TypedDict, total=False):
    """LangGraph state channel schema. Fields mirror ProjectState 1:1.

    `total=False` so run() can seed only a subset (request + fix_attempts=0);
    LangGraph treats absent keys per their reducer.
    """
    request: Annotated[str, _overwrite]
    current_stage: Annotated[str, _overwrite]
    clarification: Annotated[str, _overwrite]
    spec: Annotated[str, _overwrite]
    task_list: Annotated[str, _overwrite]
    implementation: Annotated[str, _overwrite]
    verification: Annotated[str, _overwrite]
    commit_message: Annotated[str, _overwrite]
    user_clarification_answer: Annotated[str, _overwrite]
    user_spec_approval: Annotated[str, _overwrite]
    errors: Annotated[list[str], _append_list]
    fix_feedback: Annotated[str, _overwrite]
    fix_attempts: Annotated[int, _overwrite]
    status: Annotated[str, _overwrite]


def crew_state_to_project_state(d: dict) -> ProjectState:
    """Convert a LangGraph CrewState dict to a ProjectState dataclass.

    Used at node boundaries so _build_prompt/_run_role (which use attribute
    access) can consume the graph state. Missing keys default per ProjectState.
    """
    return ProjectState(
        request=d.get("request", ""),
        current_stage=d.get("current_stage", "clarification"),
        clarification=d.get("clarification", ""),
        spec=d.get("spec", ""),
        task_list=d.get("task_list", ""),
        implementation=d.get("implementation", ""),
        verification=d.get("verification", ""),
        commit_message=d.get("commit_message", ""),
        user_clarification_answer=d.get("user_clarification_answer", ""),
        user_spec_approval=d.get("user_spec_approval", ""),
        errors=list(d.get("errors", [])),
        fix_feedback=d.get("fix_feedback", ""),
        fix_attempts=d.get("fix_attempts", 0),
        status=d.get("status", "success"),
    )
