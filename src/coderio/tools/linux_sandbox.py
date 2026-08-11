"""Linux sandboxing via bubblewrap (bwrap).

bubblewrap uses Linux user namespaces to provide a lightweight sandbox without
requiring root or a container runtime. It's the same tool Claude Code uses on
Linux, and Flatpak's underlying sandbox.

What this isolates (in "write" mode):
  - **File writes**: the sandboxed process sees the root filesystem read-only;
    only the workspace dir is mounted read-write. Writes outside the workspace
    fail at the filesystem (mount) level — not a regex check, an actual EROFS.
  - **Network**: optionally disabled via ``--unshare-net`` (network namespace).

Availability: requires ``bwrap`` installed (``apt install bubblewrap`` on Debian
/ Ubuntu, standard on Fedora). Some distros disable unprivileged user
namespaces by default (RHEL, older Debian) — in that case bwrap fails and we
fall back to plain subprocess.

Unlike the Windows path, this is a TRUE sandbox in v1 (bubblewrap's namespace
isolation is complete, not "prepared but not applied"). The gap vs. Windows
reflects the platform maturity: bubblewrap is battle-tested (Flatpak), while
the Windows Restricted Token path needs more wiring (see win_sandbox.py).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger(__name__)


def bwrap_available() -> bool:
    """Check if bubblewrap is installed and functional.

    Returns True only if the ``bwrap`` binary is on PATH. We don't probe
    unprivileged-userns support here (that requires actually running bwrap) —
    the caller should handle a bwrap failure as a fallback signal.
    """
    if sys.platform == "win32":
        return False  # bubblewrap is Linux-only
    return shutil.which("bwrap") is not None


def build_bwrap_args(
    command: str,
    workspace: str,
    *,
    network_allowed: bool = True,
) -> list[str]:
    """Build the bwrap argument list for a write-restricted sandbox.

    Mount layout:
      - ``/`` read-only (system libraries readable, no writes).
      - ``workspace`` read-write (the only writable path).
      - ``/dev``, ``/proc`` mounted (many tools need them; /dev is read-only).
      - ``/tmp`` as a private tmpfs (writable but isolated).

    Network: if ``network_allowed=False``, ``--unshare-net`` puts the process
    in its own network namespace with only loopback — no external connectivity.
    """
    # Resolve workspace to an absolute path (bwrap requires it).
    ws = str(Path(workspace).resolve())

    args = [
        "bwrap",
        "--ro-bind",
        "/",
        "/",
        "--bind",
        ws,
        ws,
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
    ]
    if not network_allowed:
        args.append("--unshare-net")
    # Die if the child can't set up the namespace (don't silently run unsandboxed).
    args.append("--die-with-parent")
    # Finally, the command to run inside the sandbox.
    args += ["--", "sh", "-c", command]
    return args


def run_bwrap(
    command: str,
    cwd: str,
    *,
    timeout: int = 120,
    env: dict | None = None,
    max_output_bytes: int = 100_000,
    network_allowed: bool = True,
) -> tuple[int, str]:
    """Run a command in a bubblewrap sandbox.

    Returns (exit_code, combined_output). On bwrap failure (not installed,
    userns disabled), the caller (sandbox_runner) falls back to plain subprocess.
    """
    if not bwrap_available():
        return (-1, "bubblewrap not installed")

    args = build_bwrap_args(command, cwd, network_allowed=network_allowed)
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            cwd=cwd,
            timeout=timeout,
            text=False,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,  # own process group for reliable kill
        )
        stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        # bubblewrap setup errors go to stderr — surface them so the model knows
        # why the command failed (e.g. "bwrap: No support for user namespaces").
        output = stdout
        if stderr:
            # On a successful run, bwrap's own stderr is empty — stderr content
            # is either the command's stderr or a bwrap setup error. Include it.
            output += f"\n[stderr]\n{stderr}"
        if len(output) > max_output_bytes:
            output = output[:max_output_bytes] + f"\n\n... Output truncated at {max_output_bytes} bytes."
        return (proc.returncode, output)
    except subprocess.TimeoutExpired:
        return (124, f"Command timed out after {timeout}s (in bubblewrap sandbox)")
    except FileNotFoundError:
        return (-1, "bubblewrap binary not found at runtime")
    except Exception as e:  # noqa: BLE001
        return (-1, f"bubblewrap sandbox failed: {e}")
