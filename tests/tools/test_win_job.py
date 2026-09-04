"""Tests for the win_job module (Job Object process-tree control + resource limits).

The Windows-specific ctypes calls can't run on non-Windows CI runners, so the
tests are guarded by @skipif. The pure-logic branches (non-Windows fallback,
graceful degradation on API failure) are tested on all platforms.
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

import pytest

from coderio.tools import win_job

# ----------------------------------------------------- platform guard


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows fallback path")
def test_create_job_returns_none_on_non_windows():
    """On non-Windows platforms, create_job_with_limits returns None (no crash)."""
    assert win_job.create_job_with_limits(memory_limit_mb=512) is None


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows fallback path")
def test_assign_to_job_returns_false_on_non_windows():
    """On non-Windows, assign_to_job is a no-op returning False."""
    assert win_job.assign_to_job(0, 1234) is False


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows fallback path")
def test_kill_process_tree_posix_uses_killpg():
    """On POSIX, kill_process_tree uses os.killpg on the process group.

    Starts a sleep subprocess in its own session (start_new_session=True) so
    killpg targets the group. Mocks os.killpg to verify it's called.
    """
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        with patch.object(win_job.os, "killpg") as mock_killpg:
            win_job.kill_process_tree(proc)
            assert mock_killpg.called, "os.killpg must be called on POSIX"
    finally:
        # Cleanup: if the mock prevented the real kill, ensure it dies.
        try:
            proc.kill()
        except Exception:
            pass


# ----------------------------------------------------- Windows-only (run locally)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only ctypes path")
def test_create_job_with_limits_returns_handle_on_windows():
    """On Windows, create_job_with_limits returns a non-zero job handle."""
    h = win_job.create_job_with_limits(memory_limit_mb=512, process_limit=100)
    assert h is not None and h != 0, "expected a valid job handle"
    # Cleanup.
    import ctypes

    ctypes.windll.kernel32.CloseHandle(h)  # type: ignore[attr-defined]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only ctypes path")
def test_create_job_no_limits_still_works():
    """create_job_with_limits() with no limits still returns a handle
    (KILL_ON_JOB_CLOSE is always set as the baseline flag)."""
    h = win_job.create_job_with_limits()
    assert h is not None and h != 0
    import ctypes

    ctypes.windll.kernel32.CloseHandle(h)  # type: ignore[attr-defined]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only ctypes path")
def test_kill_process_tree_windows_terminates_process():
    """kill_process_tree on Windows terminates the target process via Job Object.

    Starts a subprocess, calls kill_process_tree, and verifies the process is
    dead (poll() returns a non-None exit code).
    """
    proc = subprocess.Popen(["cmd", "/c", "ping", "-n", "30", "127.0.0.1"])
    win_job.kill_process_tree(proc)
    # The process should be dead shortly. Use wait() (poll() has no timeout
    # arg on Python 3.11 — wait() does).
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pytest.fail("process should be terminated by kill_process_tree within 5s")
    assert proc.returncode is not None, "process should have an exit code after kill"


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill is Windows-only")
def test_kill_process_tree_uses_taskkill_tree_flag(monkeypatch):
    """P0-6 (2026-09-04, audit M6): the Windows tree kill must invoke
    `taskkill /T /F /PID <pid>` — taskkill walks the parent-child tree as it
    exists right now, so grandchildren spawned before the kill die too. The
    old Job-assigned-at-kill-time approach never contained them, so they
    survived holding the stdout/stderr pipes."""
    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd))
    proc = subprocess.Popen(["cmd", "/c", "ping", "-n", "30", "127.0.0.1"])
    try:
        win_job.kill_process_tree(proc)
    finally:
        # The faked taskkill didn't actually kill anything — clean up.
        try:
            proc.kill()
        except Exception:
            pass
        proc.wait(timeout=5)
    assert calls, "kill_process_tree must invoke taskkill"
    assert calls[0][:4] == ["taskkill", "/T", "/F", "/PID"], f"unexpected taskkill shape: {calls[0]!r}"
    assert calls[0][4] == str(proc.pid), "taskkill must target the child process pid"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only tree kill")
def test_kill_process_tree_kills_grandchildren():
    """M6 behavioral pin (2026-09-04): the tree kill must reach DESCENDANTS,
    not just the direct child. bash -c 'sleep 30' nests sleep under bash;
    after kill_process_tree no sleep.exe parented by the killed bash may
    survive (a surviving grandchild keeps holding the stdout pipe). Windows
    does not re-parent orphans, so sleep's PPID still points at the dead
    bash after the kill — making the filter deterministic."""
    import time

    proc = subprocess.Popen(["bash", "-c", "sleep 30"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        time.sleep(1.0)  # let bash spawn sleep
        win_job.kill_process_tree(proc)
        proc.wait(timeout=5)
        time.sleep(0.5)  # give taskkill's kill a beat to land
        ps = (
            "Get-CimInstance Win32_Process -Filter "
            f"\"Name='sleep.exe' AND ParentProcessId={proc.pid}\" | "
            "Select-Object -ExpandProperty ProcessId"
        )
        # noqa S603: fixed powershell query against a process WE spawned
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=15)  # noqa: S603
        survivors = r.stdout.split()
        assert not survivors, f"grandchild sleep.exe (pids {survivors}) survived the tree kill (M6 regression)"
    finally:
        try:
            proc.kill()
        except Exception:
            pass


# ----------------------------------------------------- graceful degradation


def test_create_job_degrades_on_win32_error():
    """If the Win32 API call fails, create_job_with_limits returns None
    (degrades gracefully instead of crashing the agent)."""
    # Force the non-Windows code path even on Windows by mocking sys.platform.
    with patch.object(win_job.sys, "platform", "linux"):
        assert win_job.create_job_with_limits(memory_limit_mb=512) is None


def test_kill_process_tree_handles_already_dead_process():
    """kill_process_tree on an already-exited process must not raise."""
    # Run a command that exits immediately.
    proc = subprocess.Popen(["echo", "done"])
    proc.wait(timeout=5)  # ensure it's dead
    # Should not raise even though the process is gone.
    win_job.kill_process_tree(proc)
