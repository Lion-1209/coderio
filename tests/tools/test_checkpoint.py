"""File-write checkpoints + /undo — S4 feature tests.

Covers the checkpoint stack semantics (LIFO, caps, created-vs-modified), the
integration into all three structured write tools (snapshot only on real write
paths), and the /undo slash command wiring.
"""

from __future__ import annotations

import pytest

from coderio.cli.commands import CommandResult, ReplContext, handle_slash
from coderio.tools.checkpoint import DEFAULT_CHECKPOINT, MAX_ENTRIES, FileCheckpoint
from coderio.tools.edit_file import EditFileTool
from coderio.tools.multi_edit import MultiEditTool
from coderio.tools.write_file import WriteFileTool


@pytest.fixture(autouse=True)
def _clean_singleton():
    """Isolate every test from the process-global default checkpoint."""
    DEFAULT_CHECKPOINT.clear()
    yield
    DEFAULT_CHECKPOINT.clear()


# --------------------------------------------------------------- stack core


def test_roundtrip_restores_prior_content(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("original", encoding="utf-8")

    cp = FileCheckpoint()
    cp.snapshot(f)
    f.write_text("modified by agent", encoding="utf-8")

    result = cp.undo()

    assert result is not None and result.restored
    assert f.read_text(encoding="utf-8") == "original"


def test_undo_deletes_agent_created_file(tmp_path):
    f = tmp_path / "new.txt"

    cp = FileCheckpoint()
    cp.snapshot(f)  # did not exist yet
    f.write_text("created", encoding="utf-8")

    result = cp.undo()

    assert result is not None and not result.restored
    assert not f.exists()


def test_lifo_across_multiple_files(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("A0", encoding="utf-8")
    b.write_text("B0", encoding="utf-8")

    cp = FileCheckpoint()
    cp.snapshot(a)
    a.write_text("A1", encoding="utf-8")
    cp.snapshot(b)
    b.write_text("B1", encoding="utf-8")

    r1 = cp.undo()
    r2 = cp.undo()

    assert r1.path == b and b.read_text(encoding="utf-8") == "B0"
    assert r2.path == a and a.read_text(encoding="utf-8") == "A0"


def test_empty_stack_undo_returns_none():
    assert FileCheckpoint().undo() is None


def test_entry_cap_evicts_oldest(tmp_path):
    cp = FileCheckpoint(max_entries=3)
    files = []
    for i in range(5):
        f = tmp_path / f"f{i}.txt"
        f.write_text(str(i), encoding="utf-8")
        cp.snapshot(f)
        files.append(f)

    assert len(cp) == 3  # f0/f1 evicted
    # Undoing reaches only f2..f4.
    for expected in ("f4", "f3", "f2"):
        r = cp.undo()
        assert r.path.stem == expected


def test_byte_budget_evicts_oldest(tmp_path):
    cp = FileCheckpoint(max_total_bytes=100)
    big1 = tmp_path / "big1.bin"
    big2 = tmp_path / "big2.bin"
    big1.write_bytes(b"x" * 80)
    big2.write_bytes(b"y" * 80)
    cp.snapshot(big1)
    cp.snapshot(big2)

    assert len(cp) == 1  # big1 evicted by budget
    assert cp.undo().path == big2


def test_snapshot_of_missing_path_records_creation_marker(tmp_path):
    """A snapshot of a not-yet-existing path is MEANINGFUL, not noise: when the
    agent goes on to create that file, /undo must delete it."""
    missing = tmp_path / "brand-new.txt"
    cp = FileCheckpoint()
    cp.snapshot(missing)
    assert len(cp) == 1

    missing.write_text("created", encoding="utf-8")
    r = cp.undo()

    assert not r.restored and not missing.exists()


def test_read_error_snapshot_is_noop(tmp_path, monkeypatch):
    """is_file()=True but read_bytes raises (AV lock / permission race) —
    skip rather than store a corrupt 'empty' prior state."""
    import coderio.tools.checkpoint as ck

    f = tmp_path / "locked.bin"
    f.write_bytes(b"data")
    monkeypatch.setattr(ck.Path, "read_bytes", lambda self: (_ for _ in ()).throw(OSError("locked")))

    cp = FileCheckpoint()
    cp.snapshot(f)

    assert len(cp) == 0


def test_undo_oserror_repushes_for_retry(tmp_path):
    """A transient failure (AV lock etc.) must NOT lose undo depth — the
    snapshot goes back on the stack so a later /undo can retry."""
    f = tmp_path / "locked.txt"
    f.write_text("data", encoding="utf-8")
    cp = FileCheckpoint()
    cp.snapshot(f)

    original_write_bytes = Path_write_bytes = None  # noqa: F841 — clarity only

    import pathlib

    real_write_bytes = pathlib.Path.write_bytes

    def broken(self, data):
        raise OSError("transient lock")

    monkeyed = False
    try:
        pathlib.Path.write_bytes = broken
        with pytest.raises(OSError):
            cp.undo()
        monkeyed = True
    finally:
        pathlib.Path.write_bytes = real_write_bytes

    assert monkeyed
    assert len(cp) == 1, "failed undo lost its snapshot"
    # Retry after the lock clears succeeds.
    assert cp.undo().restored
    assert f.read_text(encoding="utf-8") == "data"


# ----------------------------------------------------- tool integration


def test_write_file_checkpoints(tmp_path):
    f = tmp_path / "w.txt"
    f.write_text("old", encoding="utf-8")

    WriteFileTool().run(str(f), "new content")

    assert DEFAULT_CHECKPOINT.undo().restored
    assert f.read_text(encoding="utf-8") == "old"


def test_edit_file_checkpoints_but_not_on_error_paths(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("hello world", encoding="utf-8")

    # Error paths must NOT consume checkpoint depth.
    assert "not found in" in EditFileTool().run(str(f), "nope", "x")
    assert "old_string is empty" in EditFileTool().run(str(f), "", "x")
    assert len(DEFAULT_CHECKPOINT) == 0

    assert "Edited" in EditFileTool().run(str(f), "world", "there")
    assert len(DEFAULT_CHECKPOINT) == 1
    assert DEFAULT_CHECKPOINT.undo().restored
    assert f.read_text(encoding="utf-8") == "hello world"


def test_multi_edit_one_snapshot_per_operation(tmp_path):
    f = tmp_path / "m.txt"
    f.write_text("one two three", encoding="utf-8")

    MultiEditTool().run(
        str(f),
        edits=[
            {"old_string": "one", "new_string": "1"},
            {"old_string": "two", "new_string": "2"},
        ],
    )

    assert len(DEFAULT_CHECKPOINT) == 1, "batch must be ONE logical undo step"
    DEFAULT_CHECKPOINT.undo()
    assert f.read_text(encoding="utf-8") == "one two three"


# ------------------------------------------------------------- /undo wiring


def _ctx() -> ReplContext:
    return ReplContext(
        available_skills=[],
        active_skills_names=set(),
        permission_mode="plan",
    )


def test_slash_undo_messages(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("before", encoding="utf-8")
    DEFAULT_CHECKPOINT.snapshot(f)
    f.write_text("after", encoding="utf-8")

    res = handle_slash("/undo", _ctx())

    assert isinstance(res, CommandResult)
    assert "已恢复写入前的原内容" in res.message
    assert "已到底" in res.message
    # Second undo: stack now empty.
    res2 = handle_slash("/undo", _ctx())
    assert "栈为空" in res2.message
    assert f.read_text(encoding="utf-8") == "before"


def test_slash_undo_delete_message(tmp_path):
    f = tmp_path / "created.txt"
    DEFAULT_CHECKPOINT.snapshot(f)  # not existing
    f.write_text("x", encoding="utf-8")

    res = handle_slash("/undo", _ctx())

    assert "由 agent 新建" in res.message
    assert not f.exists()


def test_undo_is_builtin_cannot_be_shadowed(tmp_path):
    from coderio.cli.custom_commands import CustomCommand, try_expand_line

    manual = {"undo": CustomCommand("undo", "", "EVIL", "project")}
    assert try_expand_line("/undo", manual) is None


# ------------------------------------------------ mutation-verifier hardening


def test_default_singleton_enforces_module_cap(tmp_path):
    """Every cap test above passes explicit params — they'd stay green if
    someone bumped the MODULE defaults to infinity. The production singleton
    must enforce MAX_ENTRIES by default (mutation-verifier blind spot #1)."""
    for i in range(MAX_ENTRIES + 5):
        DEFAULT_CHECKPOINT.snapshot(tmp_path / f"f{i}.txt")

    assert len(DEFAULT_CHECKPOINT) == MAX_ENTRIES


def test_slash_undo_reports_exact_remaining_count(tmp_path):
    """Off-by-one guard: the '还可撤销 N 步' number must be exact, not just
    non-negative (mutation-verifier blind spot #2)."""
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    DEFAULT_CHECKPOINT.snapshot(a)
    DEFAULT_CHECKPOINT.snapshot(b)

    res1 = handle_slash("/undo", _ctx())
    assert "还可撤销 1 步" in res1.message
    res2 = handle_slash("/undo", _ctx())
    assert "已到底" in res2.message


def test_clear_context_preserves_checkpoints(monkeypatch, tmp_path):
    """Documented invariant pinned: checkpoints DELIBERATELY survive /clear —
    reverting file damage must not depend on chat history still existing."""
    from types import SimpleNamespace

    from coderio.cli.tui_runtime import TuiRuntime

    rt_cfg = SimpleNamespace(
        session=SimpleNamespace(save_dir=str(tmp_path / "sessions")),
        model=SimpleNamespace(default="m", provider="p"),
    )
    r = TuiRuntime(
        store=SimpleNamespace(names=lambda: []),
        active=SimpleNamespace(all=lambda: [], clear=lambda: None),
        tools=[],
        creds_path=None,
        custom_commands={},
    )
    r.tui = SimpleNamespace(_add_text=lambda *a, **k: None, _clear_history=lambda: None)
    r.rt = {"cfg": rt_cfg}
    monkeypatch.setattr(
        "coderio.session.store.Session.create",
        lambda save_dir, meta: SimpleNamespace(id="fresh-session"),
    )

    marker = tmp_path / "keep.txt"
    DEFAULT_CHECKPOINT.snapshot(marker)

    r.clear_context()

    assert len(DEFAULT_CHECKPOINT) == 1, "/clear wiped the undo stack"
    assert r.rt["session"].id == "fresh-session"
