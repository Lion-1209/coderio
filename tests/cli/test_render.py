from rich.console import Console

from coderio.cli.render import mask_key, render_error, render_markdown, render_tool_call


def test_mask_key_shows_last4():
    assert mask_key("sk-abcdef1234") == "****1234"
    assert mask_key("short") == "****"
    assert mask_key("") == "****"


def test_render_markdown_contains_text():
    console = Console(record=True, width=60)
    console.print(render_markdown("# Title\n\nsome **bold** text"))
    out = console.export_text()
    assert "Title" in out
    assert "bold" in out


def test_render_error_contains_message():
    console = Console(record=True, width=60)
    console.print(render_error("something broke"))
    assert "something broke" in console.export_text()


def test_render_tool_call_shows_name():
    console = Console(record=True, width=60)
    console.print(render_tool_call("read_file", {"path": "a.py"}))
    out = console.export_text()
    assert "read_file" in out
