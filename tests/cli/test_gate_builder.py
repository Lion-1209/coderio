"""Tests for the sandbox-aware permission gate builder (audit P1-11, 2026-09-04).

auto_allow_if_sandboxed may only auto-approve execute when the configured
sandbox ACTUALLY provides a boundary on the current platform — "sandbox_mode
!= off" alone gave macOS / Linux-without-bwrap users zero isolation AND zero
confirmation.
"""

import pytest

from coderio.cli import repl
from coderio.config.models import Config, ToolsConfig


def _cfg(**tools) -> Config:
    return Config(tools=ToolsConfig(permission_mode="confirm", **tools))


def test_sandbox_boundary_off_is_not_effective():
    assert repl._sandbox_boundary("off") == (False, None)


def test_sandbox_boundary_windows_effective(monkeypatch):
    """Windows Job Object caps + tree kill exist (file-write isolation does
    not — that gap keeps its own warning at the gate)."""
    monkeypatch.setattr(repl.sys, "platform", "win32")
    assert repl._sandbox_boundary("write") == (True, None)


def test_sandbox_boundary_darwin_never_effective(monkeypatch):
    monkeypatch.setattr(repl.sys, "platform", "darwin")
    effective, gap = repl._sandbox_boundary("write")
    assert effective is False
    assert gap and "macOS" in gap


def test_sandbox_boundary_linux_job_unimplemented(monkeypatch):
    monkeypatch.setattr(repl.sys, "platform", "linux")
    effective, gap = repl._sandbox_boundary("job")
    assert effective is False
    assert gap and "job" in gap


def test_sandbox_boundary_linux_write_follows_bwrap(monkeypatch):
    monkeypatch.setattr(repl.sys, "platform", "linux")
    try:
        from coderio.tools import linux_sandbox
    except ImportError:
        pytest.skip("linux_sandbox not importable on this platform")
    monkeypatch.setattr(linux_sandbox, "bwrap_available", lambda: True)
    assert repl._sandbox_boundary("write") == (True, None)
    monkeypatch.setattr(linux_sandbox, "bwrap_available", lambda: False)
    effective, gap = repl._sandbox_boundary("write")
    assert effective is False and gap and "bubblewrap" in gap


def test_auto_allow_disabled_when_sandbox_not_effective(monkeypatch, capsys):
    """P1-11 core: macOS + sandbox_mode=write + auto_allow_if_sandboxed →
    the gate must NOT auto-approve execute (zero isolation must never mean
    zero confirmation), and the user must be told why."""
    monkeypatch.setattr(repl.sys, "platform", "darwin")
    gate = repl.build_gate(_cfg(sandbox_mode="write", auto_allow_if_sandboxed=True))
    assert getattr(gate, "_auto_allow_execute", False) is False, (
        "auto-allow must be disabled when the sandbox provides no boundary"
    )
    err = capsys.readouterr().err
    assert "auto_allow_if_sandboxed" in err, "the disablement must be surfaced, never silent"


def test_auto_allow_still_works_when_sandbox_effective(monkeypatch):
    """The tightening must not break the real use case: Windows + job mode +
    opt-in → auto-allow stays on (with its known-limitation warning)."""
    monkeypatch.setattr(repl.sys, "platform", "win32")
    gate = repl.build_gate(_cfg(sandbox_mode="job", auto_allow_if_sandboxed=True))
    assert getattr(gate, "_auto_allow_execute", False) is True
