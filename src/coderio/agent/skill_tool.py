from __future__ import annotations

from pydantic import BaseModel, Field

from coderio.agent.prompts import ActiveSkills
from coderio.skills.store import SkillStore


class ActivateSkillArgs(BaseModel):
    name: str = Field(description="Name of the skill to activate.")


class ActivateSkillTool:
    """Tool that activates a skill by name, loading its full playbook into context.

    The playbook body is returned IN the tool result, so the model can follow
    it in the SAME turn (a tool result is context-immediate). ``active`` also
    records the skill so the next system-prompt build keeps the body pinned
    (the ActiveSkills instance is shared with the loop).

    2026-09-04 audit P1-9: the old status-only return ("Activated skill: X")
    relied on an ``on_activate_skill`` callback that never existed — the body
    only reached the system prompt on the NEXT turn, so the model believed it
    had the manual in hand a full round before it did.
    """

    name = "activate_skill"
    description = (
        "Activate a skill by name to load its full playbook into context. "
        "Use when a task matches a skill's domain and you need its detailed playbook."
    )
    args_schema = ActivateSkillArgs

    def __init__(self, store: SkillStore, active: ActiveSkills) -> None:
        self.store = store
        self.active = active

    def run(self, name: str) -> str:
        skill = self.store.get(name)
        if skill is None:
            return f"Error: skill not found: {name}. Available: {', '.join(self.store.names())}"
        newly = self.active.activate(skill)
        # The body ALWAYS rides the result (third-party adversarial review
        # note, 2026-09-04): after context compaction the playbook may have
        # fallen out of the window while `active` still lists it — a
        # re-activation that only said "already active" would leave the model
        # unable to recover the manual.
        prefix = "Activated skill" if newly else "Skill re-activated (already active)"
        return f"{prefix}: {name} — playbook follows.\n\n{skill.body}"


class DeactivateSkillArgs(BaseModel):
    name: str = Field(description="Name of the active skill to deactivate.")


class DeactivateSkillTool:
    """Tool that deactivates an active skill, dropping its body from context.

    Mirrors ActivateSkillTool. The budget warning in the system prompt points the model at
    `deactivate_skill` when active skill bodies exceed ~30% of the context budget;
    without this tool that hint would reference a non-existent tool and the model
    would get an 'unknown tool' error. The next system-prompt build (next turn)
    rebuilds from whatever skills remain active.
    """

    name = "deactivate_skill"
    description = (
        "Deactivate an active skill to free context. Use when active skill bodies "
        "are consuming too much budget and one is no longer needed for the task."
    )
    args_schema = DeactivateSkillArgs

    def __init__(self, active: ActiveSkills) -> None:
        self.active = active

    def run(self, name: str) -> str:
        removed = self.active.deactivate(name)
        if not removed:
            active_names = [s.name for s in self.active.all()]
            return f"Error: skill not active: {name}. Active skills: {', '.join(active_names) or '(none)'}"
        return f"Deactivated skill: {name}. Prompt will refresh on next turn."
