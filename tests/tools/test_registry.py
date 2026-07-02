from coderio.tools import build_default_tools


def test_registry_has_all_tools():
    tools = build_default_tools()
    names = {t.name for t in tools}
    expected = frozenset({
        "edit_file", "bash", "read_file", "web_search", "web_fetch",
        "write_file", "todo", "grep", "glob",
    })
    assert expected.issubset(names)
