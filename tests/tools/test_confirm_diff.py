"""Tests for the confirm-mode diff preview (P3-1, tools/confirm_diff.py)."""

from __future__ import annotations

from coderio.tools.confirm_diff import build_diff_preview


def test_write_file_new_file_shows_content_block(tmp_path):
    target = tmp_path / "new.py"
    out = build_diff_preview("write_file", {"file_path": str(target), "content": "a\nb\n"}, workdir=tmp_path)
    assert out is not None
    assert "(new file)" in out
    assert "+a" in out and "+b" in out


def test_write_file_overwrite_shows_unified_diff(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("old line\n", encoding="utf-8")
    out = build_diff_preview("write_file", {"file_path": str(target), "content": "new line\n"}, workdir=tmp_path)
    assert out is not None
    assert "-old line" in out
    assert "+new line" in out


def test_edit_file_renders_replacement_diff(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("def main():\n    print('hello')\n", encoding="utf-8")
    out = build_diff_preview(
        "edit_file",
        {"file_path": str(target), "old_string": "print('hello')", "new_string": "print('bye')"},
        workdir=tmp_path,
    )
    assert out is not None
    assert "-    print('hello')" in out
    assert "+    print('bye')" in out


def test_edit_file_old_string_missing_reports_failure_upfront(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("nothing here\n", encoding="utf-8")
    out = build_diff_preview(
        "edit_file",
        {"file_path": str(target), "old_string": "nope", "new_string": "x"},
        workdir=tmp_path,
    )
    assert out is not None and "would fail" in out, "the real edit would fail — say so upfront"


def test_multi_edit_applies_edits_in_order(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    out = build_diff_preview(
        "multi_edit",
        {
            "path": str(target),
            "edits": [
                {"old_string": "alpha", "new_string": "ALPHA"},
                {"old_string": "beta", "new_string": "BETA"},
            ],
        },
        workdir=tmp_path,
    )
    assert out is not None
    assert "+ALPHA" in out and "+BETA" in out


def test_edit_missing_file_is_reported(tmp_path):
    out = build_diff_preview(
        "edit_file",
        {"file_path": str(tmp_path / "ghost.py"), "old_string": "a", "new_string": "b"},
        workdir=tmp_path,
    )
    assert out is not None and "file not found" in out


def test_non_file_tools_get_no_preview():
    assert build_diff_preview("execute", {"command": "rm -rf /"}) is None
    assert build_diff_preview("read_file", {"path": "x"}) is None


def test_relative_path_resolves_against_workdir(tmp_path):
    target = tmp_path / "rel.txt"
    target.write_text("old\n", encoding="utf-8")
    out = build_diff_preview(
        "write_file",
        {"file_path": "rel.txt", "content": "new\n"},
        workdir=tmp_path,
    )
    assert out is not None and "-old" in out and "+new" in out


def test_long_diff_is_truncated(tmp_path):
    target = tmp_path / "big.py"
    target.write_text("\n".join(f"line {i}" for i in range(200)) + "\n", encoding="utf-8")
    out = build_diff_preview(
        "write_file",
        {"file_path": str(target), "content": "\n".join(f"NEW {i}" for i in range(200)) + "\n"},
        workdir=tmp_path,
        max_lines=10,
    )
    assert out is not None
    assert "truncated" in out
    assert len(out.splitlines()) <= 12


def test_unexpected_args_return_none():
    assert build_diff_preview("write_file", {}) is None
    assert build_diff_preview("edit_file", {"file_path": ""}) is None


# ------------------------------------------------- real-tool semantics (P1-2, 2026-09-03)


def test_replace_all_shows_every_occurrence(tmp_path):
    """deepagents semantics: replace_all=True replaces EVERY occurrence — the
    preview must show all of them, not just the first (misleading approval)."""
    target = tmp_path / "app.py"
    target.write_text("todo\ntodo\ntodo\n", encoding="utf-8")
    out = build_diff_preview(
        "edit_file",
        {"file_path": str(target), "old_string": "todo", "new_string": "done", "replace_all": True},
        workdir=tmp_path,
    )
    assert out is not None
    assert out.count("+done") == 3, f"all three replacements must be previewed: {out}"


def test_multiple_matches_without_replace_all_reports_failure(tmp_path):
    """deepagents semantics: >1 occurrence without replace_all FAILS (nothing
    written). The preview must say so instead of rendering one replacement."""
    target = tmp_path / "app.py"
    target.write_text("todo\ntodo\n", encoding="utf-8")
    out = build_diff_preview(
        "edit_file",
        {"file_path": str(target), "old_string": "todo", "new_string": "done"},
        workdir=tmp_path,
    )
    assert out is not None
    assert "2 times" in out and "would fail" in out


def test_multi_edit_all_or_nothing_is_respected(tmp_path):
    """deepagents multi_edit aborts wholesale when ANY edit fails — the
    preview must not render a partially-applied result."""
    target = tmp_path / "app.py"
    target.write_text("keep\n", encoding="utf-8")
    out = build_diff_preview(
        "multi_edit",
        {
            "path": str(target),
            "edits": [
                {"old_string": "keep", "new_string": "changed"},
                {"old_string": "not-in-file", "new_string": "x"},
            ],
        },
        workdir=tmp_path,
    )
    assert out is not None
    assert "edit 2" in out and "would fail" in out
    assert "+changed" not in out, "the whole multi_edit aborts — nothing is applied"


# ------------------------------------- guards for the P1-2 semantics fixes


def test_crlf_file_and_crlf_old_string_preview_normally(tmp_path):
    """Guard: CRLF file + CRLF old_string must preview as a normal diff —
    the real tool normalizes CRLF before matching, so 'would fail' here was
    the exact false negative the semantics alignment fixed."""
    target = tmp_path / "win.py"
    target.write_bytes(b"print('hello')\r\nprint('bye')\r\n")
    out = build_diff_preview(
        "edit_file",
        {"file_path": str(target), "old_string": "print('hello')\r\n", "new_string": "print('hi')\r\n"},
        workdir=tmp_path,
    )
    assert out is not None
    assert "would fail" not in out
    assert "-print('hello')" in out and "+print('hi')" in out


def test_string_replace_all_false_is_not_truthy(tmp_path):
    """Guard: replace_all arriving as the STRING "false" (raw model JSON is
    not schema-validated before the preview) must parse as False — a truthy
    conversion showed an all-occurrences preview for an edit that would fail."""
    target = tmp_path / "app.py"
    target.write_text("todo\ntodo\n", encoding="utf-8")
    out = build_diff_preview(
        "edit_file",
        {"file_path": str(target), "old_string": "todo", "new_string": "done", "replace_all": "false"},
        workdir=tmp_path,
    )
    assert out is not None
    assert "appears 2 times" in out and "would fail" in out


def test_line_number_prefixed_old_string_previews_normally(tmp_path):
    """Guard: old_string carrying read_file's 'N\t' line-number prefixes must
    preview as a normal diff (multi_edit strips them before matching)."""
    target = tmp_path / "app.py"
    target.write_text("foo\nbar\n", encoding="utf-8")
    out = build_diff_preview(
        "multi_edit",
        {
            "path": str(target),
            "edits": [{"old_string": "1\tfoo\n2\tbar", "new_string": "1\tfoo\n2\tBAZ"}],
        },
        workdir=tmp_path,
    )
    assert out is not None
    assert "would fail" not in out
    assert "+BAZ" in out or "+2\tBAZ" in out, f"prefix should be stripped in preview: {out}"
