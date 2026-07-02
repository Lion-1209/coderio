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
        "Activate a skill by name to load its full playbook into context. Use when a task"
    )
    args_schema = ActivateSkillArgs

    def __init__(self, store: SkillStore, active: ActiveSkills) -> None:
        self.store = store
        self.active = active

    def run(self, name: str) -> str:
        if not self.store.has(name):
            return f"Error: skill not found: {name}. Available: {', '.join(self.store.names())}"
        skill = self.store.get(name)
        self.active.activate(skill)
        return f"Activated skill: {name}"
