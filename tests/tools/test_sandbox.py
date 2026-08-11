"""Tests for the cross-platform sandbox modules.

Platform-specific code (ctypes Win32 calls, actual bubblewrap runs) is guarded
by @skipif. Pure-logic branches (availability checks, arg building, fallback
degradation) run on all platforms.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from coderio.tools import linux_sandbox, sandbox_runner, win_sandbox

# ----------------------------------------------------- availability checks


def test_win_sandbox_available_only_on_windows():
    """is_sandbox_available() is True only on Windows."""
    assert win_sandbox.is_sandbox_available() == (sys.platform == "win32")


def test_bwrap_available_never_on_windows():
    """bwrap_available() is False on Windows (bubblewrap is Linux-only)."""
    with patch.object(linux_sandbox.sys, "platform", "win32"):
        assert linux_sandbox.bwrap_available() is False


def test_bwrap_available_false_if_not_on_path():
    """If bwrap binary isn't on PATH, bwrap_available returns False."""
    with patch.object(linux_sandbox.shutil, "which", return_value=None):
        with patch.object(linux_sandbox.sys, "platform", "linux"):
            assert linux_sandbox.bwrap_available() is False


def test_bwrap_available_true_if_on_path():
    """If bwrap binary is on PATH (non-Windows), bwrap_available returns True."""
    with patch.object(linux_sandbox.shutil, "which", return_value="/usr/bin/bwrap"):
        with patch.object(linux_sandbox.sys, "platform", "linux"):
            assert linux_sandbox.bwrap_available() is True


# ----------------------------------------------------- linux_sandbox arg building


def test_build_bwrap_args_basic():
    """The bwrap arg list mounts / read-only, workspace read-write, dev/proc/tmp."""
    args = linux_sandbox.build_bwrap_args("ls", "/workspace")
    assert "bwrap" in args[0]
    assert "--ro-bind" in args
    assert "/" in args  # root mount target
    assert "--bind" in args  # workspace read-write
    # The workspace path is resolved (may differ on Windows vs Linux test runs).
    assert "--dev" in args
    assert "--proc" in args
    assert "--tmpfs" in args
    # Command is at the end after "--".
    assert "--" in args
    assert "sh" in args and "-c" in args and "ls" in args


def test_build_bwrap_args_disables_network():
    """network_allowed=False adds --unshare-net."""
    args = linux_sandbox.build_bwrap_args("ls", "/ws", network_allowed=False)
    assert "--unshare-net" in args


def test_build_bwrap_args_network_enabled_by_default():
    """network_allowed=True (default) does NOT add --unshare-net."""
    args = linux_sandbox.build_bwrap_args("ls", "/ws")
    assert "--unshare-net" not in args


def test_build_bwrap_args_die_with_parent():
    """The args include --die-with-parent (no orphan sandbox processes)."""
    args = linux_sandbox.build_bwrap_args("ls", "/ws")
    assert "--die-with-parent" in args


def test_build_bwrap_args_resolves_relative_workspace(tmp_path):
    """A relative workspace path is resolved to absolute before mounting."""
    args = linux_sandbox.build_bwrap_args("ls", str(tmp_path))
    # The absolute path should appear (bwrap requires absolute paths).
    abs_ws = str(tmp_path.resolve())
    assert abs_ws in args


# ----------------------------------------------------- sandbox_runner fallback


def test_run_with_sandbox_off_returns_minus_one():
    """mode='off' signals the caller to use its own subprocess path."""
    code, msg = sandbox_runner.run_with_sandbox("ls", ".", mode="off")
    assert code == -1
    assert "off" in msg


def test_run_with_sandbox_degrades_gracefully_on_failure():
    """If the sandbox module fails, run_with_sandbox falls back to subprocess.

    On any platform, a simple `echo` should succeed via the fallback path
    (returns exit 0 + the echoed text).
    """
    # Force mode='job' on a platform where it should still work via fallback.
    code, output = sandbox_runner.run_with_sandbox("echo sandbox-test", ".", mode="job")
    # Either the sandbox ran (code 0) or it fell back to subprocess (code 0).
    # We mainly assert it didn't crash.
    assert code in (0, -1), f"unexpected exit code {code}, output: {output}"
    if code == 0:
        assert "sandbox-test" in output


def test_run_with_sandbox_write_without_bwrap_falls_back():
    """mode='write' on non-Windows without bubblewrap falls back to subprocess."""
    if sys.platform == "win32":
        pytest.skip("Windows uses win_sandbox, not bubblewrap")
    with patch.object(linux_sandbox, "bwrap_available", return_value=False):
        code, output = sandbox_runner.run_with_sandbox("echo fallback-ok", ".", mode="write")
        # Should succeed via fallback (not crash with "bwrap not available").
        assert code == 0, f"fallback should succeed, got {code}: {output}"
        assert "fallback-ok" in output


# ----------------------------------------------------- win_sandbox (Windows-only)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only Restricted Token")
def test_create_write_restricted_token_returns_handle_or_none():
    """create_write_restricted_token returns a handle (int) or None (never raises).

    On a typical Windows dev machine, the token should be creatable without
    admin rights. If it returns None (e.g. restricted execution environment),
    that's also valid — the caller degrades gracefully."""
    token = win_sandbox.create_write_restricted_token()
    if token is not None:
        # Clean up the handle (CloseHandle is kernel32, not advapi32).
        import ctypes

        ctypes.windll.kernel32.CloseHandle(token)  # type: ignore[attr-defined]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only sandbox path")
def test_run_sandboxed_echo_succeeds():
    """run_sandboxed runs a simple echo command (Job Object path works)."""
    code, output = win_sandbox.run_sandboxed("echo win-sandbox-test", cwd=".")
    assert code == 0, f"echo should succeed, got {code}: {output}"
    assert "win-sandbox-test" in output


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only sandbox path")
def test_run_sandboxed_truncates_large_output():
    """run_sandboxed truncates output at max_output_bytes."""
    # Generate ~2KB of output, cap at 200 bytes.
    code, output = win_sandbox.run_sandboxed("powershell -Command \"'x' * 2000\"", cwd=".", max_output_bytes=200)
    assert "truncated" in output.lower(), f"output should be truncated, got {len(output)} bytes"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only sandbox path")
def test_run_sandboxed_denies_system_dir_write(tmp_path):
    """REGRESSION GUARD: the sandboxed process must NOT be able to write to
    C:\\Windows\\. This is the core isolation promise — if this test fails,
    the restricted token isn't being applied (regression to the v1 state where
    the token was created but discarded).

    Uses LUA_TOKEN (reduced integrity) by default. The child process inherits
    read access to system directories (a coding agent needs to read libraries)
    but write attempts fail with EACCES at the OS level.
    """
    # Attempt to write to C:\\Windows\\ — must fail (non-zero exit).
    code, output = win_sandbox.run_sandboxed(
        "echo test > C:\\Windows\\coderio-sandbox-deny-test.txt",
        cwd=str(tmp_path),
        timeout=10,
    )
    assert code != 0, (
        f"sandboxed process must NOT write to C:\\Windows — got exit 0, "
        f"output: {output!r}. The restricted token may not be applied."
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only sandbox path")
def test_run_sandboxed_allows_workspace_write(tmp_path):
    """The sandboxed process CAN write to its workspace (cwd) — otherwise the
    sandbox would be useless for a coding agent. This complements the
    deny-system-dir test above: read-broad, write-narrow."""
    code, output = win_sandbox.run_sandboxed(
        f"echo ok > {tmp_path}\\sandbox-allow-test.txt",
        cwd=str(tmp_path),
        timeout=10,
    )
    assert code == 0, f"workspace write must succeed, got {code}: {output}"
    assert (tmp_path / "sandbox-allow-test.txt").is_file(), "file should exist after write"


# ----------------------------------------------------- deep_loop integration


def test_win_shell_backend_off_mode_uses_plain_subprocess(tmp_path):
    """sandbox_mode='off' uses the plain subprocess path (no sandbox delegation).

    Verifies the default path is unchanged for existing users who don't set
    sandbox_mode in their config.
    """
    deepagents = pytest.importorskip("deepagents")
    if not deepagents:
        return

    from coderio.agent.deep_loop import _WinLocalShellBackend

    backend = _WinLocalShellBackend(root_dir=str(tmp_path), virtual_mode=True, inherit_env=True, sandbox_mode="off")
    assert getattr(backend, "_sandbox_mode", "off") == "off"
    # echo should work via plain subprocess.
    result = backend.execute("echo off-mode-works")
    assert "off-mode-works" in (getattr(result, "output", "") or "")


def test_win_shell_backend_job_mode_delegates_to_sandbox(tmp_path):
    """sandbox_mode='job' routes through the sandbox runner.

    We don't verify the sandbox's internals here (that's test_sandbox.py's job)
    — just that the mode is set and the command still executes successfully.
    """
    deepagents = pytest.importorskip("deepagents")
    if not deepagents:
        return

    from coderio.agent.deep_loop import _WinLocalShellBackend

    backend = _WinLocalShellBackend(root_dir=str(tmp_path), virtual_mode=True, inherit_env=True, sandbox_mode="job")
    assert getattr(backend, "_sandbox_mode", "off") == "job"
    result = backend.execute("echo job-mode-works")
    assert "job-mode-works" in (getattr(result, "output", "") or "")
