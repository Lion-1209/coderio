"""Tests for the sandbox-aware permission gate builder (audit P1-11, 2026-09-04;
boundary-kind model per gpt5.6-sol review, 2026-09-05).

auto_allow_if_sandboxed may only auto-approve execute when the configured
sandbox ACTUALLY provides a filesystem write boundary — "sandbox_mode != off"
alone gave macOS / Linux-without-bwrap / Windows users zero isolation AND
zero confirmation. The boundary-kind model (filesystem / resource / none)
replaces the old boolean: auto-allow requires "filesystem".
"""

import pytest

from coderio.cli import repl
from coderio.config.models import Config, ToolsConfig


def _cfg(**tools) -> Config:
    return Config(tools=ToolsConfig(permission_mode="confirm", **tools))


def test_sandbox_boundary_off_is_none():
    assert repl._sandbox_boundary("off") == ("none", None)


def test_sandbox_boundary_windows_is_resource():
    """Windows Job Object caps process count but NOT file writes — a real
    boundary kind, not filesystem (gpt5.6-sol P1-2)."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(repl.sys, "platform", "win32")
    try:
        kind, gap = repl._sandbox_boundary("write")
        assert kind == "resource" and gap is None
    finally:
        monkeypatch.undo()


def test_sandbox_boundary_darwin_is_none():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(repl.sys, "platform", "darwin")
    try:
        kind, gap = repl._sandbox_boundary("write")
        assert kind == "none" and gap and "macOS" in gap
    finally:
        monkeypatch.undo()


def test_sandbox_boundary_linux_job_is_none():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(repl.sys, "platform", "linux")
    try:
        kind, gap = repl._sandbox_boundary("job")
        assert kind == "none" and gap and "job" in gap
    finally:
        monkeypatch.undo()


def test_sandbox_boundary_linux_write_follows_bwrap(monkeypatch):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(repl.sys, "platform", "linux")
    try:
        from coderio.tools import linux_sandbox

        monkeypatch.setattr(linux_sandbox, "bwrap_available", lambda: True)
        assert repl._sandbox_boundary("write") == ("filesystem", None)
        monkeypatch.setattr(linux_sandbox, "bwrap_available", lambda: False)
        kind, gap = repl._sandbox_boundary("write")
        assert kind == "none" and gap and "bubblewrap" in gap
    finally:
        monkeypatch.undo()


def test_auto_allow_disabled_when_boundary_is_not_filesystem(monkeypatch, capsys):
    """A1 (gpt5.6-sol P1-2): auto-allow requires "filesystem" — Windows
    resource, macOS none, Linux-no-bwrap all disable it with a printed
    reason."""
    for platform, mode in (("win32", "job"), ("darwin", "write"), ("linux", "job")):
        monkeypatch.setattr(repl.sys, "platform", platform)
        gate = repl.build_gate(_cfg(sandbox_mode=mode, auto_allow_if_sandboxed=True))
        assert getattr(gate, "_auto_allow_execute", False) is False, (
            f"auto-allow must be disabled on {platform}/{mode} (boundary is not filesystem)"
        )


def test_auto_allow_works_when_boundary_is_filesystem(monkeypatch):
    """Linux + bwrap + write mode + opt-in → auto-allow works."""
    monkeypatch.setattr(repl.sys, "platform", "linux")
    try:
        from coderio.tools import linux_sandbox

        monkeypatch.setattr(linux_sandbox, "bwrap_available", lambda: True)
    except ImportError:
        pass  # linux_sandbox module may not exist on Windows — bwrap_available just won't be monkeypatched
    gate = repl.build_gate(_cfg(sandbox_mode="write", auto_allow_if_sandboxed=True))
    assert getattr(gate, "_auto_allow_execute", False) is True


def test_gate_without_auto_allow_opt_in_stays_safe():
    """Default (auto_allow_if_sandboxed=False) → never auto-allows regardless
    of platform or sandbox."""
    gate = repl.build_gate(_cfg(sandbox_mode="write"))
    assert getattr(gate, "_auto_allow_execute", False) is False
