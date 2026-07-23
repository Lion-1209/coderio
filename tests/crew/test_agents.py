from pathlib import Path

from coderio.crew.agents import STAGE_FIELD, build_agent_roles

BUNDLED = Path(__file__).resolve().parents[2] / "src" / "coderio" / "skills"


def _store():
    from coderio.skills.store import load_skill_store

    return load_skill_store(BUNDLED, None, None)


def test_six_roles_in_order():
    roles = build_agent_roles(_store())
    assert [r.stage for r in roles] == [
        "clarify",
        "spec",
        "task",
        "execute",
        "verify",
        "commit",
    ]
    assert [r.name for r in roles] == [
        "Clarifier",
        "SpecWriter",
        "TaskPlanner",
        "Implementer",
        "Verifier",
        "Committer",
    ]


def test_clarifier_has_no_write_tools():
    """Physical isolation: Clarifier cannot write files or run bash."""
    roles = build_agent_roles(_store())
    clarifier = next(r for r in roles if r.stage == "clarify")
    tool_names = {t.name for t in clarifier.tools}
    assert "write_file" not in tool_names
    assert "edit_file" not in tool_names
    assert "bash" not in tool_names
    assert frozenset({"read_file", "grep", "glob"}).issubset(tool_names)


def test_implementer_has_all_tools():
    roles = build_agent_roles(_store())
    impl = next(r for r in roles if r.stage == "execute")
    tool_names = {t.name for t in impl.tools}
    assert frozenset({"bash", "grep", "glob", "write_file", "read_file", "edit_file"}).issubset(tool_names)


def test_verifier_cannot_write():
    roles = build_agent_roles(_store())
    verifier = next(r for r in roles if r.stage == "verify")
    tool_names = {t.name for t in verifier.tools}
    assert "write_file" not in tool_names
    assert "edit_file" not in tool_names
    assert "bash" in tool_names


def test_committer_only_git_read_and_bash():
    roles = build_agent_roles(_store())
    committer = next(r for r in roles if r.stage == "commit")
    tool_names = {t.name for t in committer.tools}
    assert "bash" in tool_names
    assert "read_file" in tool_names
    assert "write_file" not in tool_names


def test_human_pause_only_at_clarify_and_spec():
    roles = build_agent_roles(_store())
    pauses = {r.stage: r.human_pause for r in roles}
    assert pauses == {
        "clarify": True,
        "spec": True,
        "task": False,
        "execute": False,
        "verify": False,
        "commit": False,
    }


def test_reads_and_writes():
    roles = build_agent_roles(_store())
    by_stage = {r.stage: r for r in roles}
    assert by_stage["spec"].reads == ["request", "clarification"]
    assert by_stage["spec"].writes == "spec"
    assert by_stage["execute"].reads == ["spec", "task_list"]
    assert by_stage["execute"].writes == "implementation"
    assert by_stage["clarify"].writes == "clarification"


def test_stage_field_mapping():
    assert STAGE_FIELD["clarify"] == "clarification"
    assert STAGE_FIELD["spec"] == "spec"
    assert STAGE_FIELD["task"] == "task_list"
    assert STAGE_FIELD["execute"] == "implementation"
    assert STAGE_FIELD["verify"] == "verification"
    assert STAGE_FIELD["commit"] == "commit_message"


def test_role_skill_loaded():
    roles = build_agent_roles(_store())
    clarifier = next(r for r in roles if r.stage == "clarify")
    assert clarifier.skill_body
