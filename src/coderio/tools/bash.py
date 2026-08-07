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
        #
        # REGRESSION (2026-08-07 report P2-5): the old code passed a 4-byte
        # c_ulong buffer to SetInformationJobObject for
        # JobObjectExtendedLimitInformation (info class 9), which actually
        # expects a ~144-byte JOBOBJECT_EXTENDED_LIMIT_INFORMATION struct.
        # The malformed call silently failed (returned FALSE), so the
        # KILL_ON_JOB_CLOSE limit was never set — only TerminateJobObject
        # (which needs no limit info) actually did the killing. Now we build
        # the correct struct so BOTH mechanisms work: the limit kills the tree
        # when the handle closes, and TerminateJobObject kills it immediately.
        import ctypes

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_ulong),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_ulong),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_ulong),
                ("SchedulingClass", ctypes.c_ulong),
            ]

        class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000 — when the last handle to
        # the job closes, all processes in the job are terminated.
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        PROCESS_ALL_ACCESS = 0x1F0FFF

        h_job = kernel32.CreateJobObjectW(None, None)
        if h_job:
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject(
                h_job,
                9,  # JobObjectExtendedLimitInformation
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            h_proc = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
            if h_proc:
                kernel32.AssignProcessToJobObject(h_job, h_proc)
                kernel32.CloseHandle(h_proc)
            # TerminateJobObject kills ALL processes in the job (the whole tree)
            # immediately — the KILL_ON_JOB_CLOSE limit above is a safety net for
            # any process that might have escaped assignment.
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
        "Execute a shell command via bash (Git Bash on Windows, native bash on Linux/macOS). "
        "Returns combined stdout+stderr with an [exit_code: N] marker. "
        "Supports timeout (default 120s) and run_in_background.\n\n"
        "ENVIRONMENT DISCOVERY (important on Windows): the bash shell inherits the system "
        "PATH, which may differ from the environment coderio itself runs in. Before assuming "
        "'the environment is broken', discover what's actually available:\n"
        "  - `which python python3 py 2>/dev/null` — find which Python interpreters exist\n"
        "  - Check for a project virtual environment: `ls .venv/Scripts/python.exe` "
        "(Windows) or `ls venv/bin/python` (Linux/macOS). If found, use that path directly: "
        "`.venv/Scripts/python.exe -m pytest` instead of bare `python -m pytest`.\n"
        "  - `py -3 --version` — Windows Python launcher may find a Python 3 even when "
        "`python` resolves to Python 2.\n"
        "A bare `python` that fails does NOT mean dependencies are missing. Always verify "
        "with the correct interpreter before concluding the environment is broken."
    )
    args_schema = BashArgs

    def __init__(self, shell: str = ""):
        self._shell = shell

    def _resolve(self):
        if self._shell and Path(self._shell).is_file():
            return self._shell
        return detect_shell(self._shell)

    @staticmethod
    def _prepend_venv_activate(command: str, cwd: str) -> str:
        """Prepend 'source .venv/bin/activate' (or Scripts path on Windows) if
        a virtual environment exists in the working directory.

        This ensures `python` / `pip` in the bash command resolve to the
        project's venv interpreter, not a random system Python. The check is
        purely additive — if no .venv exists, the command runs unchanged.
        """
        if not command or not cwd:
            return command
        work = Path(cwd)
        if sys.platform == "win32":
            activate = work / ".venv" / "Scripts" / "activate"
        else:
            activate = work / ".venv" / "bin" / "activate"
        if not activate.is_file():
            return command
        # Use 'source' (bash builtin) to activate in the same shell session.
        # 'command' runs after activation, so `python` now points to .venv.
        return f"source '{activate}' 2>/dev/null; {command}"

    def run(
        self,
        command: str,
        timeout: int = 120,
        run_in_background: bool = False,
        cwd: str = "",
    ) -> str:
        shell = self._resolve()
        work = cwd or os.getcwd()
        # Auto-activate a project virtual environment if one exists in the
        # working directory. Without this, bash's login shell inherits the
        # system PATH, where `python` may resolve to a wrong interpreter (e.g.
        # Python 2.7 on Windows, or no python at all on macOS). The agent then
        # sees 'No module named pytest' even though coderio itself runs fine
        # with all deps installed in .venv.
        command = self._prepend_venv_activate(command, work)
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
