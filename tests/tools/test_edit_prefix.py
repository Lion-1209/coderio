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


def test_edit_rejects_empty_old_string(tmp_path):
    """REGRESSION (2026-08-07 report P1-7): old_string='' with replace_all=True
    would insert new_string between every character (str.replace('', x) matches
    len(text)+1 times), producing catastrophic bloat. Must be rejected, and the
    file must be left untouched."""
    f = tmp_path / "e.txt"
    original = "alpha\nbeta\n"
    f.write_text(original, encoding="utf-8")
    tool = EditFileTool()
    out = tool.run(path=str(f), old_string="", new_string="X", replace_all=True)
    assert out.startswith("Error"), "empty old_string must be rejected"
    assert "empty" in out.lower()
    # File must be unchanged.
    assert f.read_text(encoding="utf-8") == original
