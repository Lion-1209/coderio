from __future__ import annotations

from pydantic import BaseModel, Field

from coderio.agent.prompts import ActiveSkills
from coderio.skills.store import SkillStore


class ActivateSkillArgs(BaseModel):
    name: str = Field(description="Name of the skill to activate.")


class ActivateSkillTool:
    """Tool that activates a skill by name, loading its full playbook into context.

    Spec §2.3 (3): keyword/explicit skill activation. Returns a status string;
    the actual prompt refresh happens via the on_activate_skill callback in the
    loop (so the system prompt picks up the newly-active skill body).
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
        if not self.store.has(name):
            return f"Error: skill not found: {name}. Available: {', '.join(self.store.names())}"
        skill = self.store.get(name)
        newly = self.active.activate(skill)
        return f"Activated skill: {name}" if newly else f"Skill already active: {name}"


class DeactivateSkillArgs(BaseModel):
    name: str = Field(description="Name of the active skill to deactivate.")


class DeactivateSkillTool:
    """Tool that deactivates an active skill, dropping its body from context.

    Mirrors ActivateSkillTool. The budget warning in loop.py points the model at
    `deactivate_skill` when active skill bodies exceed ~30% of the context budget;
    without this tool that hint would reference a non-existent tool and the model
    would get an 'unknown tool' error. After deactivation the prompt is refreshed
    via the same on_activate_skill callback path (it rebuilds the system prompt
    from whatever skills remain active).
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
