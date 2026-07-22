from coderio.agent.prompts import build_system_prompt, ActiveSkills
from coderio.skills.store import SkillStore
from pathlib import Path


def _make(tmp_path, name, desc, body):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n{body}", encoding="utf-8"
    )


def test_prompt_lists_available_skills(tmp_path):
    _make(tmp_path, "debugging", "fix bugs", "DEBUG BODY")
    store = SkillStore()
    store._load_layer(tmp_path, "user")
    prompt = build_system_prompt(store, ActiveSkills())
    assert "debugging" in prompt
    assert "DEBUG BODY" not in prompt


def test_prompt_injects_active_body(tmp_path):
    _make(tmp_path, "debugging", "fix bugs", "DEBUG BODY")
    store = SkillStore()
    store._load_layer(tmp_path, "user")
    active = ActiveSkills()
    active.activate(store.get("debugging"))
    prompt = build_system_prompt(store, active)
    assert "DEBUG BODY" in prompt


def test_core_chain_bodies_not_injected_by_default(tmp_path):
    """Progressive disclosure: core-chain skill BODIES are NOT injected by default.
    Only descriptions are listed. The body loads on-demand via activate_skill().
    (Matches Claude Code's Agent Skills design — keeps the system prompt small.)"""
    _make(tmp_path, "clarifying-questions", "clarify", "CLARIFY BODY")
    _make(tmp_path, "spec-writing", "spec", "SPEC BODY")
    store = SkillStore()
    store._load_layer(tmp_path, "user")
    prompt = build_system_prompt(store, ActiveSkills())
    assert "CLARIFY BODY" not in prompt  # body NOT injected by default
    assert "SPEC BODY" not in prompt
    assert "clarify" in prompt  # but description IS listed
    assert "spec" in prompt


def test_activated_skill_body_is_injected(tmp_path):
    """When a skill is activated (via ActiveSkills), its body IS injected — that's
    the on-demand load path."""
    _make(tmp_path, "clarifying-questions", "clarify", "CLARIFY BODY")
    store = SkillStore()
    store._load_layer(tmp_path, "user")
    active = ActiveSkills()
    active.activate(store.get("clarifying-questions"))
    prompt = build_system_prompt(store, active)
    assert "CLARIFY BODY" in prompt  # activated → body present


def _empty_store():
    return SkillStore()


def test_prompt_has_intent_classification_layer():
    """The system prompt must tell the model to classify CODE vs QA vs ANALYZE
    before acting — this is the routing rule that prevents over-applying the
    coding workflow to simple questions (and under-applying it to code tasks)."""
    prompt = build_system_prompt(_empty_store(), ActiveSkills())
    low = prompt.lower()
    assert "classify the intent" in low
    for token in ("code", "qa", "analyze"):
        assert token in low


def test_prompt_codifies_qa_mode_guarantees():
    """QA mode must forbid file mutation and require concise grounded answers —
    the 'general agent' guarantee, not just a code agent."""
    prompt = build_system_prompt(_empty_store(), ActiveSkills())
    low = prompt.lower()
    assert "concise" in low
    checks = []
    for token in ("ground", "read"):
        assert token in low
    assert "speculation" in low
    assert "fact from" in low


def test_prompt_codifies_analyze_mode_requirements():
    """ANALYZE mode must require reading code first and presenting trade-offs
    (not dogmatic single takes)."""
    prompt = build_system_prompt(_empty_store(), ActiveSkills())
    low = prompt.lower()
    assert "evidence" in low
    assert any(token in low for token in ("trade-off", "tradeoffs", "alternatives"))


def test_prompt_keeps_unified_clarification_principle():
    """One clarification principle spanning both modes: ask when unsure, but the
    weight differs (light reply in QA vs structured skill in CODE)."""
    prompt = build_system_prompt(_empty_store(), ActiveSkills())
    low = prompt.lower()
    found = []
    for token in ("clarif", "both", "unified"):
        assert token in low


def test_prompt_marks_coding_workflow_as_code_mode_only():
    """The 6-step workflow must be framed as CODE-mode behavior, so the model
    knows NOT to run clarify→spec→task on a plain question."""
    prompt = build_system_prompt(_empty_store(), ActiveSkills())
    low = prompt.lower()
    assert "code mode" in low
    assert "explore first" in low or "verify" in low


# --- Lion-Skills 0.3.0 integration: execution-stage skills + grouped listing ---


def test_system_prompt_is_small_under_progressive_disclosure():
    """The system prompt must stay small: skill BODIES load on-demand, so the
    prompt should be descriptions + framework only — NOT the 27K it was when all
    6 core-chain bodies were injected. Guard against regressing to bulk injection.
    (~7-8K chars / ~2K tokens is the target; 15K is a hard ceiling.)"""
    prompt = build_system_prompt(_bundled_store(), ActiveSkills())
    assert len(prompt) < 15000, (
        f"System prompt is {len(prompt)} chars — too big. Skill bodies may have "
        "regressed to bulk injection instead of on-demand activate_skill."
    )


def _bundled_store():
    from coderio.skills.store import load_skill_store
    from pathlib import Path

    bundled = Path(__file__).resolve().parents[2] / "src" / "coderio" / "skills"
    return load_skill_store(bundled, None, None)


def test_execution_stage_skills_present_in_store():
    """The execution-stage skills (testing/debugging/code-review) must be present
    in the bundled store after the 0.3.0 update."""
    names = set(_bundled_store().names())
    for required in ("testing", "debugging", "code-review", "verify-and-fix"):
        assert required in names, f"{required} skill missing from bundled store"


def test_prompt_describes_execution_stage_skills():
    """The CODE workflow must mention the execution-stage skills (testing/
    debugging/code-review) so the model knows WHEN to activate them."""
    prompt = build_system_prompt(_bundled_store(), ActiveSkills())
    low = prompt.lower()
    assert "execution stage" in low
    for skill in ("testing", "debugging", "code-review"):
        assert skill in low


def test_skill_listing_groups_by_mode():
    """The skill listing must be GROUPED by when a skill applies (CODE workflow /
    execution stage / cross-cutting / onboarding), not a flat grab-bag. Under
    progressive disclosure, core-chain skills ARE listed (by description) — their
    bodies load on-demand."""
    desc = _bundled_store().descriptions_for_prompt()
    assert "工作流主链" in desc or "workflow" in desc.lower()
    assert "执行段" in desc or "execution" in desc.lower()
    assert "横切" in desc
    # Core-chain skills ARE listed by description now (bodies load on-demand)
    assert "clarifying-questions:" in desc
    assert "verify-and-fix:" in desc
    # And the execution-stage skills are listed too
    assert "testing:" in desc
    assert "debugging:" in desc


def test_skill_descriptions_are_trimmed():
    """Lion-Skills 0.3.0 trims descriptions to ~20-40 chars (was ~400). Verify the
    bundled copy is the 0.3.0 version, not the old verbose one."""
    vf = _bundled_store().get("verify-and-fix")
    assert vf is not None
    assert len(vf.description) < 60, (
        f"verify-and-fix description not trimmed (len={len(vf.description)}); "
        "expected Lion-Skills 0.3.0 (~26 chars)"
    )
