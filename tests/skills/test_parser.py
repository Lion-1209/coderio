from pathlib import Path

import pytest

from coderio.skills.parser import parse_skill_file
from coderio.skills.models import Skill


def write_skill(d, name, body, desc="a skill"):
    p = d / name / "SKILL.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}", encoding="utf-8")
    return p


def test_parse_basic(tmp_path):
    p = write_skill(tmp_path, "debugging", "# Debugging\nStep 1...", desc="a skill")
    skill = parse_skill_file(p)
    assert skill.name == "debugging"
    assert skill.description == "a skill"
    assert "Step 1" in skill.body


def test_parse_lazy_body(tmp_path):
    p = write_skill(tmp_path, "x", "BODY")
    skill = parse_skill_file(p, lazy=True)
    assert skill.name == "x"
    assert skill._loaded is False
    skill.load_body()
    assert "BODY" in skill.body


def test_parse_missing_name_raises(tmp_path):
    d = tmp_path / "bad"
    d.mkdir(parents=True)
    p = d / "SKILL.md"
    p.write_text("---\ndescription: no name\n---\nbody", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        parse_skill_file(p)
    assert "name" in str(e).lower()


def test_no_frontmatter(tmp_path):
    d = tmp_path / "nf"
    d.mkdir(parents=True)
    p = d / "SKILL.md"
    p.write_text("just body, no frontmatter", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_skill_file(p)
