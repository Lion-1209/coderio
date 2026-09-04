"""Cross-platform sandbox dispatcher.

Selects the appropriate OS-level sandbox based on platform + mode:
  - Windows: win_sandbox.py (Restricted Token + Job Object)
  - Linux:   linux_sandbox.py (bubblewrap)
  - macOS:   linux_sandbox.py fallback (bubblewrap if available, else off)

Modes (match config ToolsConfig.sandbox_mode):
  - "off":   no sandbox — caller should use plain subprocess (we return -1
             so the caller falls back to its own subprocess path).
  - "job":   Windows Job Object (resource limits + process-tree kill only).
             On Linux, equivalent to plain run (POSIX process groups already
             provide reliable kill; resource limits need cgroups — future work).
  - "write": Windows Restricted Token (file-write isolation). On Linux,
             bubblewrap (read-only /, write to workspace only).

This module is the single import point for the shell backend — it keeps the
platform branching out of deep_loop.py so the backend stays readable.
"""

from __future__ import annotations

import logging
import sys

_log = logging.getLogger(__name__)


def run_with_sandbox(
    command: str,
    cwd: str,
    *,
    mode: str = "job",
    timeout: int = 120,
    env: dict | None = None,
    max_output_bytes: int = 100_000,
    network_allowed: bool = True,
    fs_config=None,
) -> tuple[int, str]:
    """Run a command with the requested sandbox mode.

    Returns (exit_code, combined_output). On any sandbox failure, degrades to
    plain subprocess.run (returns -1 only if even the fallback fails — callers
    should treat -1 as "retry without sandbox").

    The caller (shell backend's execute) passes mode through from config. If
    the sandbox isn't available on this platform (e.g. "write" on a Linux
    without bubblewrap), we log + fall back to subprocess — never block work.

    Args:
        network_allowed: forwarded to bubblewrap as ``--unshare-net`` when
            False. REGRESSION GUARD: this was previously not forwarded (the
            ``run_bwrap`` call omitted it), so ``network_allowed=false`` had
            zero effect on Linux sandbox mode — a silent security gap.
        fs_config: optional SandboxFsConfig for filesystem allow/deny lists
            (bubblewrap only; Windows ignores it — token is a no-op there).
    """
    if mode == "off":
        # Shouldn't reach here (caller checks mode before calling), but handle
        # it defensively by signalling "use your own subprocess path".
        return (-1, "sandbox mode is off")

    if sys.platform == "win32":
        from coderio.tools.win_sandbox import run_sandboxed

        # Windows: fs_config not yet applied (token is no-op; ACL is TODO).
        return run_sandboxed(command, cwd, timeout=timeout, env=env, max_output_bytes=max_output_bytes)

    # POSIX: try bubblewrap (linux_sandbox module) for "write" mode; for "job"
    # mode there's no POSIX equivalent of Job Object resource limits without
    # cgroups, so we fall through to plain subprocess (with start_new_session
    # for at least reliable process-group kill).
    degraded: str | None = None  # set when the configured sandbox can't run
    if mode == "write":
        try:
            from coderio.tools.linux_sandbox import bwrap_available, run_bwrap

            if bwrap_available():
                # fs_config=None leaves bwrap's built-in layout with NO
                # deny_write — meaning sandboxed commands could write
                # ~/.coderio (config/credentials/TRUST STORE). The field
                # default on SandboxFsConfig only applies when the config
                # TABLE exists; for everyone else, construct it here
                # (2026-08-18 self-audit BUG C). Explicit user configs
                # (including deny_write=[]) pass through unchanged.
                if fs_config is None:
                    from coderio.config.models import SandboxFsConfig

                    fs_config = SandboxFsConfig()
                return run_bwrap(
                    command,
                    cwd,
                    timeout=timeout,
                    env=env,
                    max_output_bytes=max_output_bytes,
                    network_allowed=network_allowed,
                    fs_config=fs_config,
                )
            degraded = "bubblewrap not installed"
            _log.warning("sandbox_mode=write but bubblewrap not installed — falling back to plain run")
        except ImportError:
            degraded = "linux_sandbox module unavailable"
            _log.warning("linux_sandbox not available — falling back to plain run")

    # Fallback: plain subprocess with start_new_session (for POSIX process-group kill).
    import subprocess

    try:
        kwargs: dict = {"shell": True, "capture_output": True, "cwd": cwd, "timeout": timeout, "text": False}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True  # own process group → killpg works
        if env is not None:
            kwargs["env"] = env
        kwargs["stdin"] = subprocess.DEVNULL
        proc = subprocess.run(command, **kwargs)
        stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        output = stdout
        if stderr:
            output += f"\n[stderr]\n{stderr}"
        if degraded:
            # Model-visible marker (audit P1-11): a degraded sandbox run must
            # not be indistinguishable from a sandboxed one — the model (and
            # the user reading the transcript) should know the configured
            # isolation did NOT apply to this output.
            output = f"[sandbox unavailable: {degraded} — ran WITHOUT the configured write sandbox]\n{output}"
        if len(output) > max_output_bytes:
            output = output[:max_output_bytes] + f"\n\n... Output truncated at {max_output_bytes} bytes."
        return (proc.returncode, output)
    except subprocess.TimeoutExpired:
        return (124, f"Command timed out after {timeout}s")
    except Exception as e:  # noqa: BLE001
        return (1, f"Execution error: {e}")
