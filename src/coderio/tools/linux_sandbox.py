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
    fs_config=None,
) -> list[str]:
    """Build the bwrap argument list for a write-restricted sandbox.

    Mount layout (default, no fs_config):
      - ``/`` read-only (system libraries readable, no writes).
      - ``workspace`` read-write (the only writable path).
      - ``/dev``, ``/proc`` mounted (many tools need them; /dev is read-only).
      - ``/tmp`` as a private tmpfs (writable but isolated).

    Network: if ``network_allowed=False``, ``--unshare-net`` puts the process
    in its own network namespace with only loopback — no external connectivity.

    Filesystem config (Claude-Code-compatible four-tuple, when fs_config given):
      - ``allow_write``: extra read-write mounts beyond workspace.
      - ``deny_write``: read-only overrides (workspace subpaths forced read-only).
      - ``deny_read``: tmpfs blackholes (path exists but contents invisible).
      - ``allow_read``: read-only re-mounts that punch through a deny_read.
    Paths support ``~`` (home) and ``./`` (workspace-relative) prefixes.
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
    # Filesystem four-tuple (Gap 3). Each path is expanded and appended in
    # order; bwrap applies later mounts on top of earlier ones, so deny_read's
    # tmpfs must come before allow_read's ro-bind to "punch a hole".
    if fs_config is not None:
        from pathlib import Path as _Path

        home = _Path.home()
        # allow_write: extra read-write mounts (e.g. /tmp/build, ~/.cache).
        for p in getattr(fs_config, "allow_write", []) or []:
            resolved = _resolve_fs_path(p, ws, home)
            args += ["--bind", resolved, resolved]
        # deny_write: read-only overrides (force a path read-only even if it
        # would otherwise be writable, e.g. .git/hooks inside workspace).
        for p in getattr(fs_config, "deny_write", []) or []:
            resolved = _resolve_fs_path(p, ws, home)
            args += ["--ro-bind", resolved, resolved]
        # deny_read: tmpfs blackholes (path exists but is empty — hides the
        # real contents, e.g. ~/.ssh appears as an empty dir).
        for p in getattr(fs_config, "deny_read", []) or []:
            resolved = _resolve_fs_path(p, ws, home)
            args += ["--tmpfs", resolved]
        # allow_read: read-only re-mounts that punch through a deny_read
        # blackhole. MUST come after the deny_read tmpfs to take effect.
        for p in getattr(fs_config, "allow_read", []) or []:
            resolved = _resolve_fs_path(p, ws, home)
            args += ["--ro-bind", resolved, resolved]
    # Die if the child can't set up the namespace (don't silently run unsandboxed).
    args.append("--die-with-parent")
    # Finally, the command to run inside the sandbox.
    args += ["--", "sh", "-c", command]
    return args


def _resolve_fs_path(path: str, workspace: str, home) -> str:
    """Expand ~ and relative paths in a filesystem config entry.

    - ``~/foo`` → ``{home}/foo``
    - ``./foo`` or ``foo`` → ``{workspace}/foo`` (project-relative)
    - ``/abs/path`` → unchanged
    """
    if path.startswith("~"):
        return str((home / path[2:]).resolve()) if path.startswith("~/") else str(home.resolve())
    if path.startswith("./"):
        return str((Path(workspace) / path[2:]).resolve())
    if not path.startswith("/"):
        # Bare relative path → workspace-relative (same as ./).
        return str((Path(workspace) / path).resolve())
    return path


def run_bwrap(
    command: str,
    cwd: str,
    *,
    timeout: int = 120,
    env: dict | None = None,
    max_output_bytes: int = 100_000,
    network_allowed: bool = True,
    fs_config=None,
) -> tuple[int, str]:
    """Run a command in a bubblewrap sandbox.

    Returns (exit_code, combined_output). On bwrap failure (not installed,
    userns disabled), the caller (sandbox_runner) falls back to plain subprocess.

    Args:
        network_allowed: if False, adds ``--unshare-net`` (own network namespace,
            only loopback — no external connectivity). Prevents ``curl``/``wget``
            in shell commands from exfiltrating data.
        fs_config: optional SandboxFsConfig with allow_write/deny_write/deny_read/
            allow_read lists for per-path filesystem isolation.
    """
    if not bwrap_available():
        return (-1, "bubblewrap not installed")

    args = build_bwrap_args(command, cwd, network_allowed=network_allowed, fs_config=fs_config)
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
