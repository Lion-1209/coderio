"""Exercise real process groups through the production backend."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from coderio.agent.deep_loop import make_shell_backend
from coderio.tools.linux_sandbox import bwrap_available


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
@pytest.mark.parametrize("mode", ["off", "job", "write"])
def test_timeout_kills_descendant(tmp_path: Path, mode: str) -> None:
    if mode == "write" and bwrap_available():
        pytest.skip("write uses bubblewrap here; this regression covers the plain fallback")
    pidfile = tmp_path / "child.pid"
    child = (
        f"import os,time; from pathlib import Path; Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(30)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child)} & wait"
    backend = make_shell_backend(root_dir=tmp_path, sandbox_mode=mode)
    pid = None
    try:
        result = backend.execute(command, timeout=2)
        assert result.exit_code == 124
        pid = int(pidfile.read_text())
        deadline = time.monotonic() + 2
        while True:
            state = subprocess.run(  # noqa: S603
                ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
            ).stdout.strip()
            if not state or state.startswith("Z"):
                break
            assert time.monotonic() < deadline, f"descendant {pid} survived timeout: {state}"
            time.sleep(0.05)
    finally:
        if pid is None and pidfile.exists():
            pid = int(pidfile.read_text())
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
