from coderio.tools.note import NoteTool


def test_write_and_read(tmp_path):
    tool = NoteTool(notes_dir=str(tmp_path))
    out = tool.run(action="write", name="decision1", content="use html5")
    assert "saved" in out.lower() or "wrote" in out.lower()
    assert (tmp_path / "decision1.md").read_text(encoding="utf-8") == "use html5"
    out2 = tool.run(action="read", name="decision1")
    assert "use html5" in out2


def test_read_missing(tmp_path):
    tool = NoteTool(notes_dir=str(tmp_path))
    out = tool.run(action="read", name="nope")
    assert "not found" in out.lower() or "no note" in out.lower()


def test_list(tmp_path):
    tool = NoteTool(notes_dir=str(tmp_path))
    tool.run(action="write", name="a", content="x")
    tool.run(action="write", name="b", content="y")
    out = tool.run(action="list")
    assert "a" in out
    assert "b" in out


def test_list_empty(tmp_path):
    tool = NoteTool(notes_dir=str(tmp_path))
    out = tool.run(action="list")
    assert "no" in out.lower() or "empty" in out.lower()


def test_delete(tmp_path):
    tool = NoteTool(notes_dir=str(tmp_path))
    tool.run(action="write", name="temp", content="x")
    out = tool.run(action="delete", name="temp")
    assert "deleted" in out.lower()
    assert not (tmp_path / "temp.md").exists()


def test_append(tmp_path):
    tool = NoteTool(notes_dir=str(tmp_path))
    tool.run(action="write", name="log", content="line1\n")
    tool.run(action="append", name="log", content="line2\n")
    content = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert "line1" in content
    assert "line2" in content


def test_append_creates_if_missing(tmp_path):
    tool = NoteTool(notes_dir=str(tmp_path))
    tool.run(action="append", name="new", content="first")
    assert (tmp_path / "new.md").read_text(encoding="utf-8") == "first"


def test_invalid_action(tmp_path):
    tool = NoteTool(notes_dir=str(tmp_path))
    out = tool.run(action="bogus", name="x")
    assert "error" in out.lower() or "unknown" in out.lower()


def test_args_schema_present():
    assert hasattr(NoteTool, "args_schema")
    schema = NoteTool.args_schema.model_json_schema()
    assert "action" in schema["properties"]
    assert "name" in schema["properties"]
