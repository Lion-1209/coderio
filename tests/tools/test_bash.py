import shutil

import pytest

from coderio.tools.bash import BashTool, detect_shell

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


def test_run_echo():
    tool = BashTool()
    out = tool.run(command="echo hello-world")
    assert "hello-world" in out


def test_run_captures_stderr():
    tool = BashTool()
    out = tool.run(command="ls /nonexistent-dir-xyz 2>&1 || true")
    assert isinstance(out, str)


def test_detect_shell_returns_path():
    shell = detect_shell("")
    assert "bash" in shell.lower()


def test_run_writes_file_via_cwd(tmp_path):
    tool = BashTool()
    tool.run(command="echo done > out.txt", cwd=str(tmp_path))
    assert (tmp_path / "out.txt").exists()


def test_run_in_background_returns_pid(tmp_path):
    tool = BashTool()
    out = tool.run(command="sleep 0", run_in_background=True, cwd=str(tmp_path))
    assert "pid" in out.lower()


def test_timeout_kills_hanging_process():
    """REGRESSION: timeout must actually KILL the process and return, not hang.

    Old code used subprocess.run(timeout=...). On Windows, when the timeout
    fires, it kills only the direct child process — grandchildren (e.g. a
    hanging test's subprocesses) survive and hold the stdout/stderr pipes
    open, so subprocess.run NEVER returns. The TUI freezes indefinitely.

    Fix: Popen + manual timeout + _kill_process_tree (Windows Job Objects).
    This test verifies the timeout returns within a reasonable window (not
    1.8 hours like the real-world incident).
    """
    import time

    tool = BashTool()
    start = time.monotonic()
    # `sleep 60` inside bash spawns a child process. With the old code, the
    # timeout would fire after 2s but never return. With the fix, it returns
    # in ~2s with the timeout error message.
    out = tool.run(command="sleep 60", timeout=2)
    elapsed = time.monotonic() - start
    assert "timed out" in out.lower(), f"expected timeout message, got: {out}"
    # Must return well within the 60s sleep — if it takes >15s, the kill
    # didn't work and we're back to the old hang.
    assert elapsed < 15, (
        f"timeout kill took {elapsed:.1f}s — process tree kill failed (the old Windows subprocess.run bug is back)"
    )


def test_timeout_kills_child_process_tree():
    """The process-tree kill must reach grandchildren, not just the direct child.

    Bash spawns `sleep` as a child. If we only kill bash (the direct child),
    `sleep` keeps running and holds the pipe. This test runs a command that
    explicitly spawns a child, then verifies the timeout kills both.
    """
    import time

    tool = BashTool()
    start = time.monotonic()
    # bash -c 'sleep 30' → bash is the parent, sleep is the child.
    # The kill must reach sleep, not just bash.
    out = tool.run(command="bash -c 'sleep 30'", timeout=2)
    elapsed = time.monotonic() - start
    assert "timed out" in out.lower()
    assert elapsed < 15, f"process tree kill took {elapsed:.1f}s — grandchild not killed"
