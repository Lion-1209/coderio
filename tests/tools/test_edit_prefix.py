from coderio.tools.edit_file import EditFileTool, _strip_line_prefix


def test_strip_line_prefix():
    assert _strip_line_prefix("3\toriginal line") == "original line"
    assert _strip_line_prefix("  3 \toriginal") == "original"
    assert _strip_line_prefix("no prefix here") == "no prefix here"


def test_edit_strips_read_file_prefix(tmp_path):
    """An old_string copied from read_file's 'N\\t...' output must still match."""
    f = tmp_path / "e.txt"
    f.write_text("alpha\nbeta\n", encoding="utf-8")
    tool = EditFileTool()
    out = tool.run(path=str(f), old_string="2\tbeta", new_string="2\tBETA")
    assert "Edited" in out
    assert "BETA" in f.read_text(encoding="utf-8")
