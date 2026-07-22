from __future__ import annotations

from pathlib import Path

from coderio.skills.models import Skill
from coderio.skills.parser import parse_skill_file

_PRIORITY = ("bundled", "user", "project")


class SkillStore:
    """Holds merged skills across three layers. Metadata cached, body lazy."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def _load_layer(self, layer_dir: Path | None, layer_name: str) -> None:
        if not layer_dir or not layer_dir.is_dir():
            return
        for skill_md in layer_dir.glob("**/SKILL.md"):
            try:
                skill = parse_skill_file(skill_md, lazy=True, source_layer=layer_name)
            except ValueError:
                continue
            self._skills[skill.name] = skill

    def names(self) -> list[str]:
        return sorted(self._skills.keys())

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def descriptions_for_prompt(self) -> str:
        """Grouped skill listing (descriptions only) for the system prompt.

        All skills — including the core CODE-workflow chain — are listed here by
        description. Their full playbook bodies are loaded on-demand via
        activate_skill() (progressive disclosure, matching Claude Code's Agent
        Skills design). Grouping by WHEN a skill applies helps the model pick the
        right one at the right moment.
        """
        # Group skills by the role they play (mirrors Lion-Skills suite architecture).
        # The first group is the core CODE-workflow chain (the pipeline skeleton).
        groups = {
            "CODE 工作流主链（编码时按阶段激活）": [
                "clarifying-questions",
                "spec-writing",
                "task-breakdown",
                "executing-plans",
                "verify-and-fix",
                "commit-message",
            ],
            "CODE 执行段（写完代码后按需）": [
                "testing",
                "debugging",
                "code-review",
            ],
            "横切（任何阶段按需）": [
                "naming",
                "error-handling",
            ],
            "上手/元": [
                "onboarding-unknown-codebase",
                "lion-writing-skills",
            ],
        }
        lines = []
        listed = set()
        for label, names in groups.items():
            block = []
            for name in names:
                s = self._skills.get(name)
                if s is not None:
                    block.append(f"  - {name}: {s.description}")
                    listed.add(name)
            if block:
                lines.append(f"{label}:\n" + "\n".join(block))
        # Any skill not covered by a group (e.g. project-layer custom skills) goes
        # in a tail "其它" block so nothing is silently dropped.
        tail = []
        for name in self.names():
            if name in listed:
                continue
            s = self._skills[name]
            if s is not None:
                tail.append(f"  - {name}: {s.description}")
        if tail:
            lines.append("其它:\n" + "\n".join(tail))
        return "\n\n".join(lines)

    def has(self, name: str) -> bool:
        return name in self._skills


def load_skill_store(
    bundled_dir: Path | str | None,
    user_dir: Path | str | None,
    project_dir: Path | str | None,
) -> SkillStore:
    store = SkillStore()
    store._load_layer(Path(bundled_dir) if bundled_dir else None, "bundled")
    store._load_layer(Path(user_dir) if user_dir else None, "user")
    store._load_layer(Path(project_dir) if project_dir else None, "project")
    return store
