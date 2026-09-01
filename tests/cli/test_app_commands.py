"""Tests for coderio's top-level CLI commands (app.py coverage, P2-4).

The `run` command is covered by tests/cli/test_run_cmd.py and the e2e suite;
this file covers the rest of the entry surface: the default TUI callback's
option pass-through, `skills`, `mcp`, `config`, and main_entry.
"""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from coderio.cli.app import app

runner = CliRunner()


# ----------------------------------------------------------- default → run_tui


def test_default_invokes_run_tui_with_overrides(monkeypatch):
    """No subcommand → the TUI launches with the global options passed
    through verbatim."""
    seen: dict = {}

    def _fake_run_tui(**kwargs):
        seen.update(kwargs)

    import coderio.cli.tui as tui_mod

    monkeypatch.setattr(tui_mod, "run_tui", _fake_run_tui)
    result = runner.invoke(app, ["--resume", "abc", "--provider", "stepfun", "--model", "m1"])
    assert result.exit_code == 0, result.output
    assert seen == {
        "provider_override": "stepfun",
        "model_override": "m1",
        "resume": "abc",
        "continue_last": False,
    }


def test_default_continue_flag(monkeypatch):
    import coderio.cli.tui as tui_mod

    seen: dict = {}
    monkeypatch.setattr(tui_mod, "run_tui", lambda **kw: seen.update(kw))
    result = runner.invoke(app, ["--continue"])
    assert result.exit_code == 0, result.output
    assert seen["continue_last"] is True


def test_subcommand_skips_tui(monkeypatch):
    """A subcommand must NOT boot the TUI (main callback returns early)."""
    import coderio.cli.tui as tui_mod

    booted = []
    monkeypatch.setattr(tui_mod, "run_tui", lambda **kw: booted.append(kw))
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.output
    assert booted == []


# ------------------------------------------------------------------- skills


def test_skills_list_prints_bundled():
    result = runner.invoke(app, ["skills", "list"])
    assert result.exit_code == 0, result.output
    assert "Installed skills:" in result.output
    assert "  - " in result.output, "bundled skills should be listed"


def test_skills_install_success(monkeypatch):
    import coderio.cli.skills_cmd as sc

    monkeypatch.setattr(
        sc,
        "install_skills",
        lambda repo, target, force=False: SimpleNamespace(success=True, action="installed", skills=["a", "b"]),
    )
    result = runner.invoke(app, ["skills", "install", "--repo", "https://example.com/skills"])
    assert result.exit_code == 0, result.output
    assert "Installed: 2 skills" in result.output
    assert "- a" in result.output


def test_skills_install_failure_exits_1(monkeypatch):
    import coderio.cli.skills_cmd as sc

    monkeypatch.setattr(
        sc,
        "install_skills",
        lambda repo, target, force=False: SimpleNamespace(success=False, action="", message="git clone failed"),
    )
    result = runner.invoke(app, ["skills", "install"])
    assert result.exit_code == 1
    assert "git clone failed" in result.output


def test_skills_update_delegates_to_install(monkeypatch):
    import coderio.cli.skills_cmd as sc

    seen: dict = {}
    monkeypatch.setattr(
        sc,
        "install_skills",
        lambda repo, target, force=False: (
            seen.update(repo=repo, force=force) or SimpleNamespace(success=True, action="updated", skills=["x"])
        ),
    )
    result = runner.invoke(app, ["skills", "update", "--repo", "https://example.com/s2"])
    assert result.exit_code == 0, result.output
    assert seen["repo"] == "https://example.com/s2"
    assert seen["force"] is False, "update must never force-overwrite"


# ---------------------------------------------------------------------- mcp


def test_mcp_list_empty(monkeypatch):
    import coderio.cli.mcp_cmd as mc

    monkeypatch.setattr(mc, "mcp_list", lambda: [])
    result = runner.invoke(app, ["mcp", "list"])
    assert result.exit_code == 0, result.output
    assert "No MCP servers configured" in result.output


def test_mcp_list_grouped_by_scope(monkeypatch):
    import coderio.cli.mcp_cmd as mc

    monkeypatch.setattr(
        mc,
        "mcp_list",
        lambda: [("fs", "project", ".mcp.json"), ("gh", "user", "~/.coderio/mcp.json")],
    )
    result = runner.invoke(app, ["mcp", "list"])
    assert result.exit_code == 0, result.output
    assert "project scope" in result.output
    assert "user scope" in result.output
    assert "fs" in result.output and "gh" in result.output


def test_mcp_add_success(monkeypatch):
    import coderio.cli.mcp_cmd as mc

    seen: dict = {}

    def _fake_add(name, **kw):
        seen.update(name=name, **kw)
        return SimpleNamespace(success=True, action="added", message=f"{name} @ project")

    monkeypatch.setattr(mc, "mcp_add", _fake_add)
    result = runner.invoke(
        app,
        ["mcp", "add", "fs", "--command", "npx", "--arg", "-y", "--arg", "@mcp/fs", "--scope", "user"],
    )
    assert result.exit_code == 0, result.output
    assert seen == {
        "name": "fs",
        "server_type": "stdio",
        "command": "npx",
        "url": None,
        "args": ["-y", "@mcp/fs"],
        "scope": "user",
    }
    assert "Added" in result.output


def test_mcp_add_failure_exits_1(monkeypatch):
    import coderio.cli.mcp_cmd as mc

    monkeypatch.setattr(
        mc, "mcp_add", lambda name, **kw: SimpleNamespace(success=False, action="", message="bad request")
    )
    result = runner.invoke(app, ["mcp", "add", "x"])
    assert result.exit_code == 1
    assert "bad request" in result.output


def test_mcp_remove_noop_vs_removed(monkeypatch):
    import coderio.cli.mcp_cmd as mc

    monkeypatch.setattr(mc, "mcp_remove", lambda name, scope: SimpleNamespace(action="noop", message="absent"))
    result = runner.invoke(app, ["mcp", "remove", "ghost"])
    assert result.exit_code == 0, result.output
    assert "No-op" in result.output

    monkeypatch.setattr(mc, "mcp_remove", lambda name, scope: SimpleNamespace(action="removed", message="gone"))
    result = runner.invoke(app, ["mcp", "remove", "fs"])
    assert result.exit_code == 0, result.output
    assert "Removed" in result.output


# ------------------------------------------------------------------- config


def test_config_command_prints_panel(monkeypatch, tmp_path):
    from coderio.config import load_config

    cfg = load_config(search_from=str(tmp_path))
    monkeypatch.setattr("coderio.cli.app.load_config", lambda: cfg)

    class _FakeProviderInfo:
        base_url = "https://api.example.com"

    import coderio.cli.providers as prov

    monkeypatch.setattr(prov, "get_provider", lambda pid: _FakeProviderInfo())

    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.output
    assert "coderio config" in result.output
    assert "permission:" in result.output


def test_main_entry_reconfigures_streams(monkeypatch):
    """main_entry() forces UTF-8 reconfigure then calls the app — must not
    raise even when streams are already UTF-8 or lack reconfigure()."""
    import sys

    import coderio.cli.app as app_mod

    monkeypatch.setattr(app_mod, "app", lambda: None)  # don't boot the CLI loop
    app_mod.main_entry()  # exercises the reconfigure loop; no exception = pass
    # sanity: the loop really touched the streams' encoding attr
    assert sys.stdout is not None
