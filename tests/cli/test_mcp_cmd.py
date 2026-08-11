"""Tests for the ``coderio mcp`` subcommand (add/list/remove).

These test the mcp_cmd module functions directly (not the Typer CLI layer) —
the Typer wrappers are thin pass-throughs whose only logic is console
rendering, which is covered by the import-smoke + --help smoke at the CI level.
"""

from __future__ import annotations

import json

from coderio.cli.mcp_cmd import mcp_add, mcp_list, mcp_remove

# ----------------------------------------------------- mcp_add


def test_mcp_add_creates_project_config(tmp_path):
    """No .mcp.json exists → add creates one at the search_from root."""
    result = mcp_add(
        "filesystem",
        server_type="stdio",
        command="npx",
        args=["-y", "@mcp/server-fs", "/tmp"],
        scope="project",
        search_from=tmp_path,
        user_dir=tmp_path / "user",
    )
    assert result.success
    assert result.action == "added"
    assert result.server == "filesystem"
    # The file must exist and contain the server.
    config_path = tmp_path / ".mcp.json"
    assert config_path.is_file()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "filesystem" in data["mcpServers"]
    assert data["mcpServers"]["filesystem"]["command"] == "npx"


def test_mcp_add_appends_to_existing(tmp_path):
    """An existing .mcp.json keeps its prior servers; the new one is appended."""
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "node", "args": ["a.js"]}}}),
        encoding="utf-8",
    )
    result = mcp_add(
        "newserver",
        server_type="stdio",
        command="npx",
        args=["srv"],
        scope="project",
        search_from=tmp_path,
    )
    assert result.success
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "existing" in data["mcpServers"]  # preserved
    assert "newserver" in data["mcpServers"]  # added
    assert result.all_servers == ["existing", "newserver"]


def test_mcp_add_overwrites_same_name(tmp_path):
    """Adding an existing server name overwrites (idempotent re-configuration)."""
    mcp_add(
        "fs",
        server_type="stdio",
        command="npx",
        args=["v1"],
        scope="project",
        search_from=tmp_path,
    )
    result = mcp_add(
        "fs",
        server_type="stdio",
        command="node",
        args=["v2"],
        scope="project",
        search_from=tmp_path,
    )
    assert result.success
    data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    # The second add must overwrite the first, not duplicate.
    assert len(data["mcpServers"]) == 1
    assert data["mcpServers"]["fs"]["command"] == "node"
    assert data["mcpServers"]["fs"]["args"] == ["v2"]


def test_mcp_add_user_scope(tmp_path):
    """scope=user writes to {user_dir}/mcp.json instead of project .mcp.json."""
    user_dir = tmp_path / "userhome" / ".coderio"
    result = mcp_add(
        "github",
        server_type="http",
        url="https://example.com/mcp",
        scope="user",
        user_dir=user_dir,
        search_from=tmp_path,
    )
    assert result.success
    assert result.scope == "user"
    user_config = user_dir / "mcp.json"
    assert user_config.is_file()
    # Project .mcp.json must NOT be created.
    assert not (tmp_path / ".mcp.json").exists()
    data = json.loads(user_config.read_text(encoding="utf-8"))
    assert "github" in data["mcpServers"]


def test_mcp_add_http_requires_url(tmp_path):
    """http server without --url is an error (not a silent empty entry)."""
    result = mcp_add(
        "bad",
        server_type="http",
        url=None,
        scope="project",
        search_from=tmp_path,
    )
    assert not result.success
    assert "url" in result.message.lower()


def test_mcp_add_stdio_requires_command(tmp_path):
    """stdio server without --command is an error."""
    result = mcp_add(
        "bad",
        server_type="stdio",
        command=None,
        scope="project",
        search_from=tmp_path,
    )
    assert not result.success
    assert "command" in result.message.lower()


def test_mcp_add_unknown_type(tmp_path):
    """An unrecognized type string is an error."""
    result = mcp_add(
        "bad",
        server_type="bogus",
        scope="project",
        search_from=tmp_path,
    )
    assert not result.success
    assert "unknown" in result.message.lower() or "bogus" in result.message.lower()


# ----------------------------------------------------- mcp_remove


def test_mcp_remove_deletes_entry(tmp_path):
    """remove deletes the named server from the config."""
    mcp_add(
        "fs",
        server_type="stdio",
        command="npx",
        args=["srv"],
        scope="project",
        search_from=tmp_path,
    )
    result = mcp_remove("fs", scope="project", search_from=tmp_path)
    assert result.success
    assert result.action == "removed"
    data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert "fs" not in data["mcpServers"]


def test_mcp_remove_nonexistent_is_noop(tmp_path):
    """Removing a name that isn't in the config is a no-op, not an error."""
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    result = mcp_remove("ghost", scope="project", search_from=tmp_path)
    assert result.success
    assert result.action == "noop"
    # Existing servers untouched.
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "other" in data["mcpServers"]


def test_mcp_remove_no_config_file_is_noop(tmp_path):
    """remove on a directory with no .mcp.json is a no-op (no file to edit)."""
    result = mcp_remove("anything", scope="project", search_from=tmp_path)
    assert result.success
    assert result.action == "noop"


# ----------------------------------------------------- mcp_list


def test_mcp_list_shows_both_scopes(tmp_path):
    """list returns servers from both project and user scope."""
    user_dir = tmp_path / "userhome" / ".coderio"
    mcp_add(
        "fs",
        server_type="stdio",
        command="npx",
        args=["srv"],
        scope="project",
        search_from=tmp_path,
        user_dir=user_dir,
    )
    mcp_add(
        "github",
        server_type="http",
        url="https://example.com/mcp",
        scope="user",
        user_dir=user_dir,
        search_from=tmp_path,
    )
    entries = mcp_list(search_from=tmp_path, user_dir=user_dir)
    names_scopes = {(name, scope) for name, scope, _ in entries}
    assert ("fs", "project") in names_scopes
    assert ("github", "user") in names_scopes


def test_mcp_list_empty_returns_empty(tmp_path):
    """No configs anywhere → empty list (not an error)."""
    entries = mcp_list(search_from=tmp_path, user_dir=tmp_path / "nope")
    assert entries == []


def test_mcp_list_project_overrides_user(tmp_path):
    """When the same name exists in both scopes, both are listed (project
    wins at load time per load_mcp_config, but list shows both for transparency)."""
    user_dir = tmp_path / "userhome" / ".coderio"
    mcp_add(
        "shared",
        server_type="stdio",
        command="user-cmd",
        scope="user",
        user_dir=user_dir,
        search_from=tmp_path,
    )
    mcp_add(
        "shared",
        server_type="stdio",
        command="project-cmd",
        scope="project",
        search_from=tmp_path,
        user_dir=user_dir,
    )
    entries = mcp_list(search_from=tmp_path, user_dir=user_dir)
    shared_entries = [(n, s) for n, s, _ in entries if n == "shared"]
    # Both scopes must be represented.
    assert ("shared", "project") in shared_entries
    assert ("shared", "user") in shared_entries
