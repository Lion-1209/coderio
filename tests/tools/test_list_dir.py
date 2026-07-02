from pathlib import Path

from coderio.tools.list_dir import ListDirTool


def _setup(tmp_path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("z", encoding="utf-8")
    deep = sub / "deep"
    deep.mkdir()
    (deep / "d.md").write_text("w", encoding="utf-8")


def test_list_flat(tmp_path):
    _setup(tmp_path)
    out = ListDirTool().run(path=str(tmp_path))
    assert "a.py" in out
    assert "b.txt" in out
    assert "sub" in out
    assert "c.py" not in out


def test_list_recursive(tmp_path):
    _setup(tmp_path)
    out = ListDirTool().run(path=str(tmp_path), recursive=True)
    assert "a.py" in out
    assert "c.py" in out
    assert "d.md" in out


def test_list_max_depth(tmp_path):
    _setup(tmp_path)
    out = ListDirTool().run(path=str(tmp_path), recursive=True, max_depth=1)
    assert "a.py" in out
    assert "c.py" not in out
    assert "d.md" not in out


def test_list_missing_dir(tmp_path):
    out = ListDirTool().run(path=str(tmp_path / "nope"))
    assert "not found" in out.lower() or "error" in out.lower()


def test_list_shows_dir_marker(tmp_path):
    _setup(tmp_path)
    out = ListDirTool().run(path=str(tmp_path))
    assert "sub" in out
    assert "sub/" in out or "[dir]" in out or "sub\\" in out


def test_args_schema_present():
    assert hasattr(ListDirTool, "args_schema")
    schema = ListDirTool.args_schema.model_json_schema()
    assert "path" in schema["properties"]
    assert "recursive" in schema["properties"]
