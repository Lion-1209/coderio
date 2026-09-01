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
