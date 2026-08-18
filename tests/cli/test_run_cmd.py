"""Tests for the headless `coderio run` command.

Covers the three hard-failure paths (missing onboarding, untrusted repo
config, bad session id — none may hang waiting for input) plus the happy
path with a fully mocked runtime, and HeadlessStream's quiet behavior.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from coderio.cli.app import app

runner = CliRunner()


def test_run_missing_onboarding_exits_cleanly(tmp_path, monkeypatch):
    """No credentials + no env keys → stderr message + exit 1. NEVER prompt.

    A headless run without a TTY would hang forever on the onboarding wizard
    — this must be a hard error instead.
    """
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "Z_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # redirect Path.home()
    monkeypatch.setenv("HOME", str(tmp_path))

    result = runner.invoke(app, ["run", "do something"])
    assert result.exit_code == 1
    assert "credentials" in result.output.lower() or "onboarding" in result.output.lower()


def test_run_untrusted_repo_config_exits(tmp_path, monkeypatch):
    """Repo has .coderio/config.toml but no stored trust → exit 1 with a
    pointer to the interactive confirmation (never prompts headless)."""
    monkeypatch.setattr("coderio.cli.repl._needs_onboarding", lambda p: False)
    monkeypatch.setattr(
        "coderio.config.trust.existing_repo_configs",
        lambda d: [Path(d) / ".coderio" / "config.toml"],
    )
    monkeypatch.setattr("coderio.config.trust.is_repo_trusted", lambda d, u: False)

    result = runner.invoke(app, ["run", "task"])
    assert result.exit_code == 1
    assert "untrusted" in result.output.lower()


def test_run_bad_session_id_exits(tmp_path, monkeypatch):
    """--session-id pointing at a nonexistent session → clean exit 1."""
    monkeypatch.setattr("coderio.cli.repl._needs_onboarding", lambda p: False)
    monkeypatch.setattr("coderio.config.trust.existing_repo_configs", lambda d: [])
    # load_config must still work (reads user config only).
    result = runner.invoke(app, ["run", "task", "--session-id", "nope-1234"])
    assert result.exit_code == 1
    assert "session" in result.output.lower()


def _mock_runtime(monkeypatch, tmp_path, final_text="headless result"):
    """Patch build_runtime + run_deep_agent; return the kwargs captured."""
    captured: dict = {}

    class _FakeSession:
        pass

    from types import SimpleNamespace

    fake_cfg = SimpleNamespace(
        tools=SimpleNamespace(
            blocked_commands=[],
            network_allowed=True,
            whitelist_mode=False,
            allowed_commands=[],
            sandbox_mode="off",
            sandbox_fs=None,
            bash_shell="",
            workspace_root="",
        ),
        skills=SimpleNamespace(harness=True),
        # Mirrors CoderioConfig.hooks (list[HookSpec]); no hooks configured.
        hooks=[],
    )

    def _fake_build_runtime(**kwargs):
        captured["build_kwargs"] = kwargs
        return (fake_cfg, None, "fake-model", [], "fake-gate", _FakeSession(), None, None)

    def _fake_run_deep_agent(**kwargs):
        captured["run_kwargs"] = kwargs
        return final_text

    monkeypatch.setattr("coderio.cli.repl._needs_onboarding", lambda p: False)
    monkeypatch.setattr("coderio.config.trust.existing_repo_configs", lambda d: [])
    monkeypatch.setattr("coderio.cli.repl.build_runtime", _fake_build_runtime)
    monkeypatch.setattr("coderio.agent.deep_loop.run_deep_agent", _fake_run_deep_agent)
    return captured


def test_run_happy_path_prints_final_text(tmp_path, monkeypatch):
    """Full happy path: final agent text goes to stdout."""
    captured = _mock_runtime(monkeypatch, tmp_path, final_text="ALL DONE")
    result = runner.invoke(app, ["run", "fix the bug", "--quiet"])
    assert result.exit_code == 0
    assert "ALL DONE" in result.output
    # run_deep_agent received the task and a headless stream.
    assert captured["run_kwargs"]["user_input"] == "fix the bug"
    assert captured["run_kwargs"]["stream"].quiet is True


def test_run_permission_defaults_to_plan(tmp_path, monkeypatch):
    """v3 audit #7: headless default is PLAN (read-only) — a headless entry
    that silently allowed everything was a zero-confirmation door. full now
    requires --dangerously-skip-permissions."""
    captured = _mock_runtime(monkeypatch, tmp_path)
    result = runner.invoke(app, ["run", "task"])
    assert result.exit_code == 0, result.output
    assert captured["build_kwargs"]["mode_override"] == "plan"


def test_run_dangerously_skip_enables_full(tmp_path, monkeypatch):
    """--dangerously-skip-permissions is the explicit opt-in for full mode."""
    captured = _mock_runtime(monkeypatch, tmp_path)
    result = runner.invoke(app, ["run", "task", "--dangerously-skip-permissions"])
    assert result.exit_code == 0, result.output
    assert captured["build_kwargs"]["mode_override"] == "full"


def test_run_invalid_permission_rejected_early(tmp_path, monkeypatch):
    """Invalid --permission fails fast with exit 1 (config error), not later
    inside build_gate."""
    result = runner.invoke(app, ["run", "task", "--permission", "bogus"])
    assert result.exit_code == 1
    assert "Invalid --permission" in result.output


def test_run_confirm_without_tty_rejected(tmp_path, monkeypatch):
    """confirm/auto_edit need a TTY; under CliRunner's piped stdin they must
    fail fast instead of hanging on input() mid-execution."""
    monkeypatch.setattr("coderio.cli.repl._needs_onboarding", lambda p: False)
    monkeypatch.setattr("coderio.config.trust.existing_repo_configs", lambda d: [])
    import sys as _sys
    from unittest.mock import patch as _patch

    with _patch.object(_sys.stdin, "isatty", return_value=False):
        result = runner.invoke(app, ["run", "task", "--permission", "confirm"])
    assert result.exit_code == 1
    assert "TTY" in result.output


def test_run_permission_override_passed_through(tmp_path, monkeypatch):
    """--permission plan reaches build_runtime explicitly too."""
    captured = _mock_runtime(monkeypatch, tmp_path)
    result = runner.invoke(app, ["run", "task", "--permission", "plan"])
    assert result.exit_code == 0
    assert captured["build_kwargs"]["mode_override"] == "plan"


# --- HeadlessStream unit ---


def test_headless_stream_quiet_suppresses_tokens(capsys):
    from coderio.cli.run_cmd import HeadlessStream

    s = HeadlessStream(quiet=True)
    s.on_token("hello")
    s.on_tool_start("execute", {"command": "ls"})
    s.on_finish()
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_headless_stream_streams_tokens_to_stdout(capsys):
    from coderio.cli.run_cmd import HeadlessStream

    s = HeadlessStream(quiet=False)
    s.on_token("hello ")
    s.on_token("world")
    out = capsys.readouterr()
    assert "hello world" in out.out


def test_headless_stream_tool_progress_to_stderr(capsys):
    from coderio.cli.run_cmd import HeadlessStream

    s = HeadlessStream(quiet=False)
    s.on_tool_start("execute", {"command": "pytest -q"})
    out = capsys.readouterr()
    assert "execute" in out.err and "pytest" in out.err
    assert out.out == ""  # stdout is for model tokens only


# --- v3 #14: wall-clock timeout + exit codes ---


def test_run_timeout_exits_124(tmp_path, monkeypatch):
    """--timeout kills the wait and exits 124 (agent thread daemonized)."""
    import time

    def _slow_runtime(**kwargs):
        time.sleep(3)
        return _mock_runtime(monkeypatch, tmp_path)[0]  # never reached

    captured = _mock_runtime(monkeypatch, tmp_path)

    def _slow_deep_agent(**kwargs):
        time.sleep(5)
        return "late"

    monkeypatch.setattr("coderio.agent.deep_loop.run_deep_agent", _slow_deep_agent)
    start = time.time()
    result = runner.invoke(app, ["run", "task", "--timeout", "1"])
    elapsed = time.time() - start
    assert result.exit_code == 124, result.output
    assert elapsed < 4, f"timeout=1 should return promptly, took {elapsed:.1f}s"
    assert "Timed out" in result.output


def test_run_agent_failure_exits_2(tmp_path, monkeypatch):
    """Agent execution exceptions map to exit 2 (distinct from config=1)."""

    def _boom(**kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr("coderio.agent.deep_loop.run_deep_agent", _boom)
    result = runner.invoke(app, ["run", "task"])
    assert result.exit_code == 2, result.output
    assert "Agent execution failed" in result.output


def test_run_success_exits_0(tmp_path, monkeypatch):
    captured = _mock_runtime(monkeypatch, tmp_path, final_text="DONE-OK")
    result = runner.invoke(app, ["run", "task", "--quiet"])
    assert result.exit_code == 0
    assert "DONE-OK" in result.output
