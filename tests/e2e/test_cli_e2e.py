"""E2E smoke tests — drive the REAL typer app end-to-end.

P2-4 (2026-08-28 audit: tests/e2e was an empty shell). The LLM boundary
(run_deep_agent) is stubbed; everything around it — CLI parsing, permission
gates, runtime wiring, headless streaming, session persistence, and the
timeout watchdog — is the production path. Fake API keys come from the
environment (tests/conftest.py seeds test-key), so `coderio run` reaches the
engine exactly as a CI harness would invoke it.
"""

from __future__ import annotations

import re
from dataclasses import replace as _dc_replace

from typer.testing import CliRunner

from coderio.cli.app import app

runner = CliRunner()

# On CI (Linux/macOS runners) rich renders --help WITH ANSI styling even
# through CliRunner's capture (color detection differs from a dev terminal),
# so option names arrive wrapped in escape sequences. Strip SGR codes before
# any `needle in output` assertion — matching against raw output is why the
# first push of this file went red on non-Windows runners.
_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strip ANSI SGR (color/style) escape sequences from captured CLI output."""
    return _ANSI_SGR.sub("", text)


# ----------------------------------------------------------------- helpers


def _patched_env(monkeypatch, tmp_path):
    """Fake HOME + fake key: no onboarding prompt, no trust prompt, all
    state lands inside tmp_path."""
    from coderio.config import load_config

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-e2e")
    monkeypatch.setattr("coderio.cli.repl._needs_onboarding", lambda p: False)
    monkeypatch.setattr("coderio.config.trust.existing_repo_configs", lambda d: [])

    cfg = load_config(search_from=str(tmp_path))
    cfg = _dc_replace(cfg, session=_dc_replace(cfg.session, save_dir=str(tmp_path / "sessions")))
    return cfg


def _patch_runtime(monkeypatch, cfg, final_text="E2E OK"):
    """Real Session on disk; engine stubbed at the LLM boundary."""
    from coderio.session.store import Session

    session = Session.create(cfg.session.save_dir, {"model": cfg.model.default, "provider": cfg.model.provider})
    captured: dict = {}

    def _fake_run_deep_agent(**kwargs):
        captured.update(kwargs)
        from coderio.session import Message

        kwargs["session"].append(Message.assistant(final_text))
        return final_text

    monkeypatch.setattr(
        "coderio.cli.repl.build_runtime",
        lambda **kw: (cfg, None, "fake-model", [], "fake-gate", session, None, None),
    )
    monkeypatch.setattr("coderio.agent.deep_loop.run_deep_agent", _fake_run_deep_agent)
    return captured, session


# ----------------------------------------------------------------- --help


def test_help_snapshot():
    """`coderio --help` lists every subcommand with the global options."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = _plain(result.output)
    for needle in ("run", "skills", "mcp", "config", "--resume", "--continue", "--provider", "--model"):
        assert needle in output, f"missing {needle!r} in --help output"


def test_help_snapshot_subcommands():
    """Each subcommand's --help renders (options don't drift away silently)."""
    for argv, needles in [
        (["run", "--help"], ("--permission", "--session-id", "--quiet", "--timeout", "--dangerously-skip-permissions")),
        (["skills", "--help"], ("list", "install", "update")),
        (["mcp", "--help"], ("list", "add", "remove")),
        (["config", "--help"], ("Print current configuration",)),
    ]:
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, f"{argv} failed: {result.output}"
        output = _plain(result.output)
        for needle in needles:
            assert needle in output, f"missing {needle!r} in {' '.join(argv)} output"


# ----------------------------------------------------------------- coderio run


def test_fake_key_run_smoke(monkeypatch, tmp_path):
    """Full `coderio run` happy path with a fake key: task in, final text on
    stdout, exit 0 — the exact shape CI harnesses call."""
    cfg = _patched_env(monkeypatch, tmp_path)
    _patch_runtime(monkeypatch, cfg)

    result = runner.invoke(app, ["run", "say hello", "--quiet"])
    assert result.exit_code == 0, result.output
    assert "E2E OK" in result.output


def test_run_short_session_persists(monkeypatch, tmp_path):
    """A short headless run creates a resumable session jsonl on disk and the
    engine received that live Session object."""
    cfg = _patched_env(monkeypatch, tmp_path)
    captured, _session = _patch_runtime(monkeypatch, cfg)

    result = runner.invoke(app, ["run", "short task", "--quiet"])
    assert result.exit_code == 0, result.output

    # The engine got the task and the real session object...
    assert captured["user_input"] == "short task"
    assert captured["session"].id
    # ...and the session jsonl exists on disk with the assistant reply persisted.
    session_file = tmp_path / "sessions" / f"{captured['session'].id}.jsonl"
    assert session_file.is_file(), f"session jsonl missing: {session_file}"
    content = session_file.read_text(encoding="utf-8")
    assert "E2E OK" in content


def test_run_timeout_returns_124(monkeypatch, tmp_path):
    """--timeout kills a hung engine run with the documented exit code 124."""
    import time

    cfg = _patched_env(monkeypatch, tmp_path)
    _patch_runtime(monkeypatch, cfg)

    def _slow(**kw):
        time.sleep(5)

    import coderio.agent.deep_loop as deep_loop

    monkeypatch.setattr(deep_loop, "run_deep_agent", _slow)

    result = runner.invoke(app, ["run", "hang forever", "--quiet", "--timeout", "1"])
    assert result.exit_code == 124, result.output
    assert "Timed out" in result.output
