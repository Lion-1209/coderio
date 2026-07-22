from coderio.tools.multi_edit import MultiEditTool


def test_multiple_distinct_edits(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    tool = MultiEditTool()
    out = tool.run(
        path=str(f),
        edits=[
            {"old_string": "alpha", "new_string": "ALPHA"},
            {"old_string": "gamma", "new_string": "GAMMA"},
        ],
    )
    content = f.read_text(encoding="utf-8")
    assert "ALPHA" in content
    assert "GAMMA" in content
    assert "beta" in content
    assert "2" in out


def test_edits_applied_in_order(tmp_path):
    """Each edit sees the result of the previous one (sequential application)."""
    f = tmp_path / "e.txt"
    f.write_text("foo\n", encoding="utf-8")
    tool = MultiEditTool()
    tool.run(
        path=str(f),
        edits=[
            {"old_string": "foo", "new_string": "bar"},
            {"old_string": "bar", "new_string": "baz"},
        ],
    )
    assert f.read_text(encoding="utf-8") == "baz\n"


def test_strips_line_prefix(tmp_path):
    """old_string copied from read_file's N\t output must still match."""
    f = tmp_path / "e.txt"
    f.write_text("alpha\nbeta\n", encoding="utf-8")
    tool = MultiEditTool()
    out = tool.run(
        path=str(f),
        edits=[
            {"old_string": "2\tbeta", "new_string": "2\tBETA"},
        ],
    )
    assert "Edited" in out
    assert "BETA" in f.read_text(encoding="utf-8")


def test_aborts_on_missing_match(tmp_path):
    """If any edit's old_string is not found, report and stop (no partial write)."""
    f = tmp_path / "e.txt"
    f.write_text("alpha\nbeta\n", encoding="utf-8")
    tool = MultiEditTool()
    out = tool.run(
        path=str(f),
        edits=[
            {"old_string": "alpha", "new_string": "ALPHA"},
            {"old_string": "nonexistent", "new_string": "X"},
        ],
    )
    assert "error" in out.lower()
    assert "not found" in out.lower()
    # unchanged (no partial write)
    assert f.read_text(encoding="utf-8") == "alpha\nbeta\n"


def test_aborts_on_ambiguous_match(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("foo\nfoo\n", encoding="utf-8")
    tool = MultiEditTool()
    out = tool.run(
        path=str(f),
        edits=[
            {"old_string": "foo", "new_string": "X"},
        ],
    )
    assert "error" in out.lower()
    assert "not unique" in out.lower()


def test_replace_all_flag(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("foo\nfoo\n", encoding="utf-8")
    tool = MultiEditTool()
    tool.run(
        path=str(f),
        edits=[
            {"old_string": "foo", "new_string": "X", "replace_all": True},
        ],
    )
    assert f.read_text(encoding="utf-8") == "X\nX\n"


def test_empty_edits_list(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("content\n", encoding="utf-8")
    tool = MultiEditTool()
    out = tool.run(path=str(f), edits=[])
    assert "no edits" in out.lower()
    assert f.read_text(encoding="utf-8") == "content\n"


def test_missing_file(tmp_path):
    tool = MultiEditTool()
    out = tool.run(
        path=str(tmp_path / "nope.txt"),
        edits=[
            {"old_string": "a", "new_string": "b"},
        ],
    )
    assert "not found" in out.lower() or "error" in out.lower()


def test_args_schema_present():
    assert hasattr(MultiEditTool, "args_schema")
    schema = MultiEditTool.args_schema.model_json_schema()
    assert "edits" in schema["properties"]
