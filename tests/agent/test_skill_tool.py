from pathlib import Path

from coderio.skills.store import SkillStore
from coderio.agent.skill_tool import ActivateSkillTool, ActiveSkills


def _make(tmp_path):
    d = tmp_path / "debugging"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: debugging\ndescription: fix bugs\n---\nDEBUG BODY", encoding="utf-8"
    )


def test_activate_skill(tmp_path):
    _make(tmp_path)
    store = SkillStore()
    store._load_layer(tmp_path, "user")
    active = ActiveSkills()
    tool = ActivateSkillTool(store, active)
    out = tool.run(name="debugging")
    assert "activated" in out.lower()
    assert active.is_active("debugging")


def test_activate_unknown(tmp_path):
    store = SkillStore()
    active = ActiveSkills()
    tool = ActivateSkillTool(store, active)
    out = tool.run(name="nope")
    assert "not found" in out.lower() or "error" in out.lower()
