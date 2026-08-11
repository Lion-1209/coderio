"""``coderio mcp`` subcommand implementations (add / list / remove).

Manages MCP server entries in the Claude-Code-compatible ``.mcp.json`` files:
  - Project scope: ``{project}/.mcp.json`` (created if absent on add)
  - User scope:    ``~/.coderio/mcp.json``

The config format is identical across both scopes (top-level ``mcpServers``
object) and is shared with ``coderio.mcp_loader`` — this module only reads/
writes the files; the actual server connections happen at TUI startup via
``load_mcp_tools_sync``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from coderio.mcp_loader import _find_mcp_config, _read_mcp_json


@dataclass
class McpManageResult:
    """Result of an add/remove operation (for CLI rendering + test assertions)."""

    success: bool
    action: str = ""  # "added" / "removed" / "noop"
    scope: str = "project"  # "project" / "user"
    path: str = ""
    server: str = ""
    message: str = ""
    all_servers: list[str] = field(default_factory=list)


def _project_config_path(search_from: str | Path = ".") -> Path:
    """Return the project .mcp.json path: existing if found, else {search_from}/.mcp.json.

    On add, if no .mcp.json exists we create one at the search_from root (not
    wherever the upward walk would find one — that could be a parent dir we
    don't own). On list/remove, we use the upward walk to locate the active file.
    """
    found = _find_mcp_config(search_from)
    if found is not None:
        return found
    return Path(search_from).resolve() / ".mcp.json"


def _user_config_path(user_dir: str | Path | None = None) -> Path:
    """Return the user-scope ~/.coderio/mcp.json path."""
    base = Path(user_dir) if user_dir else Path.home() / ".coderio"
    return base / "mcp.json"


def _read_servers(path: Path) -> dict[str, dict]:
    """Read mcpServers from a single path (returns {} if missing/corrupt)."""
    return _read_mcp_json(path)


def _write_servers(path: Path, servers: dict[str, dict]) -> None:
    """Write the mcpServers dict back to path, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve any top-level keys we don't understand (defense for future fields).
    existing: dict = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (json.JSONDecodeError, OSError):
            existing = {}
    existing["mcpServers"] = servers
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def mcp_list(
    search_from: str | Path = ".",
    user_dir: str | Path | None = None,
) -> list[tuple[str, str, Path]]:
    """List all configured MCP servers across both scopes.

    Returns a list of (server_name, scope, path) tuples. Project scope is
    listed first; on name collision, project wins (matching load_mcp_config
    merge semantics) but both entries are shown for transparency.
    """
    project_path = _find_mcp_config(search_from)
    project_servers = _read_servers(project_path) if project_path else {}
    user_path = _user_config_path(user_dir)
    user_servers = _read_servers(user_path)

    out: list[tuple[str, str, Path]] = []
    for name in sorted(project_servers):
        out.append((name, "project", project_path))  # type: ignore[arg-type]
    for name in sorted(user_servers):
        out.append((name, "user", user_path))
    return out


def mcp_add(
    name: str,
    *,
    server_type: str = "stdio",
    command: str | None = None,
    url: str | None = None,
    args: list[str] | None = None,
    env: dict | None = None,
    headers: dict | None = None,
    scope: str = "project",
    user_dir: str | Path | None = None,
    search_from: str | Path = ".",
) -> McpManageResult:
    """Add an MCP server entry to the project or user config file.

    For stdio: ``command`` is required (``args``/``env`` optional).
    For http/sse: ``url`` is required (``headers`` optional).

    If the server name already exists in the target file, it's overwritten
    (idempotent re-configuration, not an error).
    """
    if not name:
        return McpManageResult(success=False, message="server name is required")

    # Build the config entry based on type.
    entry: dict = {}
    if server_type in ("stdio", ""):
        if not command:
            return McpManageResult(
                success=False,
                server=name,
                message="stdio server requires --command",
            )
        entry["command"] = command
        if args:
            entry["args"] = args
        if env:
            entry["env"] = env
    elif server_type in ("http", "sse"):
        if not url:
            return McpManageResult(
                success=False,
                server=name,
                message=f"{server_type} server requires --url",
            )
        entry["type"] = server_type
        entry["url"] = url
        if headers:
            entry["headers"] = headers
    else:
        return McpManageResult(
            success=False,
            server=name,
            message=f"unknown server type {server_type!r} (use stdio/http/sse)",
        )

    # Resolve target path by scope.
    if scope == "project":
        target = _project_config_path(search_from)
    elif scope == "user":
        target = _user_config_path(user_dir)
    else:
        return McpManageResult(success=False, message=f"unknown scope {scope!r} (use project/user)")

    servers = _read_servers(target)
    servers[name] = entry
    _write_servers(target, servers)

    return McpManageResult(
        success=True,
        action="added",
        scope=scope,
        path=str(target),
        server=name,
        message=f"Added {server_type} server {name!r} to {target}",
        all_servers=sorted(servers),
    )


def mcp_remove(
    name: str,
    *,
    scope: str = "project",
    user_dir: str | Path | None = None,
    search_from: str | Path = ".",
) -> McpManageResult:
    """Remove an MCP server entry. No-op (not an error) if the name is absent."""
    if not name:
        return McpManageResult(success=False, message="server name is required")

    if scope == "project":
        target = _project_config_path(search_from)
    elif scope == "user":
        target = _user_config_path(user_dir)
    else:
        return McpManageResult(success=False, message=f"unknown scope {scope!r} (use project/user)")

    if not target.is_file():
        return McpManageResult(
            success=True,
            action="noop",
            scope=scope,
            path=str(target),
            server=name,
            message=f"No config at {target}; nothing to remove",
        )

    servers = _read_servers(target)
    if name not in servers:
        return McpManageResult(
            success=True,
            action="noop",
            scope=scope,
            path=str(target),
            server=name,
            message=f"Server {name!r} not found in {target}",
            all_servers=sorted(servers),
        )

    del servers[name]
    _write_servers(target, servers)
    return McpManageResult(
        success=True,
        action="removed",
        scope=scope,
        path=str(target),
        server=name,
        message=f"Removed {name!r} from {target}",
        all_servers=sorted(servers),
    )
