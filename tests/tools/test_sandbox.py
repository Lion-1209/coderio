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


# --- filesystem 4-tuple (Gap 3, Claude-Code-compatible) ---


def _make_fs_config(**kwargs):
    """Build a minimal SandboxFsConfig-like stub for testing."""
    from types import SimpleNamespace

    defaults = {"allow_write": [], "deny_write": [], "deny_read": [], "allow_read": []}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_build_bwrap_args_with_allow_write():
    """fs_config.allow_write adds extra --bind (read-write) mounts."""
    cfg = _make_fs_config(allow_write=["/tmp/build", "~/.cache"])
    args = linux_sandbox.build_bwrap_args("ls", "/workspace", fs_config=cfg)
    # Each allow_write entry becomes a --bind src src pair.
    assert "--bind" in args
    # /tmp/build is absolute → passed as-is.
    idx = args.index("--bind")  # first --bind is workspace; allow_write comes after
    # Find the /tmp/build entry specifically.
    assert "/tmp/build" in args, "allow_write /tmp/build must appear in args"


def test_build_bwrap_args_with_deny_read():
    """fs_config.deny_read adds --tmpfs blackholes (path exists but empty)."""
    cfg = _make_fs_config(deny_read=["~/.ssh"])
    args = linux_sandbox.build_bwrap_args("ls", "/workspace", fs_config=cfg)
    assert "--tmpfs" in args
    # The expanded ~/.ssh path should appear (home-resolved).
    from pathlib import Path

    ssh_path = str(Path.home() / ".ssh")
    assert ssh_path in args, f"deny_read ~/.ssh must resolve to {ssh_path}"


def test_build_bwrap_args_deny_read_before_allow_read():
    """Order matters: deny_read tmpfs must come BEFORE allow_read ro-bind.

    bwrap applies later mounts on top of earlier ones, so the allow_read
    "hole punch" only works if it's mounted after the deny_read blackhole.
    """
    cfg = _make_fs_config(deny_read=["~/.ssh"], allow_read=["~/.ssh/known_hosts"])
    args = linux_sandbox.build_bwrap_args("ls", "/workspace", fs_config=cfg)
    from pathlib import Path

    ssh_path = str(Path.home() / ".ssh")
    known_hosts = str(Path.home() / ".ssh" / "known_hosts")
    # Find positions of the tmpfs (deny) and ro-bind target (allow).
    # The tmpfs arg is at position of "--tmpfs" + 1 (the path).
    deny_pos = args.index(ssh_path) if ssh_path in args else -1
    allow_pos = args.index(known_hosts) if known_hosts in args else -1
    assert deny_pos >= 0 and allow_pos >= 0, "both deny_read and allow_read paths must appear"
    assert deny_pos < allow_pos, (
        f"deny_read tmpfs (pos {deny_pos}) must come BEFORE allow_read ro-bind (pos {allow_pos}) "
        "— bwrap mounts later args on top of earlier ones"
    )


def test_resolve_fs_path_tilde_expansion():
    """~/.foo expands to home/.foo."""
    from pathlib import Path

    result = linux_sandbox._resolve_fs_path("~/.ssh", "/ws", Path.home())
    assert result == str((Path.home() / ".ssh").resolve())


def test_resolve_fs_path_relative_to_workspace():
    """./foo and bare 'foo' resolve to workspace/foo."""
    from pathlib import Path

    result1 = linux_sandbox._resolve_fs_path("./build", "/ws", Path.home())
    result2 = linux_sandbox._resolve_fs_path("build", "/ws", Path.home())
    expected = str((Path("/ws") / "build").resolve())
    assert result1 == expected
    assert result2 == expected


def test_resolve_fs_path_absolute_unchanged():
    """/abs/path passes through unchanged."""
    from pathlib import Path

    result = linux_sandbox._resolve_fs_path("/tmp/build", "/ws", Path.home())
    assert result == "/tmp/build"


# ----------------------------------------------------- sandbox_runner fallback


def test_run_with_sandbox_off_returns_minus_one():
    """mode='off' signals the caller to use its own subprocess path."""
    code, msg = sandbox_runner.run_with_sandbox("ls", ".", mode="off")
    assert code == -1
    assert "off" in msg


def test_run_with_sandbox_forwards_network_allowed_to_bwrap():
    """REGRESSION GUARD (Gap 1): run_with_sandbox MUST forward network_allowed
    to run_bwrap. Previously this parameter was omitted at the call site
    (sandbox_runner.py:67), so network_allowed=false had ZERO effect on Linux
    sandbox mode — --unshare-net was never added. This test mocks run_bwrap
    to verify the parameter actually reaches it.
    """
    import sys
    from unittest.mock import patch

    if sys.platform == "win32":
        pytest.skip("bubblewrap forwarding test is POSIX-only")

    # Patch run_bwrap to capture its kwargs (don't actually run bwrap).
    captured: dict = {}

    def _fake_run_bwrap(command, cwd, **kwargs):
        captured.update(kwargs)
        return (0, "ok")

    # Patch bwrap_available to True so the bwrap path is taken.
    with patch.object(sandbox_runner.sys, "platform", "linux"):
        with patch("coderio.tools.linux_sandbox.bwrap_available", return_value=True):
            with patch("coderio.tools.linux_sandbox.run_bwrap", side_effect=_fake_run_bwrap):
                sandbox_runner.run_with_sandbox("echo test", ".", mode="write", network_allowed=False)

    assert "network_allowed" in captured, "run_with_sandbox must forward network_allowed"
    assert captured["network_allowed"] is False, (
        "network_allowed=False must reach run_bwrap (was silently dropped before Gap 1 fix)"
    )


def test_run_with_sandbox_forwards_fs_config_to_bwrap():
    """REGRESSION GUARD (Gap 3): fs_config must be forwarded to run_bwrap so
    the filesystem 4-tuple (allow_write/deny_read/etc.) actually takes effect
    inside the sandbox."""
    import sys
    from types import SimpleNamespace
    from unittest.mock import patch

    if sys.platform == "win32":
        pytest.skip("bubblewrap forwarding test is POSIX-only")

    fs_cfg = SimpleNamespace(allow_write=["/tmp/build"], deny_read=["~/.ssh"], deny_write=[], allow_read=[])
    captured: dict = {}

    def _fake_run_bwrap(command, cwd, **kwargs):
        captured.update(kwargs)
        return (0, "ok")

    with patch.object(sandbox_runner.sys, "platform", "linux"):
        with patch("coderio.tools.linux_sandbox.bwrap_available", return_value=True):
            with patch("coderio.tools.linux_sandbox.run_bwrap", side_effect=_fake_run_bwrap):
                sandbox_runner.run_with_sandbox("echo test", ".", mode="write", fs_config=fs_cfg)

    assert captured.get("fs_config") is fs_cfg, "fs_config must be forwarded to run_bwrap"


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
def test_run_sandboxed_timeout_kills_process_quickly():
    """REGRESSION GUARD: when timeout fires, the process MUST be killed quickly.

    Before the fix, kill_process_tree was called AFTER the process had already
    spawned children (cmd /c powershell) that escaped the Job Object assignment.
    Result: timeout=2 on a `sleep 10` ran the full 10 seconds (the process
    kept running, only the exit code said 124). Now the process is created
    SUSPENDED, assigned to the Job Object, then resumed — so all descendants
    are in the job and TerminateJobObject kills them all.

    This test runs `sleep 10` with timeout=2 and asserts elapsed < 5s. If the
    regression returns (process not killed), elapsed will be ~10s and this test
    fails loudly.
    """
    import time

    start = time.time()
    code, _ = win_sandbox.run_sandboxed('powershell -Command "Start-Sleep -Seconds 10"', cwd=".", timeout=2)
    elapsed = time.time() - start
    assert code == 124, f"timeout should return exit 124, got {code}"
    assert elapsed < 5, (
        f"timeout=2 should kill the process within ~2-3s, but elapsed={elapsed:.1f}s "
        "— the process tree kill is broken (regression of the cmd/c grandchild escape bug)"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only sandbox path")
def test_run_sandboxed_timeout_kills_grandchild_process():
    """REGRESSION GUARD (the original bug scenario): a `cmd /c <grandchild>` chain
    must have the grandchild killed on timeout too.

    This is the EXACT scenario that was broken: cmd.exe spawns powershell.exe
    as a grandchild; the old code assigned only cmd.exe to the Job Object AFTER
    it started, so powershell.exe escaped and kept running. The suspended-create
    + assign-before-resume fix ensures all descendants are in the job.
    """
    import time

    start = time.time()
    # cmd /c powershell = the grandchild-spawning chain that broke before.
    code, _ = win_sandbox.run_sandboxed('cmd /c "powershell -Command Start-Sleep -Seconds 8"', cwd=".", timeout=2)
    elapsed = time.time() - start
    assert code == 124, f"timeout should return exit 124, got {code}"
    assert elapsed < 5, (
        f"grandchild (powershell under cmd /c) must be killed on timeout, "
        f"but elapsed={elapsed:.1f}s — the job-assign-before-resume fix regressed"
    )


# NOTE on the absence of write-isolation tests:
#
# Before this cleanup, there were two tests (test_run_sandboxed_denies_system_dir_write
# and test_run_sandboxed_allows_workspace_write) claiming to verify OS-level write
# isolation. They were INVALID: the deny test wrote to C:\Windows, which a normal
# user can't write to REGARDLESS of sandbox, so it passed even with no sandbox at all.
#
# Writing a valid isolation test requires a path where:
#   (a) the user normally HAS write permission, AND
#   (b) the sandbox DENIES that write.
#
# On Windows with the current LUA_TOKEN implementation, no such path exists —
# CreateRestrictedToken with no SID lists returns an equivalent token (verified:
# original and restricted tokens both have Medium integrity 0x2000). The Windows
# write-isolation feature is therefore currently a no-op; a valid test cannot be
# written until real per-directory ACLs (SetEntriesInAcl + SetSecurityInfo) are
# added. See win_sandbox.py docstring for the honest status.
#
# Do NOT re-add a C:\Windows write test claiming to verify isolation — it proves
# nothing (the user already lacks permission there).


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


# ----------------------------------------------------- sandbox_runner: subprocess fallback (mocked POSIX)


def test_run_with_sandbox_write_none_fs_config_auto_constructs_default(tmp_path):
    """REGRESSION GUARD (BUG C, 2026-08-18): when fs_config is None and mode
    is 'write', run_with_sandbox MUST auto-construct a SandboxFsConfig with
    the default deny_write=['~/.coderio']. Before the fix, fs_config=None
    meant bwrap got NO deny_write, so sandboxed commands could write the
    trust store. We mock the POSIX path on Windows to verify the construction."""
    from unittest.mock import patch

    from coderio.config.models import SandboxFsConfig

    captured_fs = {}

    def _fake_run_bwrap(command, cwd, **kwargs):
        captured_fs["fs_config"] = kwargs.get("fs_config")
        return (0, "ok")

    with patch.object(sandbox_runner.sys, "platform", "linux"):
        with patch("coderio.tools.linux_sandbox.bwrap_available", return_value=True):
            with patch("coderio.tools.linux_sandbox.run_bwrap", side_effect=_fake_run_bwrap):
                sandbox_runner.run_with_sandbox("echo test", str(tmp_path), mode="write", fs_config=None)

    fs = captured_fs.get("fs_config")
    assert fs is not None, "fs_config=None must be auto-constructed"
    assert isinstance(fs, SandboxFsConfig), f"expected SandboxFsConfig, got {type(fs)}"
    assert "~/.coderio" in fs.deny_write, (
        f"auto-constructed SandboxFsConfig must have default deny_write, got {fs.deny_write}"
    )


def test_run_with_sandbox_write_explicit_fs_config_not_overridden(tmp_path):
    """An explicit fs_config (even deny_write=[]) must pass through unchanged."""
    from types import SimpleNamespace
    from unittest.mock import patch

    captured_fs = {}

    def _fake_run_bwrap(command, cwd, **kwargs):
        captured_fs["fs_config"] = kwargs.get("fs_config")
        return (0, "ok")

    explicit = SimpleNamespace(allow_write=[], deny_write=[], deny_read=[], allow_read=[])
    with patch.object(sandbox_runner.sys, "platform", "linux"):
        with patch("coderio.tools.linux_sandbox.bwrap_available", return_value=True):
            with patch("coderio.tools.linux_sandbox.run_bwrap", side_effect=_fake_run_bwrap):
                sandbox_runner.run_with_sandbox("echo test", str(tmp_path), mode="write", fs_config=explicit)

    assert captured_fs["fs_config"] is explicit, "explicit fs_config must not be replaced"


def test_run_with_sandbox_plain_subprocess_timeout_returns_124():
    """POSIX plain subprocess (mode='job' fallback) must return exit 124 on timeout."""
    if sys.platform == "win32":
        pytest.skip("POSIX plain subprocess test requires non-Windows platform")
    code, output = sandbox_runner.run_with_sandbox("sleep 10", ".", mode="job", timeout=2)
    assert code == 124, f"timeout should return 124, got {code}: {output}"


def test_run_with_sandbox_plain_subprocess_truncates_output():
    """Output exceeding max_output_bytes is truncated with a message."""
    if sys.platform == "win32":
        pytest.skip("POSIX plain subprocess test requires non-Windows platform")
    # Generate ~500 bytes, cap at 100.
    code, output = sandbox_runner.run_with_sandbox(
        "python3 -c \"print('X'*500)\"", ".", mode="job", max_output_bytes=100
    )
    assert code == 0, f"command should succeed, got {code}: {output}"
    assert "truncated" in output.lower(), f"output should be truncated, got {len(output)} bytes"
    assert len(output) <= 200, f"truncated output should be small, got {len(output)} bytes"


def test_run_with_sandbox_plain_subprocess_stderr_in_output():
    """stderr from the command must appear in the combined output."""
    if sys.platform == "win32":
        pytest.skip("POSIX plain subprocess test requires non-Windows platform")
    code, output = sandbox_runner.run_with_sandbox(
        "python3 -c \"import sys; sys.stderr.write('ERRSTREAM')\"", ".", mode="job"
    )
    assert code == 0
    assert "ERRSTREAM" in output, f"stderr must be captured, got: {output!r}"


def test_run_with_sandbox_env_forwarded_to_subprocess():
    """The env dict must be forwarded to the subprocess (not leaked to parent env)."""
    if sys.platform == "win32":
        pytest.skip("POSIX plain subprocess test requires non-Windows platform")
    env = {"SANDBOX_TEST_VAR": "sandbox-value-42"}
    code, output = sandbox_runner.run_with_sandbox(
        "python3 -c \"import os; print(os.environ.get('SANDBOX_TEST_VAR',''))\"",
        ".",
        mode="job",
        env=env,
    )
    assert code == 0
    assert "sandbox-value-42" in output, f"env var must be forwarded, got: {output!r}"
