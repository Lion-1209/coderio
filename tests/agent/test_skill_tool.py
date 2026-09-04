from coderio.agent.skill_tool import (
    ActivateSkillTool,
    ActiveSkills,
    DeactivateSkillTool,
)
from coderio.skills.store import SkillStore


def _make(tmp_path):
    d = tmp_path / "debugging"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("---\nname: debugging\ndescription: fix bugs\n---\nDEBUG BODY", encoding="utf-8")


def test_activate_skill(tmp_path):
    _make(tmp_path)
    store = SkillStore()
    store._load_layer(tmp_path, "user")
    active = ActiveSkills()
    tool = ActivateSkillTool(store, active)
    out = tool.run(name="debugging")
    assert "activated" in out.lower()
    assert active.is_active("debugging")


def test_activate_skill_returns_body_same_turn(tmp_path):
    """P1-9 (2026-09-04): the playbook body must be IN the tool result so the
    model can follow it the same turn. The old status-only string relied on an
    on_activate_skill callback that never existed — the body only reached the
    system prompt on the NEXT turn."""
    _make(tmp_path)
    store = SkillStore()
    store._load_layer(tmp_path, "user")
    active = ActiveSkills()
    out = ActivateSkillTool(store, active).run(name="debugging")
    assert "DEBUG BODY" in out, "the skill body must be returned with the activation result"
    # Re-activation also returns the body (adversarial review note): after
    # context compaction the playbook may have left the window while `active`
    # still lists it — "already active" alone would leave it unrecoverable.
    out2 = ActivateSkillTool(store, active).run(name="debugging")
    assert "already active" in out2.lower()
    assert "DEBUG BODY" in out2, "re-activation must re-supply the playbook body"


def test_activate_unknown(tmp_path):
    store = SkillStore()
    active = ActiveSkills()
    tool = ActivateSkillTool(store, active)
    out = tool.run(name="nope")
    assert "not found" in out.lower() or "error" in out.lower()


def test_deactivate_skill(tmp_path):
    """DeactivateSkillTool removes an active skill (frees its context budget)."""
    _make(tmp_path)
    store = SkillStore()
    store._load_layer(tmp_path, "user")
    active = ActiveSkills()
    # Activate first so there's something to deactivate.
    ActivateSkillTool(store, active).run(name="debugging")
    assert active.is_active("debugging")
    # Now deactivate.
    tool = DeactivateSkillTool(active)
    out = tool.run(name="debugging")
    assert "deactivated" in out.lower()
    assert not active.is_active("debugging")


def test_deactivate_not_active(tmp_path):
    """Deactivating a skill that isn't active reports an error, not a crash."""
    active = ActiveSkills()
    tool = DeactivateSkillTool(active)
    out = tool.run(name="debugging")
    assert "not active" in out.lower() or "error" in out.lower()


def test_deactivate_lists_active_skills_on_error(tmp_path):
    """When deactivating an unknown name, the error lists what IS active — so
    the model can correct itself instead of guessing."""
    _make(tmp_path)
    store = SkillStore()
    store._load_layer(tmp_path, "user")
    active = ActiveSkills()
    ActivateSkillTool(store, active).run(name="debugging")
    tool = DeactivateSkillTool(active)
    out = tool.run(name="nope")
    assert "debugging" in out  # the actually-active skill is named
