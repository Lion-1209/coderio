from pathlib import Path

from coderio.skills.parser import parse_skill_file

# Bundled skills ship inside the package at src/coderio/skills/ (the bundled layer
# used by load_skill_store): bundled/executing-plans/ + lion-skills/skills/*/.
BUNDLED = Path(__file__).resolve().parents[2] / "src" / "coderio" / "skills"


def test_executing_plans_parses():
    # Pick a core-chain skill that ships bundled and verify it parses cleanly.
    skill_md = BUNDLED / "lion-skills" / "skills" / "commit-message" / "SKILL.md"
    skill = parse_skill_file(skill_md, source_layer="bundled")
    assert skill.name == "commit-message"
    assert skill.description
    assert skill.body.strip()
