"""Windows Job Object helpers for process-tree control and resource limits.

A Job Object groups processes so they can be controlled as a unit: kill the
whole tree, cap memory/CPU/process-count, etc. This module provides:

- ``create_job_with_limits``: create a Job Object with resource limits set.
- ``assign_to_job``: attach a process (and its children) to a job.
- ``kill_process_tree``: kill a process and all its descendants (the original
  use case in bash.py, now shared with the production shell backend).

On non-Windows platforms these are no-ops / use ``os.killpg`` instead — callers
don't need to branch on ``sys.platform``.

Resource limits applied by default (when ``memory_limit_mb`` / ``process_limit``
are provided):
  - ``JOB_OBJECT_LIMIT_PROCESS_MEMORY``: per-process memory cap (prevents a
    single runaway test from OOM-ing the machine).
  - ``JOB_OBJECT_LIMIT_ACTIVE_PROCESS``: max processes in the job (prevents
    fork bombs from spawning unbounded children).
  - ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``: when the job handle closes (e.g.
    coderio crashes), all child processes are terminated — no orphans.

These are RESOURCE limits, not a security sandbox. A process in a job can still
read/write any file the user has access to. For file-write isolation see
``win_sandbox.py`` (Restricted Token).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import subprocess

_log = logging.getLogger(__name__)

# Windows Job Object limit flags (winnt.h JOB_OBJECT_LIMIT_*).
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x1000
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x0008


def _build_win32_structs():
    """Build the ctypes Structures needed for Job Object calls.

    Lazily imported/built so non-Windows platforms don't pay the ctypes import
    cost. Returns (kernel32, IO_COUNTERS, BASIC_LIMIT, EXTENDED_LIMIT) or raises
    ImportError on non-Windows.
    """
    if sys.platform != "win32":
        raise ImportError("win_job is Windows-only")

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

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    return kernel32, _IO_COUNTERS, _JOBOBJECT_BASIC_LIMIT_INFORMATION, _JOBOBJECT_EXTENDED_LIMIT_INFORMATION


def create_job_with_limits(
    *,
    memory_limit_mb: int | None = None,
    process_limit: int | None = None,
) -> int | None:
    """Create a Job Object with the given resource limits.

    Returns the job handle (int) on success, or None on failure (non-Windows,
    or Win32 API error — caller should degrade gracefully). The caller owns the
    handle and must CloseHandle it when done (or let process exit close it,
    which triggers KILL_ON_JOB_CLOSE).

    Args:
        memory_limit_mb: per-process memory cap in MB. None = no limit.
        process_limit: max processes in the job. None = no limit.
    """
    if sys.platform != "win32":
        return None
    try:
        kernel32, _, _, _ExtendedLimit = _build_win32_structs()
        import ctypes

        h_job = kernel32.CreateJobObjectW(None, None)
        if not h_job:
            return None

        info = _ExtendedLimit()
        flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_limit_mb is not None:
            flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = memory_limit_mb * 1024 * 1024
        if process_limit is not None:
            flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = process_limit
        info.BasicLimitInformation.LimitFlags = flags

        ok = kernel32.SetInformationJobObject(
            h_job,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            _log.warning("SetInformationJobObject failed (err=%s)", kernel32.GetLastError())
            kernel32.CloseHandle(h_job)
            return None
        return h_job
    except Exception as e:  # noqa: BLE001 — best-effort, never crash the agent
        _log.warning("create_job_with_limits failed (degrading to no limits): %s", e)
        return None


def assign_to_job(job_handle: int, pid: int) -> bool:
    """Attach a process to a Job Object. Returns True on success, False otherwise."""
    if sys.platform != "win32" or not job_handle:
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_ALL_ACCESS = 0x1F0FFF
        h_proc = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not h_proc:
            return False
        try:
            ok = kernel32.AssignProcessToJobObject(job_handle, h_proc)
            return bool(ok)
        finally:
            kernel32.CloseHandle(h_proc)
    except Exception as e:  # noqa: BLE001
        _log.warning("assign_to_job failed: %s", e)
        return False


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill a process AND all its children (the whole process tree).

    On Windows: creates a Job Object, assigns the process, then terminates the
    job — this kills the entire tree reliably (subprocess.run's timeout only
    kills the direct child, leaving grandchildren running with open pipes).

    On POSIX: uses os.killpg on the process group (requires the process to have
    been started with start_new_session=True or in its own process group).

    This is the shared implementation — bash.py (self-hosted tool) and
    deep_loop.py (production shell backend) both use it.
    """
    pid = proc.pid
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # Create a one-shot job just for the kill (simpler than tracking a
            # persistent job handle across the process lifetime). The
            # KILL_ON_JOB_CLOSE + TerminateJobObject combo ensures the whole
            # tree dies even if some children escaped assignment.
            h_job = create_job_with_limits()
            if h_job:
                if assign_to_job(h_job, pid):
                    kernel32.TerminateJobObject(h_job, 1)
                kernel32.CloseHandle(h_job)
            # Fallback: if job creation failed, try a direct TerminateProcess
            # (kills only the direct child, but better than hanging).
            PROCESS_TERMINATE = 0x0001
            h_proc = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if h_proc:
                kernel32.TerminateProcess(h_proc, 1)
                kernel32.CloseHandle(h_proc)
        except Exception as e:  # noqa: BLE001
            _log.warning("kill_process_tree (Windows) failed: %s", e)
    else:
        try:
            import signal

            os.killpg(os.getpgid(pid), signal.SIGKILL)  # type: ignore[name-defined]
        except (ProcessLookupError, PermissionError):
            pass
        except Exception as e:  # noqa: BLE001
            _log.warning("kill_process_tree (POSIX) failed: %s", e)
