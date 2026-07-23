from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill a process AND all its children (the whole process tree).

    On Windows, ``subprocess.run(timeout=...)`` only kills the direct child
    when the timeout fires — the grandchildren (e.g. pytest's worker
    processes, a hanging test's threads) keep running and hold the stdout/stderr
    pipes open, so ``subprocess.run`` never actually returns. The TUI freezes
    indefinitely (observed: a pytest run with timeout=180 hung for 1.8 hours).

    Fix: use Windows Job Objects. Assigning the process to a job and then
    terminating the job kills the entire tree. On Linux/macOS we use
    ``os.killpg`` on the process group.
    """
    pid = proc.pid
    if sys.platform == "win32":
        # Use a Job Object to kill the whole tree. ctypes lets us call the
        # Win32 API without adding a dependency on pywin32.
        import ctypes

        kernel32 = ctypes.windll.kernel32
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        PROCESS_ALL_ACCESS = 0x1F0FFF

        h_job = kernel32.CreateJobObjectW(None, None)
        if h_job:
            kernel32.SetInformationJobObject(
                h_job,
                9,  # JobObjectExtendedLimitInformation = 9
                ctypes.byref(ctypes.c_ulong(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)),
                ctypes.sizeof(ctypes.c_ulong),
            )
            h_proc = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
            if h_proc:
                kernel32.AssignProcessToJobObject(h_job, h_proc)
                kernel32.CloseHandle(h_proc)
            # TerminateJobObject kills ALL processes in the job (the whole tree).
            kernel32.TerminateJobObject(h_job, 1)
            kernel32.CloseHandle(h_job)
    else:
        try:
            import signal

            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


_WIN_CANDIDATES = (
    "C:\\Program Files\\Git\\bin\\bash.exe",
    "C:\\Program Files\\Git\\usr\\bin\\bash.exe",
    "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
)


class BashArgs(BaseModel):
    command: str = Field(description="The shell command to execute.")
    timeout: int = Field(default=120, description="Timeout in seconds.")
    run_in_background: bool = Field(default=False, description="Run detached; returns a pid.")
    cwd: str = Field(default="", description="Working directory for the command.")


def detect_shell(configured: str) -> str:
    """Resolve the bash executable path. Spec §3.3.

    Order: explicit config > Windows candidates > PATH `bash`.
    """
    if configured:
        p = Path(configured)
        if p.is_file():
            return str(p)
    if sys.platform == "win32":
        for cand in _WIN_CANDIDATES:
            if Path(cand).is_file():
                return cand
    found = shutil.which("bash")
    if found:
        return found
    if sys.platform == "win32":
        raise FileNotFoundError(
            "bash not found. Install Git Bash (https://git-scm.com) and/or set [tools].bash_shell in config.toml."
        )
    raise FileNotFoundError(
        "bash not found. Install it via your package manager "
        "(e.g. apt install bash / brew install bash) "
        "and/or set [tools].bash_shell in config.toml."
    )


class BashTool:
    name = "bash"
    description = (
        "Execute a shell command via bash (Git Bash on Windows). Returns combined "
        "stdout+stderr. Supports timeout and run_in_background. Requires permission."
    )
    args_schema = BashArgs

    def __init__(self, shell: str = ""):
        self._shell = shell

    def _resolve(self):
        if self._shell and Path(self._shell).is_file():
            return self._shell
        return detect_shell(self._shell)

    def run(
        self,
        command: str,
        timeout: int = 120,
        run_in_background: bool = False,
        cwd: str = "",
    ) -> str:
        shell = self._resolve()
        work = cwd or os.getcwd()
        if run_in_background:
            proc = subprocess.Popen(
                [shell, "-l", "-c", command],
                cwd=work,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return f"Started background task (pid={proc.pid})"
        # Use Popen + manual timeout instead of subprocess.run(timeout=...).
        # subprocess.run's timeout on Windows only kills the direct child —
        # grandchildren (pytest workers, hanging test threads) survive and
        # hold the pipes open, so the call NEVER returns. The manual timeout
        # below uses _kill_process_tree (Windows Job Objects) to kill the
        # entire process tree, then closes the pipes explicitly.
        try:
            proc = subprocess.Popen(
                [shell, "-l", "-c", command],
                cwd=work,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                # On Linux/macOS, start a new process group so os.killpg works.
                start_new_session=(sys.platform != "win32"),
            )
        except FileNotFoundError as e:
            return f"Error: {e}"
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            out = (stdout or "") + (stderr or "")
            # Append exit code so the model can tell success (0) from failure
            # without guessing from the output text. Critical for the harness
            # VerifyGate: a non-zero exit means the verification attempt failed,
            # which the model should read and fix — not just "ran = verified".
            return f"{out}\n[exit_code: {proc.returncode}]"
        except subprocess.TimeoutExpired:
            # Kill the ENTIRE process tree (not just the direct child). Without
            # this, grandchildren hold the pipes open and communicate() never
            # returns — the TUI freezes indefinitely.
            _kill_process_tree(proc)
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return f"Error: command timed out after {timeout}s"
