"""Plan artifact (.coderio/plan.md): serialize / parse / two-way sync, plus
HarnessMiddleware materialize-on-write_todos and run_deep_agent wiring pins.

S5 feature: the plan becomes an editable ARTIFACT — user edits between turns
win (adopted at turn start), agent updates mirror out after every write_todos.
"""

from __future__ import annotations

from types import SimpleNamespace

from coderio.agent.harness_middleware import HarnessMiddleware
from coderio.agent.plan_artifact import (
    AdoptionNote,
    PlanArtifact,
    parse_plan,
    serialize_plan,
)
from coderio.tools.todo import Todo, TodoStore

# --------------------------------------------------------------- serialize


def test_serialize_roundtrip_statuses_and_priority():
    todos = [
        Todo(content="first", status="pending", priority="high"),
        Todo(content="done deal", status="completed", priority="medium"),
        Todo(content="plain", status="pending", priority="medium"),
    ]
    text = serialize_plan(todos)

    assert "- [ ] first `(high)`" in text
    assert "- [x] done deal" in text  # medium priority omitted (noise control)
    parsed = parse_plan(text)

    assert parsed == todos


def test_parse_tolerates_prose_and_casing():
    text = (
        "# some title\n"
        "random prose the user wrote\n"
        "- [X] UPPERCASE DONE\n"
        "- [x] tagged `(low)`\n"
        "-   [ ]   spaced   out\n"
        "not an item\n"
    )

    parsed = parse_plan(text)

    assert parsed is not None
    assert [(t.content, t.status, t.priority) for t in parsed] == [
        ("UPPERCASE DONE", "completed", "medium"),
        ("tagged", "completed", "low"),
        ("spaced   out", "pending", "medium"),
    ]


def test_parse_returns_none_when_no_checklist_items():
    """Prose over the plan must NOT wipe a live task list on adoption."""
    assert parse_plan("just some notes\n- not a checklist\n* also not") is None


# ---------------------------------------------------------------- sync


def test_materialize_writes_then_skips_identical(tmp_path):
    store = TodoStore(todos=[Todo(content="task one")])
    art = PlanArtifact(anchor=tmp_path / ".coderio", store=store)

    assert art.materialize() is True
    written = (tmp_path / ".coderio" / "plan.md").read_text(encoding="utf-8")
    assert "- [ ] task one" in written

    # Identical content → no second write.
    assert art.materialize() is False


def test_adopt_user_edit_between_turns(tmp_path):
    store = TodoStore(todos=[Todo(content="original plan")])
    art = PlanArtifact(anchor=tmp_path / ".coderio", store=store)
    art.materialize()

    # User rewrites the plan between messages (drops a task, adds another).
    art.path.write_text(
        serialize_plan([Todo(content="user edited step"), Todo(content="extra", priority="high")]),
        encoding="utf-8",
    )
    adopted = art.adopt_if_edited()

    assert adopted == 2
    assert [t.content for t in store.todos] == ["user edited step", "extra"]
    assert store.todos[0].priority == "medium"


def test_adopt_is_noop_on_missing_unparseable_or_equal(tmp_path):
    store = TodoStore(todos=[Todo(content="keep me")])
    art = PlanArtifact(anchor=tmp_path / ".coderio", store=store)

    # Missing file.
    assert art.adopt_if_edited() == 0
    # Unparseable (no items).
    art.path.parent.mkdir(parents=True)
    art.path.write_text("prose only", encoding="utf-8")
    assert art.adopt_if_edited() == 0
    assert [t.content for t in store.todos] == ["keep me"]
    # Byte-equal content (agent just wrote it, no human touch).
    art.path.write_text(serialize_plan(store.todos), encoding="utf-8")
    assert art.adopt_if_edited() == 0


# ------------------------------------------- HarnessMiddleware integration


def _mw_request(name: str, args: dict):
    return SimpleNamespace(tool_call={"name": name, "args": args}, runtime=None)


def test_successful_write_todos_materializes_plan(tmp_path):
    store = TodoStore()
    art = PlanArtifact(anchor=tmp_path / ".coderio", store=store)
    mw = HarnessMiddleware(stream=None, enabled=True, todos=store, plan_artifact=art)

    mw.wrap_tool_call(
        _mw_request("write_todos", {"todos": [{"content": "step A", "status": "pending"}]}),
        lambda request: "ok",
    )

    on_disk = art.path.read_text(encoding="utf-8")
    assert "- [ ] step A" in on_disk


def test_failed_write_todos_does_not_materialize(tmp_path):
    """A failed tool means graph state wasn't updated — mirroring args would
    create a mismatch, so no sync AND no artifact write."""
    store = TodoStore()
    art = PlanArtifact(anchor=tmp_path / ".coderio", store=store)
    mw = HarnessMiddleware(stream=None, enabled=True, todos=store, plan_artifact=art)

    mw.wrap_tool_call(
        _mw_request("write_todos", {"todos": [{"content": "ghost", "status": "pending"}]}),
        lambda request: "Error: state update rejected",
    )

    assert not art.path.exists()


def test_subagent_middleware_has_no_artifact_by_default(tmp_path):
    """The plan has ONE owner: subagent HarnessMiddleware instances keep their
    own private store and never materialize anything."""
    mw = HarnessMiddleware(stream=None, enabled=True)

    mw.wrap_tool_call(
        _mw_request("write_todos", {"todos": [{"content": "sub", "status": "pending"}]}),
        lambda request: "ok",
    )

    assert mw.plan_artifact is None
    assert [t.content for t in mw.harness.todos.todos] == ["sub"]


# ------------------------------------------------------------- wiring pins


def test_run_deep_agent_anchor_walks_up_to_project_root():
    """SA-4 lesson pinned at source level: the plan anchor must use
    _find_project_dir (walk-up), NOT the literal runtime dir."""
    import inspect

    from coderio.agent import deep_loop

    src = inspect.getsource(deep_loop.run_deep_agent)
    assert '_find_project_dir(project_dir) / ".coderio"' in src, "plan artifact anchor must walk up to the project root"


def test_adoption_note_renders_model_visible_context():
    note = AdoptionNote(count=2, path=__import__("pathlib").Path("/p/.coderio/plan.md"))
    rendered = note.render()
    assert "modified externally" in rendered
    assert "2 task(s)" in rendered
