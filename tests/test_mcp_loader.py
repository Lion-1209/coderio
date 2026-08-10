"""Tests for the MCP tool loader.

Tests the config-reading + normalization logic (no real MCP server connections
needed). The actual tool-loading path (MultiServerMCPClient) is tested
implicitly — we only verify config parsing and error handling here.
"""

from __future__ import annotations

import json

from coderio.mcp_loader import (
    _normalize_connection,
    _read_mcp_json,
    load_mcp_config,
)

# ----------------------------------------------------- _read_mcp_json


def test_read_mcp_json_valid(tmp_path):
    """A valid .mcp.json with mcpServers returns the servers dict."""
    f = tmp_path / ".mcp.json"
    f.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fs": {"command": "npx", "args": ["server-fs", "/tmp"]},
                    "web": {"type": "http", "url": "https://example.com/mcp"},
                }
            }
        ),
        encoding="utf-8",
    )
    servers = _read_mcp_json(f)
    assert "fs" in servers
    assert "web" in servers
    assert servers["fs"]["command"] == "npx"


def test_read_mcp_json_missing_file(tmp_path):
    """A non-existent file returns {} (no crash)."""
    assert _read_mcp_json(tmp_path / "nope.json") == {}


def test_read_mcp_json_invalid_json(tmp_path):
    """Corrupt JSON returns {} (no crash, just a warning log)."""
    f = tmp_path / ".mcp.json"
    f.write_text("{ this is not valid json", encoding="utf-8")
    assert _read_mcp_json(f) == {}


def test_read_mcp_json_no_mcpServers_key(tmp_path):
    """JSON without mcpServers returns {}."""
    f = tmp_path / ".mcp.json"
    f.write_text('{"other": "stuff"}', encoding="utf-8")
    assert _read_mcp_json(f) == {}


def test_read_mcp_json_empty_servers(tmp_path):
    """mcpServers: {} returns {}."""
    f = tmp_path / ".mcp.json"
    f.write_text('{"mcpServers": {}}', encoding="utf-8")
    assert _read_mcp_json(f) == {}


# ----------------------------------------------------- load_mcp_config (merge)


def test_load_mcp_config_project_only(tmp_path):
    """Project .mcp.json without user config → project servers."""
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["fs"]}}}), encoding="utf-8"
    )
    config = load_mcp_config(search_from=tmp_path, user_dir=tmp_path / "fake_user")
    assert "fs" in config


def test_load_mcp_config_user_only(tmp_path):
    """User mcp.json without project config → user servers."""
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"type": "http", "url": "https://example.com"}}}),
        encoding="utf-8",
    )
    config = load_mcp_config(search_from=tmp_path / "project", user_dir=user_dir)
    assert "github" in config


def test_load_mcp_config_project_overrides_user(tmp_path):
    """When both scopes define the same server name, project wins."""
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"fs": {"command": "user-npx", "args": ["v1"]}}}), encoding="utf-8"
    )
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"fs": {"command": "project-npx", "args": ["v2"]}}}), encoding="utf-8"
    )
    config = load_mcp_config(search_from=project_dir, user_dir=user_dir)
    assert config["fs"]["command"] == "project-npx"


def test_load_mcp_config_both_exist_merged(tmp_path):
    """Project and user configs with different servers are merged."""
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"type": "http", "url": "https://gh.com"}}}), encoding="utf-8"
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["fs"]}}}), encoding="utf-8"
    )
    config = load_mcp_config(search_from=tmp_path, user_dir=user_dir)
    assert "github" in config  # from user
    assert "fs" in config  # from project


def test_load_mcp_config_nothing_configured(tmp_path):
    """No config files at all → empty dict."""
    assert load_mcp_config(search_from=tmp_path, user_dir=tmp_path / "nope") == {}


# ----------------------------------------------------- _normalize_connection


def test_normalize_stdio_server():
    """A stdio entry ({command, args}) gets transport='stdio'."""
    conn = _normalize_connection("fs", {"command": "npx", "args": ["server-fs", "/tmp"]})
    assert conn["transport"] == "stdio"
    assert conn["command"] == "npx"
    assert conn["args"] == ["server-fs", "/tmp"]


def test_normalize_stdio_with_env():
    """stdio entries can carry env vars."""
    conn = _normalize_connection("db", {"command": "node", "args": ["db.js"], "env": {"DB_URL": "x"}})
    assert conn["transport"] == "stdio"
    assert conn["env"] == {"DB_URL": "x"}


def test_normalize_http_server():
    """Claude Code's 'type': 'http' → adapter's 'streamable_http'."""
    conn = _normalize_connection("github", {"type": "http", "url": "https://api.example.com/mcp/"})
    assert conn["transport"] == "streamable_http"
    assert conn["url"] == "https://api.example.com/mcp/"


def test_normalize_http_with_headers():
    """HTTP entries can carry headers (e.g. auth)."""
    conn = _normalize_connection(
        "github",
        {"type": "http", "url": "https://example.com", "headers": {"Authorization": "Bearer xxx"}},
    )
    assert conn["headers"] == {"Authorization": "Bearer xxx"}


def test_normalize_sse_server():
    """SSE transport is passed through."""
    conn = _normalize_connection("old", {"type": "sse", "url": "https://example.com/sse"})
    assert conn["transport"] == "sse"


def test_normalize_unknown_format():
    """An unrecognized config returns {} (will be skipped)."""
    conn = _normalize_connection("bad", {"random": "stuff"})
    assert conn == {}


# ----------------------------------------------------- load_mcp_tools (no servers)


def test_load_mcp_tools_no_config(tmp_path):
    """No .mcp.json anywhere → load_mcp_tools returns []."""
    import asyncio

    from coderio.mcp_loader import load_mcp_tools

    tools = asyncio.run(load_mcp_tools(search_from=tmp_path, user_dir=tmp_path / "nope"))
    assert tools == []


def test_load_mcp_tools_sync_no_config(tmp_path):
    """The sync wrapper also returns [] when no config exists."""
    from coderio.mcp_loader import load_mcp_tools_sync

    assert load_mcp_tools_sync(search_from=tmp_path, user_dir=tmp_path / "nope") == []
