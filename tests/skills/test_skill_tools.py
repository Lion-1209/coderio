"""Tests for skill-carried executable tools (tools.py convention).

A skill directory may contain a tools.py with a TOOLS list. When the skill is
activated, those tools are added to the agent's bound set. These tests cover
the loading, aggregation (ActiveSkills.active_tools), and edge cases.
"""

from __future__ import annotations

from pathlib import Path

from coderio.agent.prompts import ActiveSkills
from coderio.skills.models import Skill


def _make_skill(tmp_path: Path, name: str, tools_py: str | None = None) -> Skill:
    """Create a skill dir with SKILL.md and optional tools.py."""
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n# {name}\nbody",
        encoding="utf-8",
    )
    if tools_py is not None:
        (d / "tools.py").write_text(tools_py, encoding="utf-8")
    return Skill(name=name, description="test skill", dir_path=d)


_ECHO_TOOL_PY = """
from pydantic import BaseModel, Field

class EchoArgs(BaseModel):
    msg: str = Field(description="message")

class EchoTool:
    name = "echo"
    description = "Echo a message."
    args_schema = EchoArgs
    def run(self, msg):
        return f"echo: {msg}"

TOOLS = [EchoTool()]
"""


def test_skill_without_tools_returns_empty(tmp_path):
    skill = _make_skill(tmp_path, "plain")
    assert skill.load_tools() == []


def test_skill_with_tools_loads_them(tmp_path):
    skill = _make_skill(tmp_path, "echo-skill", tools_py=_ECHO_TOOL_PY)
    tools = skill.load_tools()
    assert len(tools) == 1
    assert tools[0].name == "echo"
    assert tools[0].run(msg="hi") == "echo: hi"


def test_load_tools_is_cached(tmp_path):
    skill = _make_skill(tmp_path, "echo-skill", tools_py=_ECHO_TOOL_PY)
    first = skill.load_tools()
    second = skill.load_tools()
    # Same tool instances (cached), not re-imported.
    assert first is not second  # returns a copy
    assert first[0] is second[0]  # same underlying object


def test_broken_tools_py_returns_empty(tmp_path):
    """A syntax error in tools.py must not crash — silently no tools."""
    skill = _make_skill(tmp_path, "broken", tools_py="this is not valid python !!!")
    assert skill.load_tools() == []


def test_active_skills_aggregates_tools(tmp_path):
    skill = _make_skill(tmp_path, "echo-skill", tools_py=_ECHO_TOOL_PY)
    active = ActiveSkills()
    assert active.active_tools() == []
    active.activate(skill)
    tools = active.active_tools()
    assert len(tools) == 1
    assert tools[0].name == "echo"


def test_deactivate_removes_tools(tmp_path):
    skill = _make_skill(tmp_path, "echo-skill", tools_py=_ECHO_TOOL_PY)
    active = ActiveSkills()
    active.activate(skill)
    assert len(active.active_tools()) == 1
    active.deactivate("echo-skill")
    assert active.active_tools() == []


def test_active_tools_deduplicates_by_name(tmp_path):
    """Two skills carrying tools with the same name → only first wins."""
    skill_a = _make_skill(tmp_path, "skill-a", tools_py=_ECHO_TOOL_PY)
    skill_b = _make_skill(tmp_path, "skill-b", tools_py=_ECHO_TOOL_PY)
    active = ActiveSkills()
    active.activate(skill_a)
    active.activate(skill_b)
    tools = active.active_tools()
    assert len(tools) == 1  # dedup, not 2
