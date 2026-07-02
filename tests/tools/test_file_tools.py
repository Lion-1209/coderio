from pathlib import Path

import pytest

from coderio.tools.read_file import ReadFileTool
from coderio.tools.write_file import WriteFileTool
from coderio.tools.edit_file import EditFileTool


def test_read_file_with_line_numbers(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("alpha\nbeta\n", encoding="utf-8")
    tool = ReadFileTool()
    out = tool.run(path=str(f))
    assert "1\talpha" in out
    assert "2\tbeta" in out


def test_read_file_offset_limit(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("a\nb\nc\nd\n", encoding="utf-8")
    tool = ReadFileTool()
    out = tool.run(path=str(f), offset=2, limit=2)
    assert "b" in out
    assert "c" in out
    assert "a" not in out


def test_read_missing_file(tmp_path):
    tool = ReadFileTool()
    out = tool.run(path=str(tmp_path / "nope.txt"))
    assert "not found" in out.lower() or "error" in out.lower()


def test_write_creates_file(tmp_path):
    tool = WriteFileTool()
    out = tool.run(path=str(tmp_path / "x.txt"), content="hello")
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "hello"
    assert "wrote" in out.lower() or "created" in out.lower()


def test_write_creates_parent_dirs(tmp_path):
    tool = WriteFileTool()
    tool.run(path=str(tmp_path / "sub" / "deep" / "y.txt"), content="hi")
    assert (tmp_path / "sub" / "deep" / "y.txt").read_text(encoding="utf-8") == "hi"


def test_edit_replaces_unique(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("foo\nbar\nfoo\n", encoding="utf-8")
    tool = EditFileTool()
    out = tool.run(path=str(f), old_string="bar", new_string="BAZ")
    assert "BAZ" in f.read_text(encoding="utf-8")
    assert "bar" not in f.read_text(encoding="utf-8")


def test_edit_ambiguous_raises(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("foo\nfoo\n", encoding="utf-8")
    tool = EditFileTool()
    out = tool.run(path=str(f), old_string="foo", new_string="x")
    assert "error" in out.lower()
    assert "multiple" in out.lower() or "not unique" in out.lower()


def test_edit_replace_all(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("foo\nfoo\n", encoding="utf-8")
    tool = EditFileTool()
    tool.run(path=str(f), old_string="foo", new_string="x", replace_all=True)
    assert f.read_text(encoding="utf-8") == "x\nx\n"
