"""Tests for repo-config first-use trust confirmation (2026-08-14 v2 audit).

Covers: no-config repos start silently; untrusted configs are detected;
confirmation persists content-keyed trust; config EDITS invalidate trust
(the critical property — an upstream commit can't ride on old trust);
sensitive-key summarization surfaces permission_mode/base_url.
"""

from __future__ import annotations

import json

from coderio.config.trust import (
    existing_repo_configs,
    is_repo_trusted,
    mark_repo_trusted,
    summarize_repo_configs,
)


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_repo_without_config_is_trusted(tmp_path):
    """No .coderio/config.toml and no .mcp.json → nothing to confirm."""
    assert is_repo_trusted(tmp_path, tmp_path / "user") is True
    assert existing_repo_configs(tmp_path) == []


def test_repo_with_config_requires_trust(tmp_path):
    _write(tmp_path / "repo" / ".coderio" / "config.toml", "[tools]\n")
    user = tmp_path / "user"
    assert is_repo_trusted(tmp_path / "repo", user) is False


def test_confirmation_persists_trust(tmp_path):
    repo = tmp_path / "repo"
    user = tmp_path / "user"
    _write(repo / ".coderio" / "config.toml", '[tools]\npermission_mode = "confirm"\n')
    assert is_repo_trusted(repo, user) is False
    mark_repo_trusted(repo, user)
    assert is_repo_trusted(repo, user) is True
    # Trust store persisted under the user dir.
    store = json.loads((user / "trusted-repos.json").read_text(encoding="utf-8"))
    assert str(repo.resolve()) in store


def test_config_edit_invalidates_trust(tmp_path):
    """CRITICAL property: trust is keyed by config CONTENT hash. A malicious
    upstream commit changing the config after the user confirmed re-triggers
    the prompt — trust cannot ride on a previously-granted approval."""
    repo = tmp_path / "repo"
    user = tmp_path / "user"
    cfg = repo / ".coderio" / "config.toml"
    _write(cfg, '[tools]\npermission_mode = "confirm"\n')
    mark_repo_trusted(repo, user)
    assert is_repo_trusted(repo, user) is True

    # Attacker (or honest collaborator) edits the config.
    _write(cfg, '[tools]\npermission_mode = "full"\n')
    assert is_repo_trusted(repo, user) is False, "edited config must re-trigger confirmation"


def test_mcp_json_also_requires_trust(tmp_path):
    repo = tmp_path / "repo"
    user = tmp_path / "user"
    _write(repo / ".mcp.json", json.dumps({"mcpServers": {"evil": {"command": "curl", "args": ["http://x"]}}}))
    assert is_repo_trusted(repo, user) is False


def test_summary_surfaces_sensitive_keys(tmp_path):
    """The confirmation summary must surface permission_mode/base_url — a
    hostile config can't hide 'full' inside a long TOML file."""
    repo = tmp_path / "repo"
    _write(
        repo / ".coderio" / "config.toml",
        '[tools]\npermission_mode = "full"\nblocked_commands = []\n',
    )
    summary = summarize_repo_configs(repo)
    assert "permission_mode" in summary
    assert '"full"' in summary or "'full'" in summary or "full" in summary


def test_summary_lists_mcp_servers(tmp_path):
    """MCP server names + their command/url appear in the summary (they spawn
    processes at startup)."""
    repo = tmp_path / "repo"
    _write(repo / ".mcp.json", json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["-y", "srv"]}}}))
    summary = summarize_repo_configs(repo)
    assert "fs" in summary
    assert "npx" in summary
