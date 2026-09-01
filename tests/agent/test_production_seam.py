"""SEAM tests: real create_deep_agent assembly drives the production write path.

These exist because the 2026-08-26 review found that every "production"
feature can be dead code while 959 tests stay green — unit tests called
coderio classes directly, bypassing the deepagents tool set that production
actually uses (the sixth seam-class incident in project history).

What these tests pin (each corresponds to a shipped-then-found-dead bug):

1. CHECKPOINT SEAM: deepagents' write_file/edit_file/delete (the tools the
   model actually calls) snapshot via the backend override → /undo restores.
   The fix lives in _WinLocalShellBackend._Sub, BELOW all tools, so every
   write path is covered regardless of which tool invoked it.

2. MULTI_EDIT SHAPE: pydantic validates `edits` into _SingleEdit objects
   before MultiEditTool.run sees them (via to_langchain_tool); the old
   .get()-on-object call crashed the whole turn with AttributeError.

3. WRITE_TODOS EXISTENCE: deepagents 0.7.6 removed TodoListMiddleware from
   the default graph — without re-adding it, write_todos is "not a valid
   tool" and the plan.md artifact's agent→file direction is dead.

4. PLAN SYNC STAMP: a pre-existing plan.md must NOT be "adopted" every turn
   (the false-positive "externally modified" note) — only genuine user edits
   (checklist content changes) trigger adoption.

All drive the REAL graph (scripted fake model → real middleware → real
backend → real disk), matching how TUI/headless invoke run_deep_agent.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

deepagents = pytest.importorskip("deepagents")

from coderio.tools.checkpoint import DEFAULT_CHECKPOINT  # noqa: E402


def _tc(name, args, mid="t1"):
    return {"name": name, "args": args, "id": mid, "type": "tool_call"}


def _run(tmp_path, msgs, **kw):
    """Drive run_deep_agent with the production tool set + fake model."""
    import sys

    sys.path.insert(0, "tests")
    from agent.conftest import NoOpStream, make_model, make_session

    from coderio.agent.deep_loop import TurnSpec, run_deep_agent
    from coderio.tools import build_default_tools

    harness = kw.pop("harness_enabled", False)
    prompt = kw.pop("prompt", "t")
    stream = NoOpStream()
    session = make_session(tmp_path)
    tools = build_default_tools("")
    run_deep_agent(
        prompt,
        TurnSpec(
            model=make_model(*msgs),
            harness_enabled=harness,
            workdir=str(tmp_path),
            tools=tools,
            **kw,
        ),
        session,
        stream=stream,
    )
    return stream, session


@pytest.fixture(autouse=True)
def _fresh_checkpoint():
    DEFAULT_CHECKPOINT.clear()
    yield
    DEFAULT_CHECKPOINT.clear()


# ----------------------------------------------------- 1. checkpoint seam


def test_production_write_file_snapshots_and_undo_restores(tmp_path):
    """deepagents' write_file (the one the model calls) must snapshot via the
    backend override; /undo restores the prior bytes. This is the P0 from the
    2026-08-26 review: 318 lines of checkpoint tests were green while every
    production write bypassed the checkpoint entirely."""
    target = tmp_path / "target.txt"
    target.write_text("SEED", encoding="utf-8")
    stream, _ = _run(
        tmp_path,
        [
            AIMessage(content="", tool_calls=[_tc("write_file", {"file_path": "/target.txt", "content": "AGENT"})]),
            AIMessage(content="done"),
        ],
    )
    assert len(DEFAULT_CHECKPOINT) > 0, "production write_file must snapshot"
    assert target.read_text(encoding="utf-8") == "AGENT", "the tool must actually write"
    r = DEFAULT_CHECKPOINT.undo()
    assert r is not None and r.restored, "undo must report a real restore"
    assert target.read_text(encoding="utf-8") == "SEED", "undo must restore the pre-write bytes"


def test_production_edit_file_snapshots_and_undo_restores(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("ORIGINAL", encoding="utf-8")
    stream, _ = _run(
        tmp_path,
        [
            AIMessage(
                content="",
                tool_calls=[
                    _tc(
                        "edit_file",
                        {
                            "file_path": "/target.txt",
                            "old_string": "ORIGINAL",
                            "new_string": "EDITED",
                        },
                    )
                ],
            ),
            AIMessage(content="done"),
        ],
    )
    assert len(DEFAULT_CHECKPOINT) > 0, "production edit_file must snapshot"
    assert target.read_text(encoding="utf-8") == "EDITED"
    r = DEFAULT_CHECKPOINT.undo()
    assert r is not None
    assert target.read_text(encoding="utf-8") == "ORIGINAL"


def test_production_delete_snapshots_and_undo_restores(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("KEEP ME", encoding="utf-8")
    stream, _ = _run(
        tmp_path,
        [
            AIMessage(content="", tool_calls=[_tc("delete", {"file_path": "/target.txt"})]),
            AIMessage(content="done"),
        ],
    )
    assert len(DEFAULT_CHECKPOINT) > 0, "production delete must snapshot"
    assert not target.exists()
    r = DEFAULT_CHECKPOINT.undo()
    assert r is not None
    assert target.read_text(encoding="utf-8") == "KEEP ME"


def test_production_delete_directory_is_not_checkpointed(tmp_path):
    """deepagents' delete rmtree's directories; the checkpoint can only hold
    single-file bytes. A dir snapshot would record existed=False and /undo
    would report 'deleted agent-created file' while NOTHING was restored
    (2026-08-27 review R3). The honest behavior: no snapshot, empty stack."""
    d = tmp_path / "subdir"
    d.mkdir()
    (d / "f.txt").write_text("X", encoding="utf-8")
    stream, _ = _run(
        tmp_path,
        [
            AIMessage(content="", tool_calls=[_tc("delete", {"file_path": "/subdir"})]),
            AIMessage(content="done"),
        ],
    )
    assert not d.exists(), "delete must rmtree the directory"
    assert len(DEFAULT_CHECKPOINT) == 0, "directory rmtree is unrecoverable — no lying snapshot"
    assert DEFAULT_CHECKPOINT.undo() is None


def test_production_internal_offload_paths_skip_checkpoint(tmp_path):
    """deepagents offloads oversized tool results / conversation history to
    /large_tool_results/ and /conversation_history/ THROUGH backend.write.
    Snapshotting those poisons /undo: the next undo deletes an offload file
    the conversation still references while real user damage stays put
    (2026-08-27 review R2, reproduced with damage→big-output→/undo)."""
    stream, _ = _run(
        tmp_path,
        [
            AIMessage(
                content="",
                tool_calls=[_tc("write_file", {"file_path": "/large_tool_results/probe.txt", "content": "offload"})],
            ),
            AIMessage(content="done"),
        ],
    )
    offloaded = tmp_path / "large_tool_results" / "probe.txt"
    assert offloaded.read_text(encoding="utf-8") == "offload", "the offload path must still write normally"
    assert len(DEFAULT_CHECKPOINT) == 0, "internal offload writes must not enter the /undo stack"


def test_production_failed_edit_leaves_no_ghost_snapshot(tmp_path):
    """A failed edit (old_string absent) changes nothing on disk. deepagents
    reports the failure as a result OBJECT, not an exception — the backend
    hook must check the result and drop the snapshot, else the next /undo is
    a false 'restored' no-op while real damage stays put (2026-08-27 Y1)."""
    target = tmp_path / "t.txt"
    target.write_text("KEEP", encoding="utf-8")
    stream, _ = _run(
        tmp_path,
        [
            AIMessage(
                content="",
                tool_calls=[_tc("edit_file", {"file_path": "/t.txt", "old_string": "ABSENT", "new_string": "X"})],
            ),
            AIMessage(content="done"),
        ],
    )
    assert target.read_text(encoding="utf-8") == "KEEP", "the failed edit must not change the file"
    assert len(DEFAULT_CHECKPOINT) == 0, "a write that never landed must not leave a ghost snapshot"


# ----------------------------------------------------- 2. multi_edit shape


def test_production_multi_edit_with_objects_no_crash(tmp_path):
    """The model's multi_edit call goes through pydantic validation (edits
    become _SingleEdit objects) then MultiEditTool.run — the old dict-only
    .get() crashed the turn. P1 from the 2026-08-26 review."""
    target = tmp_path / "m.txt"
    target.write_text("AAA BBB CCC", encoding="utf-8")
    real = str(target)
    stream, _ = _run(
        tmp_path,
        [
            AIMessage(
                content="",
                tool_calls=[
                    _tc(
                        "multi_edit",
                        {
                            "path": real,
                            "edits": [
                                {"old_string": "AAA", "new_string": "XXX"},
                                {"old_string": "BBB", "new_string": "YYY"},
                            ],
                        },
                    )
                ],
            ),
            AIMessage(content="done"),
        ],
    )
    assert target.read_text(encoding="utf-8") == "XXX YYY CCC"
    # ONE snapshot per multi_edit run → ONE undo reverts the whole batch.
    assert len(DEFAULT_CHECKPOINT) == 1
    r = DEFAULT_CHECKPOINT.undo()
    assert r is not None
    assert target.read_text(encoding="utf-8") == "AAA BBB CCC"


# ----------------------------------------------------- 3. write_todos existence


def test_write_todos_is_a_valid_production_tool(tmp_path):
    """deepagents 0.7.6 removed TodoListMiddleware from the default graph;
    coderio re-adds it. Without it the model's write_todos fails and the
    plan.md artifact's agent→file direction is dead."""
    stream, session = _run(
        tmp_path,
        [
            AIMessage(
                content="",
                tool_calls=[
                    _tc(
                        "write_todos",
                        {
                            "todos": [{"content": "task A", "status": "pending"}],
                        },
                    )
                ],
            ),
            AIMessage(content="done"),
        ],
        harness_enabled=True,
        prompt="plan something",
    )
    invalid = [r for _, r in stream.tool_ends if "not a valid tool" in str(r)]
    assert not invalid, f"write_todos must be valid in production: {invalid}"


def test_write_todos_in_progress_stamp_roundtrips(tmp_path):
    """Cross-boundary R1 pin: the REAL graph (write_todos → HarnessMiddleware
    mirror → materialize) must produce a plan.md whose stamp matches what the
    next turn's fresh store parses back — including langchain's forced
    in_progress on the first task (todo.py tool description)."""
    from coderio.agent.plan_artifact import PlanArtifact
    from coderio.tools.todo import TodoStore

    stream, _ = _run(
        tmp_path,
        [
            AIMessage(
                content="",
                tool_calls=[_tc("write_todos", {"todos": [{"content": "task A", "status": "in_progress"}]})],
            ),
            AIMessage(content="done"),
        ],
        harness_enabled=True,
        prompt="plan something",
    )
    plan = tmp_path / ".coderio" / "plan.md"
    assert plan.is_file(), "write_todos must materialize plan.md (cross-boundary pin)"
    fresh = PlanArtifact(anchor=tmp_path / ".coderio", store=TodoStore())
    assert fresh.adopt_if_edited() == 0, "in_progress from the real graph must not false-adopt"


# ----------------------------------------------------- 4. plan sync stamp


def test_plan_stamp_prevents_false_positive_adoption(tmp_path):
    """A plan.md that nobody edited since materialize() wrote it must NOT be
    reported as externally modified on the next turn (the sync stamp matches
    → store backfills silently, adoption count stays 0)."""
    from coderio.agent.plan_artifact import PlanArtifact
    from coderio.tools.todo import Todo, TodoStore

    anchor = tmp_path / ".coderio"
    anchor.mkdir(exist_ok=True)

    store1 = TodoStore()
    store1.todos = [Todo(content="task A", status="pending")]
    art = PlanArtifact(anchor=anchor, store=store1)
    assert art.materialize() is True

    # Fresh store (what the next turn constructs): stamp matches → no adoption
    store2 = TodoStore()
    art2 = PlanArtifact(anchor=anchor, store=store2)
    assert art2.adopt_if_edited() == 0, "un-edited plan must not trigger adoption"
    # But the store IS backfilled (harness state warm for this turn).
    assert any(t.content == "task A" for t in store2.todos)


def test_plan_user_edit_of_checklist_triggers_adoption(tmp_path):
    from coderio.agent.plan_artifact import PlanArtifact
    from coderio.tools.todo import Todo, TodoStore

    anchor = tmp_path / ".coderio"
    anchor.mkdir(exist_ok=True)
    store1 = TodoStore()
    store1.todos = [Todo(content="task A", status="pending")]
    art = PlanArtifact(anchor=anchor, store=store1)
    art.materialize()

    plan_file = anchor / "plan.md"
    edited = plan_file.read_text(encoding="utf-8").replace("task A", "task A USER EDIT")
    plan_file.write_text(edited, encoding="utf-8")

    store2 = TodoStore()
    art2 = PlanArtifact(anchor=anchor, store=store2)
    n = art2.adopt_if_edited()
    assert n > 0, "genuine checklist edit must be adopted"
    assert any("USER EDIT" in t.content for t in store2.todos)


def test_plan_whitespace_reformat_is_not_an_edit(tmp_path):
    """Reformatting (whitespace/newline churn that keeps the same checklist
    items) keeps the items hash → correctly NOT an external edit."""
    from coderio.agent.plan_artifact import PlanArtifact
    from coderio.tools.todo import Todo, TodoStore

    anchor = tmp_path / ".coderio"
    anchor.mkdir(exist_ok=True)
    store1 = TodoStore()
    store1.todos = [Todo(content="task A", status="pending")]
    PlanArtifact(anchor=anchor, store=store1).materialize()

    plan_file = anchor / "plan.md"
    text = plan_file.read_text(encoding="utf-8")
    plan_file.write_text(text.replace("- [ ]", "- [ ]  "), encoding="utf-8")  # extra spaces

    store2 = TodoStore()
    art2 = PlanArtifact(anchor=anchor, store=store2)
    assert art2.adopt_if_edited() == 0, "whitespace-only reformat is not a user edit"


def test_plan_stamp_survives_in_progress_roundtrip(tmp_path):
    """R1 killer (2026-08-27 seam test): langchain's write_todos forces
    status=in_progress on the first task — the MOST COMMON production todo
    state. The checkbox format can only express pending/completed, so the
    hash must be computed over the serialization ROUND-TRIP domain, else the
    stamp never matches and a false 'externally modified' adoption fires on
    every turn (with zero user edits)."""
    from coderio.agent.plan_artifact import PlanArtifact
    from coderio.tools.todo import Todo, TodoStore

    anchor = tmp_path / ".coderio"
    anchor.mkdir(exist_ok=True)

    store1 = TodoStore()
    store1.todos = [
        Todo(content="task A", status="in_progress"),
        Todo(content="task B", status="pending"),
    ]
    PlanArtifact(anchor=anchor, store=store1).materialize()

    # What the NEXT turn does: a fresh-empty store reads the plan back.
    store2 = TodoStore()
    assert PlanArtifact(anchor=anchor, store=store2).adopt_if_edited() == 0, (
        "in_progress must round-trip to the same stamp — no false adoption"
    )
    # Fresh store is still warmed (backfilled from the file).
    assert [t.content for t in store2.todos] == ["task A", "task B"]

    # A NON-empty store keeps its in_progress — backfilling from the file
    # would downgrade statuses the checkbox can't express.
    store3 = TodoStore()
    store3.todos = [Todo(content="task A", status="in_progress"), Todo(content="task B", status="pending")]
    art3 = PlanArtifact(anchor=anchor, store=store3)
    assert art3.adopt_if_edited() == 0
    assert store3.todos[0].status == "in_progress", "stamp-match must not downgrade a warm store"


def test_plan_adoption_signal_survives_after_model_state_sync(tmp_path):
    """Y2 (2026-08-27 adversarial review): re-adding TodoListMiddleware made
    graph-state todos non-empty again; HarnessMiddleware.after_model's
    state→store sync would clobber the plan the user JUST edited via plan.md
    (adoption at turn start) with the stale checkpointed state todos. The
    adoption must win: after_model pushes the adopted plan INTO state."""
    from coderio.agent.harness_middleware import HarnessMiddleware
    from coderio.agent.plan_artifact import PlanArtifact
    from coderio.tools.todo import Todo, TodoStore

    # Production setup: materialize a plan, then the user edits its checklist.
    anchor = tmp_path / ".coderio"
    store1 = TodoStore()
    store1.todos = [Todo(content="stale state plan", status="pending")]
    art1 = PlanArtifact(anchor=anchor, store=store1)
    art1.materialize()
    plan_file = anchor / "plan.md"
    plan_file.write_text(
        plan_file.read_text(encoding="utf-8").replace("stale state plan", "user edited plan"),
        encoding="utf-8",
    )

    # What run_deep_agent does at the next turn start: fresh store + adopt.
    store3 = TodoStore()
    art3 = PlanArtifact(anchor=anchor, store=store3)
    assert art3.adopt_if_edited() > 0, "setup: the user edit must be adopted"

    mw = HarnessMiddleware(enabled=False, todos=store3, plan_artifact=art3)
    state = {
        "messages": [AIMessage(content="done")],
        "todos": [{"content": "stale state plan", "status": "pending"}],
    }
    ret = mw.after_model(state, None)
    # The adopted plan is pushed into graph state…
    assert ret is not None and ret.get("todos") == [{"content": "user edited plan", "status": "pending"}]
    # …and the store was NOT overwritten by the stale state todos.
    assert store3.todos[0].content == "user edited plan"


def test_production_multi_edit_relative_path_uses_workspace_anchor(tmp_path):
    """Seam T4 pin: inside the REAL graph, deepagents write_file anchors
    relative paths at the workspace root while multi_edit anchored at process
    cwd — when they differ the same input went to two different files. The
    model's relative-path multi_edit must land in the workspace."""
    target = tmp_path / "rel.txt"
    target.write_text("ONE TWO", encoding="utf-8")
    stream, _ = _run(
        tmp_path,
        [
            AIMessage(
                content="",
                tool_calls=[
                    _tc(
                        "multi_edit",
                        {"path": "rel.txt", "edits": [{"old_string": "ONE", "new_string": "1"}]},
                    )
                ],
            ),
            AIMessage(content="done"),
        ],
    )
    assert target.read_text(encoding="utf-8") == "1 TWO", (
        "relative multi_edit must edit the file under the WORKSPACE "
        "(pytest cwd is the repo root — exactly the drift this pins)"
    )
