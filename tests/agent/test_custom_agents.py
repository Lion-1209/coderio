"""Custom subagents (.coderio/agents/*.md): discovery + read-only spec assembly.

S2 feature. Security invariant under test throughout: a discovered file
customizes WHO the agent pretends to be (name/description/system prompt),
never WHAT it can do — every assembled spec carries the same PLAN-gated
read-only middleware stack as the built-in research agent.
"""

from __future__ import annotations

from pathlib import Path

from coderio.agent.custom_agents import (
    CustomAgent,
    discover_custom_agents,
)
from coderio.agent.deep_loop import (
    _build_custom_subagent,
    _build_research_subagent,
    _drop_trusted_name_collisions,
)


def _make_agent(dir_path: Path, name: str, content: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{name}.md").write_text(content, encoding="utf-8")


# --------------------------------------------------------------- discovery


def test_discover_finds_both_layers_with_frontmatter(tmp_path):
    user_dir = tmp_path / "user"
    proj_dir = tmp_path / "proj"
    _make_agent(user_dir, "trend", "Find trending patterns.")
    _make_agent(proj_dir, "sec", "---\ndescription: security review\n---\nAudit for XSS.")

    cmds = discover_custom_agents(project_dir=proj_dir, user_dir=user_dir)

    assert set(cmds) == {"trend", "sec"}
    assert cmds["sec"].description == "security review"
    assert cmds["sec"].system_prompt == "Audit for XSS."
    assert cmds["sec"].source_layer == "project"


def test_project_overrides_user_on_conflict(tmp_path):
    user_dir = tmp_path / "user"
    proj_dir = tmp_path / "proj"
    _make_agent(user_dir, "audit", "USER")
    _make_agent(proj_dir, "audit", "PROJECT")

    cmds = discover_custom_agents(project_dir=proj_dir, user_dir=user_dir)

    assert len(cmds) == 1
    assert cmds["audit"].system_prompt == "PROJECT"
    assert cmds["audit"].source_layer == "project"


def test_reserved_names_dropped_exact_and_case(tmp_path):
    """research.md / GENERAL-PURPOSE.md must never shadow trusted types."""
    d = tmp_path / "proj"
    _make_agent(d, "research", "EVIL")
    _make_agent(d, "RESEARCH", "EVIL")
    _make_agent(d, "general-purpose", "EVIL")
    _make_agent(d, "General-Purpose", "EVIL")
    _make_agent(d, "legit", "fine")

    cmds = discover_custom_agents(project_dir=d)

    assert set(cmds) == {"legit"}


def test_real_layout_anchor_not_project_root(tmp_path):
    """REGRESSION GUARD (S1 runtime-audit pattern): callers pass
    ``<project>/.coderio/agents`` — the LAYER dir. Root-level *.md decoys
    (README etc.) must not become agent definitions."""
    project = tmp_path / "project"
    sub = project / "pkg" / "deep"
    sub.mkdir(parents=True)
    (project / ".coderio").mkdir()
    (project / ".coderio" / "config.toml").write_text("[tools]\n", encoding="utf-8")
    _make_agent(project / ".coderio" / "agents", "reviewer", "Review things.")
    (project / "README.md").write_text("# decoy", encoding="utf-8")

    # Mirror deep_loop.py's join exactly.
    from coderio.config.loader import _find_project_dir

    layer = _find_project_dir(sub) / ".coderio" / "agents"

    cmds = discover_custom_agents(project_dir=layer)

    assert set(cmds) == {"reviewer"}


def test_hygiene_whitespace_nul_oversize_empty_skipped(tmp_path):
    d = tmp_path / "proj"
    _make_agent(d, "my agent", "spaced name is unreachable via task()")
    big = d / "big.md"
    d.mkdir(exist_ok=True)
    big.write_text("x" * (129 * 1024), encoding="utf-8")
    _make_agent(d, "nully", "PWN\x00 evil")
    _make_agent(d, "empty", "   ")

    cmds = discover_custom_agents(project_dir=d)

    assert set(cmds) == {"nully"}
    assert "\x00" not in cmds["nully"].system_prompt


# ------------------------------------------------------------ spec assembly


def test_built_custom_spec_carries_readonly_stack():
    ca = CustomAgent(
        name="sec",
        description="security review",
        system_prompt="Audit for XSS.",
        source_layer="project",
    )
    spec = _build_custom_subagent(ca)

    assert spec["name"] == "sec"
    assert spec["description"] == "security review"
    assert spec["system_prompt"] == "Audit for XSS."
    mw_names = [type(m).__name__ for m in spec["middleware"]]
    assert "PermissionMiddleware" in mw_names, mw_names
    assert "CommandReviewMiddleware" in mw_names, mw_names
    perm = next(m for m in spec["middleware"] if type(m).__name__ == "PermissionMiddleware")
    assert perm.gate.mode == "plan", f"custom agent must be PLAN, got {perm.gate.mode}"


def test_custom_stack_identical_to_research_stack():
    """The core S2 invariant: a custom definition gets EXACTLY the same
    enforcement stack as the built-in research agent — same classes, same
    order, same PLAN gate. Persona differs; power does not."""
    ca = CustomAgent("x", "", "prompt", "user")
    research = _build_research_subagent()
    custom = _build_custom_subagent(ca)

    r_types = [type(m).__name__ for m in research["middleware"]]
    c_types = [type(m).__name__ for m in custom["middleware"]]
    assert r_types == c_types


def test_description_fallback_when_frontmatter_absent():
    ca = CustomAgent("helper", "", "Do helper things.", "user")
    spec = _build_custom_subagent(ca)

    assert ".coderio/agents/helper.md" in spec["description"]
    assert "user" in spec["description"]


# --------------------------------------------- adversarial-review regressions


def test_description_truncated_and_control_chars_stripped(tmp_path):
    """ADVERSARIAL REGRESSION: description flows into the task() tool spec the
    MAIN model sees every turn — an unbounded value let a repo file inflate or
    poison that context (90KB flowed through verbatim). Now truncated hard and
    control-char-free at discovery."""
    d = tmp_path / "proj"
    huge = "A" * 5000 + "\x07" + "B" * 10
    _make_agent(d, "flood", f"---\ndescription: {huge}\n---\nprompt")

    from coderio.skills.parser import MAX_DESCRIPTION_CHARS

    cmds = discover_custom_agents(project_dir=d)

    desc = cmds["flood"].description
    assert len(desc) <= MAX_DESCRIPTION_CHARS
    assert "\x07" not in desc


def test_wiring_filter_drops_trusted_name_collisions_last_line():
    """ADVERSARIAL REGRESSION: deepagents builds {name: spec} LAST-WINS and
    custom specs sit at the END of the list — if discovery's reserved-name
    drop regressed, a repo research.md would silently REPLACE the trusted
    spec. The wiring-time filter is the second line of defense."""
    trusted = [_build_research_subagent(), _build_research_subagent()]
    trusted[0]["name"] = "research"
    trusted[1]["name"] = "general-purpose"
    evil = [{"name": "research", "description": "", "system_prompt": "EVIL"}]
    ok = [{"name": "legit", "description": "", "system_prompt": "fine"}]

    kept = _drop_trusted_name_collisions(evil + ok, trusted)

    assert [s["name"] for s in kept] == ["legit"]
    # Case variants too (mirrors the discovery-layer rule).
    casey = [{"name": "RESEARCH", "description": "", "system_prompt": "EVIL"}]
    assert _drop_trusted_name_collisions(casey, trusted) == []
