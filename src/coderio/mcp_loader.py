"""MCP (Model Context Protocol) tool loader.

Reads Claude-Code-compatible ``.mcp.json`` config files, connects to the
configured MCP servers via ``langchain-mcp-adapters``, and returns the tools
as LangChain ``StructuredTool`` instances ready to pass to ``create_deep_agent``.

Config format (identical to Claude Code's ``.mcp.json``):

.. code-block:: json

    {
      "mcpServers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        },
        "github": {
          "type": "http",
          "url": "https://api.githubcopilot.com/mcp/",
          "headers": {"Authorization": "Bearer xxx"}
        }
      }
    }

Config discovery (two scopes, project overrides user on name collision):
  - Project: ``{search_from}/.mcp.json``
  - User: ``~/.coderio/mcp.json``

Tool naming: ``MultiServerMCPClient(tool_name_prefix=True)`` prefixes each tool
name with its server (e.g. ``filesystem_read_file``) so MCP tools never collide
with coderio's built-in ``read_file``, ``write_file``, etc.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def _find_mcp_config(search_from: str | Path = ".") -> Path | None:
    """Find the project-level .mcp.json (walks upward from search_from).

    Returns the path if found, None otherwise. Stops at the user's home dir
    (the home ~/.coderio/mcp.json is the USER scope, handled separately).
    """
    cur = Path(search_from).resolve()
    home = Path.home()
    while cur != home and cur != cur.parent:
        candidate = cur / ".mcp.json"
        if candidate.is_file():
            return candidate
        cur = cur.parent
    return None


def _read_mcp_json(path: Path | None) -> dict[str, dict]:
    """Read a single .mcp.json file, returning the mcpServers dict.

    Returns {} on missing file, invalid JSON, or wrong structure — never raises.
    """
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("Failed to read MCP config %s: %s", path, e)
        return {}
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        return {}
    return servers


def load_mcp_config(
    search_from: str | Path = ".",
    user_dir: str | Path | None = None,
) -> dict[str, dict]:
    """Merge MCP server configs from project + user scope.

    Project .mcp.json overrides user ~/.coderio/mcp.json on server-name
    collision (project-specific config wins, same as skills/config layering).

    Returns a dict of server_name → config_dict, possibly empty.
    """
    project_servers = _read_mcp_json(_find_mcp_config(search_from))
    # User scope: ~/.coderio/mcp.json
    user_path = Path(user_dir) if user_dir else Path.home() / ".coderio"
    user_servers = _read_mcp_json(user_path / "mcp.json")
    # Merge: user first, project overrides.
    merged = dict(user_servers)
    merged.update(project_servers)
    return merged


def _normalize_connection(server_name: str, cfg: dict) -> dict:
    """Convert a .mcp.json entry to langchain-mcp-adapters connection format.

    Claude Code uses ``"type": "http"``; the adapter expects
    ``"transport": "streamable_http"``. stdio entries omit the transport key
    (it's the default) — we add it explicitly.

    ZCode-compatible fields (all optional, all backward-compatible):
      - ``enabled`` (bool, default True): set False to temporarily disable a
        server without removing its config. Legacy alias ``enable`` accepted.
      - ``cwd`` (str, stdio only): working directory for the subprocess. Key
        on Windows where ``npx`` may not be on PATH without it.
      - ``timeoutMs`` (int): per-request timeout in milliseconds, forwarded to
        the adapter as ``timeout`` (seconds). Default 30000 if unset by caller.
      - ``type`` inference: when ``type`` is omitted, infer from the presence
        of ``command`` (→ stdio) or ``url`` (→ http).

    Legacy field migrations (ZCode auto-migrates these; we follow suit so
    ZCode/Cursor-style configs work unchanged):
      - ``type: "remote"`` → ``http``
      - ``environment`` → ``env``
      - ``http_headers`` → ``headers``
      - ``enable`` → ``enabled``
    """
    # --- enabled gate (ZCode-compatible): disabled servers return {} → skipped.
    if not cfg.get("enabled", cfg.get("enable", True)):
        _log.info("MCP server %r is disabled (enabled=false), skipping", server_name)
        return {}

    # --- legacy field migrations (applied before branch logic).
    server_type = cfg.get("type", "")
    if server_type == "remote":  # ZCode legacy name for http
        server_type = "http"

    # timeoutMs → seconds (forwarded to adapter on each branch).
    timeout_s: float | None = None
    if "timeoutMs" in cfg:
        try:
            timeout_s = float(cfg["timeoutMs"]) / 1000.0
        except (TypeError, ValueError):
            _log.warning("MCP server %r has invalid timeoutMs %r, ignoring", server_name, cfg["timeoutMs"])

    # --- stdio branch (command present, or type explicitly stdio).
    if "command" in cfg:
        conn: dict[str, Any] = {
            "transport": "stdio",
            "command": cfg["command"],
            "args": cfg.get("args", []),
        }
        # env (ZCode legacy: environment → env)
        env = cfg.get("env", cfg.get("environment"))
        if env:
            conn["env"] = env
        # cwd (Windows PATH robustness — npx/node often need an explicit cwd)
        if "cwd" in cfg:
            conn["cwd"] = cfg["cwd"]
        if timeout_s is not None:
            conn["timeout"] = timeout_s
        return conn

    # --- type inference: url present without command → http (ZCode behavior).
    if server_type in ("http", "streamable_http") or ("url" in cfg and not server_type):
        if "url" not in cfg:
            _log.warning("MCP server %r has type=http but no url, skipping", server_name)
            return {}
        conn = {
            "transport": "streamable_http",
            "url": cfg["url"],
        }
        # headers (ZCode legacy: http_headers → headers)
        headers = cfg.get("headers", cfg.get("http_headers"))
        if headers:
            conn["headers"] = headers
        if timeout_s is not None:
            conn["timeout"] = timeout_s
        return conn

    if server_type == "sse":
        if "url" not in cfg:
            _log.warning("MCP server %r has type=sse but no url, skipping", server_name)
            return {}
        conn = {
            "transport": "sse",
            "url": cfg["url"],
        }
        headers = cfg.get("headers", cfg.get("http_headers"))
        if headers:
            conn["headers"] = headers
        if timeout_s is not None:
            conn["timeout"] = timeout_s
        return conn

    # Unknown format — return a marker that will be skipped.
    _log.warning("MCP server %r has unrecognized config: %s", server_name, cfg)
    return {}


async def load_mcp_tools(
    search_from: str | Path = ".",
    user_dir: str | Path | None = None,
) -> list:
    """Connect to all configured MCP servers and return their tools.

    Returns a list of LangChain ``BaseTool`` instances (empty if no servers
    configured or all connections failed). Tool names are prefixed with the
    server name (e.g. ``filesystem_read_file``) to avoid collisions with
    coderio's built-in tools.

    Connection failures (server not installed, network error, bad config) are
    logged as warnings and skipped — they never block coderio startup.
    """
    config = load_mcp_config(search_from, user_dir)
    if not config:
        return []

    # Build connections dict for MultiServerMCPClient.
    connections: dict[str, dict] = {}
    for name, raw_cfg in config.items():
        if not isinstance(raw_cfg, dict):
            continue
        conn = _normalize_connection(name, raw_cfg)
        if conn:
            connections[name] = conn

    if not connections:
        return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        _log.warning(
            "langchain-mcp-adapters not installed — MCP tools disabled. "
            "Install with: pip install langchain-mcp-adapters"
        )
        return []

    try:
        client = MultiServerMCPClient(connections, tool_name_prefix=True)
    except Exception as e:
        _log.warning("Failed to initialize MCP client: %s", e)
        return []

    # Load tools from each server individually — one bad server (command not
    # found, network error) must not prevent the good servers' tools from
    # loading. MultiServerMCPClient connections are lazy; get_tools(server_name)
    # only connects to that one server.
    all_tools: list = []
    for server_name in connections:
        try:
            server_tools = await client.get_tools(server_name=server_name)
            all_tools.extend(server_tools)
        except Exception as e:
            _log.warning("MCP server %r failed to load (skipped): %s", server_name, e)

    _log.info("Loaded %d MCP tools from %d servers", len(all_tools), len(connections))
    return all_tools


def load_mcp_tools_sync(
    search_from: str | Path = ".",
    user_dir: str | Path | None = None,
) -> list:
    """Sync wrapper for load_mcp_tools. Called from build_runtime (sync context).

    Uses asyncio.run() — safe because this runs once at startup, not inside an
    existing event loop.
    """
    import asyncio

    try:
        return asyncio.run(load_mcp_tools(search_from, user_dir))
    except RuntimeError:
        # Already inside an event loop (shouldn't happen at startup, but guard
        # just in case). Return empty — MCP tools are opt-in, not critical.
        _log.warning("Cannot load MCP tools inside an existing event loop")
        return []
