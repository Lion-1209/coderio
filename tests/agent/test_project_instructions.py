"""Project instruction files (AGENTS.md / CLAUDE.md) — P2-7 tests."""

from __future__ import annotations

from coderio.agent.project_instructions import (
    instructions_block,
    load_project_instructions,
)


def _write(path, name, text):
    path.mkdir(parents=True, exist_ok=True)
    f = path / name
    f.write_text(text, encoding="utf-8")
    return f


def test_reads_agents_md(tmp_path):
    _write(tmp_path, "AGENTS.md", "Always use uv, never pip.")
    assert load_project_instructions(tmp_path) == "Always use uv, never pip."


def test_falls_back_to_claude_md(tmp_path):
    _write(tmp_path, "CLAUDE.md", "Keep functions short.")
    assert load_project_instructions(tmp_path) == "Keep functions short."


def test_agents_md_wins_over_claude_md(tmp_path):
    _write(tmp_path, "AGENTS.md", "from agents")
    _write(tmp_path, "CLAUDE.md", "from claude")
    assert load_project_instructions(tmp_path) == "from agents"


def test_walks_up_to_project_root(tmp_path):
    root = tmp_path / "repo"
    sub = root / "src" / "pkg"
    sub.mkdir(parents=True)
    _write(root, "AGENTS.md", "root instructions")
    assert load_project_instructions(sub) == "root instructions"


def test_missing_files_return_empty(tmp_path):
    assert load_project_instructions(tmp_path) == ""


def test_unreadable_file_returns_empty(tmp_path, monkeypatch):
    f = _write(tmp_path, "AGENTS.md", "x")
    monkeypatch.setattr("pathlib.Path.read_text", lambda self, **k: (_ for _ in ()).throw(OSError("boom")))
    assert load_project_instructions(f.parent) == ""


def test_oversized_instructions_are_truncated(tmp_path):
    _write(tmp_path, "AGENTS.md", "A" * 50_000)
    out = load_project_instructions(tmp_path)
    assert len(out) == 20_000


def test_instructions_block_wraps_content(tmp_path):
    _write(tmp_path, "AGENTS.md", "convention one")
    block = instructions_block(tmp_path)
    assert "convention one" in block
    assert "untrusted file content" in block, "wrapper must frame content as untrusted"


def test_instructions_block_empty_when_missing(tmp_path):
    assert instructions_block(tmp_path) == ""


# ----------------------------------------------------- deep_loop wiring (P0-1)


def test_load_stops_at_launch_dir_when_bounded(tmp_path):
    """stop_at=launch dir bounds the walk: a parent AGENTS.md must NOT load."""
    parent = tmp_path / "parent"
    sub = parent / "child"
    sub.mkdir(parents=True)
    _write(parent, "AGENTS.md", "LEAKED-INSTRUCTION-FROM-PARENT")
    assert load_project_instructions(sub, stop_at=sub) == ""


def test_resolve_system_prompt_bounds_walk_at_launch_dir(tmp_path, monkeypatch):
    """P0-1 regression (2026-09-02 audit, finding 2): with workdir=None the
    instruction walk must be bounded at the LAUNCH dir. The pre-fix code
    passed stop_at=None, so an AGENTS.md in a PARENT directory leaked into
    the system prompt of every project launched from below it.

    NOTE: the launch dir deliberately has NO AGENTS.md of its own —
    nearest-wins would otherwise mask the leak (the child file would be
    returned before the walk ever reaches the parent)."""
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    _write(parent, "AGENTS.md", "LEAKED-INSTRUCTION-FROM-PARENT")
    monkeypatch.chdir(child)

    from coderio.agent import deep_loop

    sp = deep_loop._resolve_system_prompt(None, None, None)  # workdir=None
    assert "LEAKED-INSTRUCTION-FROM-PARENT" not in sp, "parent AGENTS.md leaked past the launch dir"


def test_run_deep_agent_default_launch_bounded(tmp_path, monkeypatch):
    """Integration pin: real run_deep_agent with workdir=None carries the
    launch-dir AGENTS.md, never the parent's."""
    import pytest

    deepagents = pytest.importorskip("deepagents")

    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    _write(parent, "AGENTS.md", "LEAKED-INSTRUCTION-FROM-PARENT")
    # No AGENTS.md in child — nearest-wins would mask the parent leak.
    monkeypatch.chdir(child)

    captured = {}

    class _FakeAgent:
        def stream(self, inputs, config=None, stream_mode=None):
            return iter(())

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return _FakeAgent()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)

    from coderio.agent.deep_loop import TurnSpec, run_deep_agent
    from coderio.session.store import Session

    session = Session.create(str(tmp_path / "sessions"), {"model": "m"})
    run_deep_agent("hi", TurnSpec(model=object()), session)

    sp = captured.get("system_prompt", "")
    assert "LEAKED-INSTRUCTION-FROM-PARENT" not in sp, "parent AGENTS.md leaked into the engine prompt"


def test_run_deep_agent_still_injects_launch_dir_agents_md(tmp_path, monkeypatch):
    """Guard against over-fixing: the launch dir's OWN AGENTS.md must still be
    injected after the P0-1 bounding."""
    import pytest

    deepagents = pytest.importorskip("deepagents")

    child = tmp_path / "child"
    child.mkdir(parents=True)
    _write(child, "AGENTS.md", "child-local convention")
    monkeypatch.chdir(child)

    captured = {}

    class _FakeAgent:
        def stream(self, inputs, config=None, stream_mode=None):
            return iter(())

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return _FakeAgent()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)

    from coderio.agent.deep_loop import TurnSpec, run_deep_agent
    from coderio.session.store import Session

    session = Session.create(str(tmp_path / "sessions"), {"model": "m"})
    run_deep_agent("hi", TurnSpec(model=object()), session)

    assert "child-local convention" in captured.get("system_prompt", "")
