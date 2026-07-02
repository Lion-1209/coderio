from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

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
    raise FileNotFoundError("bash not found. Install Git Bash and/or set [tools].bash_shell in config.")


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

    def run(self, command: str, timeout: int = 120, run_in_background: bool = False, cwd: str = "") -> str:
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
        try:
            proc = subprocess.run(
                [shell, "-l", "-c", command],
                cwd=work,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            return (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"
        except FileNotFoundError as e:
            return f"Error: {e}"
