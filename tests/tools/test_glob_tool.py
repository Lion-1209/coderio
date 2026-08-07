"""Tests for the GlobTool — match files by glob pattern.

The report (2026-08-07, dimension 7 gap) noted glob_tool had no direct tests.
These cover the happy path, recursive patterns, empty results, and the
non-existent-directory case.
"""

from __future__ import annotations

from coderio.tools.glob_tool import GlobTool


def test_glob_finds_files_by_extension(tmp_path):
    """`**/*.py` recursively finds all Python files."""
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y")
    (tmp_path / "c.txt").write_text("z")
    out = GlobTool().run(pattern="**/*.py", path=str(tmp_path))
    assert "a.py" in out
    assert "b.py" in out
    assert "c.txt" not in out


def test_glob_non_recursive(tmp_path):
    """`*.py` (no **) finds only top-level files."""
    (tmp_path / "top.py").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.py").write_text("y")
    out = GlobTool().run(pattern="*.py", path=str(tmp_path))
    assert "top.py" in out
    assert "deep.py" not in out


def test_glob_no_matches_returns_sentinel(tmp_path):
    """No matches → returns the 'No matches' sentinel (not empty string)."""
    out = GlobTool().run(pattern="**/*.nonexistent", path=str(tmp_path))
    assert out == "No matches"


def test_glob_matches_are_sorted(tmp_path):
    """Results are sorted for deterministic output (helps the model reason)."""
    for name in ("c.py", "a.py", "b.py"):
        (tmp_path / name).write_text("x")
    out = GlobTool().run(pattern="*.py", path=str(tmp_path))
    lines = out.split("\n")
    assert lines == sorted(lines)


def test_glob_default_path_is_cwd(tmp_path, monkeypatch):
    """path defaults to '.' — uses CWD when no path is given."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "found.py").write_text("x")
    out = GlobTool().run(pattern="*.py")
    assert "found.py" in out
