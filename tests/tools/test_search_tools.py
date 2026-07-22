from pathlib import Path

from coderio.tools.glob_tool import GlobTool
from coderio.tools.grep_tool import GrepTool


def _setup(tmp_path):
    (tmp_path / "a.py").write_text("import os\nprint('hi')\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("hello\nworld\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("import sys\n")


def test_glob_matches(tmp_path):
    _setup(tmp_path)
    tool = GlobTool()
    out = tool.run(pattern="**/*.py", path=str(tmp_path))
    assert "a.py" in out
    assert "c.py" in out
    assert "b.txt" not in out


def test_grep_content(tmp_path):
    _setup(tmp_path)
    tool = GrepTool()
    out = tool.run(pattern="import", path=str(tmp_path))
    assert "a.py" in out
    assert "c.py" in out


def test_grep_files_only(tmp_path):
    _setup(tmp_path)
    tool = GrepTool()
    out = tool.run(
        pattern="import", path=str(tmp_path), output_mode="files_with_matches"
    )
    assert "a.py" in out


def test_grep_fallback_python(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "coderio.tools.grep_tool._rg_available",
        lambda: False,
    )
    _setup(tmp_path)
    tool = GrepTool()
    out = tool.run(pattern="hello", path=str(tmp_path))
    assert "b.txt" in out


def test_grep_count_mode_python(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "coderio.tools.grep_tool._rg_available",
        lambda: False,
    )
    _setup(tmp_path)
    tool = GrepTool()
    out = tool.run(pattern="import", path=str(tmp_path), output_mode="count")
    assert "2 matches" in out
