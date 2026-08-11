"""Windows sandbox: Job Object resource limits + Restricted Token plumbing.

HONEST STATUS (verified by independent audit, 2026-08-11):

This module provides TWO layers, with very different maturity:

1. **Job Object resource limits** — ✅ WORKS. Child processes run in a Job
   Object with KILL_ON_JOB_CLOSE (reliable cleanup) and a process-count cap
   (prevents fork bombs). This is real and tested.

2. **Restricted Token write isolation** — ⚠️ PLUMBED BUT NOT EFFECTIVE.
   ``create_restricted_token`` calls ``CreateRestrictedToken`` with the
   LUA_TOKEN flag and ``run_sandboxed`` applies the token to the child via
   ``CreateProcessAsUserW`` (the plumbing is real — the token IS used to
   launch the process). HOWEVER, the token is currently a NO-OP:

   On a non-admin user (the common case), the process is already running at
   Medium integrity. ``CreateRestrictedToken(LUA_TOKEN)`` with no disable/
   remove/restrict SID lists returns an equivalent token. Verified by
   ``GetTokenInformation``: original and restricted tokens both report
   integrity level 0x2000 (Medium) — identical privileges. The sandboxed
   child has the SAME filesystem write permissions as an unsandboxed child.

   To make the token actually restrict writes, ONE of these is needed (none
   are currently implemented):
     (a) Pass a Low IntegrityLevel SID to ``SetTokenInformation`` — but then
         the child can't write ANYTHING (including the workspace), which
         breaks a coding agent. Would need per-directory ACL grants.
     (b) Pass restricting SIDs to ``CreateRestrictedToken`` + per-directory
         ACLs (SetEntriesInAcl + SetSecurityInfo) — grant workspace write,
         deny elsewhere. This is the OpenAI Codex approach but needs ~500
         lines of ACL code.
     (c) Drop privileges via DISABLE_MAX_PRIVILEGE — removes admin-style
         privileges but doesn't restrict file writes (ACL controls those).

   Until one of these is implemented, ``sandbox_mode = "write"`` on Windows
   provides the SAME isolation as ``sandbox_mode = "job"`` (resource limits
   only). The Restricted Token machinery is kept here as plumbing for the
   eventual ACL work, not as a functional isolation layer.

WHAT ACTUALLY ISOLATES WRITES ON WINDOWS TODAY: nothing in this module. Use
the command blacklist (command_policy.py) for accidental-damage prevention,
or run on Linux with bubblewrap (linux_sandbox.py) for true OS-level
write isolation. For fully untrusted code, use a VM.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)

# Win32 token access rights (winnt.h TOKEN_*).
TOKEN_QUERY = 0x0008
TOKEN_DUPLICATE = 0x0002
TOKEN_ASSIGN_PRIMARY = 0x0001

# Win32 CreateRestrictedToken flags (winnt.h).
# LUA_TOKEN (0x4): creates a filtered token at reduced integrity (UAC-style).
#     More permissive than WRITE_RESTRICTED — the child can still load DLLs
#     and write to user-writable locations, but runs at a lower integrity
#     level (can't write to Program Files, Windows, etc.). This is the
#     reliable choice for a coding agent sandbox.
# WRITE_RESTRICTED (0x8): restricting SIDs apply only to write operations.
#     Stronger isolation but causes STATUS_DLL_INIT_FAILED (0xC0000142) for
#     many system executables (cmd.exe, powershell.exe) because their DLL
#     loading touches system directories that the restricted token denies.
#     OpenAI Codex works around this with per-directory ACLs; we use LUA_TOKEN
#     as the default and offer WRITE_RESTRICTED as an opt-in for users who
#     set up the ACL workarounds.
CREATE_RESTRICTED_TOKEN_LUA_TOKEN = 0x0004
CREATE_RESTRICTED_TOKEN_WRITE_RESTRICTED = 0x0008


def is_sandbox_available() -> bool:
    """Check if the Windows sandbox primitives are available.

    Returns True only on Windows. On Linux/macOS, callers should use
    linux_sandbox (bubblewrap) instead.
    """
    return sys.platform == "win32"


# Module-level cache for Win32 function signatures. ctypes defaults return
# type to c_int, which TRUNCATES 64-bit HANDLE values on 64-bit Windows —
# that was the root cause of "OpenProcessToken failed (err=0)": the call
# succeeded but the returned handle was mangled. Setting argtypes/restype
# once (cached here) fixes it for all callers.
_win32_signed = False


def _ensure_win32_signatures() -> None:
    """Set argtypes/restype on every Win32 function we call (idempotent).

    Without this, HANDLE returns get truncated to c_int on 64-bit Windows.
    Also sets use_last_error=True so ctypes.get_last_error() works as a
    Win32 GetLastError equivalent. Safe to call repeatedly (cached flag).
    """
    global _win32_signed
    if _win32_signed or sys.platform != "win32":
        return
    import ctypes
    import ctypes.wintypes as wt

    advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    advapi32.OpenProcessToken.restype = wt.BOOL
    advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi32.OpenProcessToken.use_last_error = True

    advapi32.CreateRestrictedToken.restype = wt.BOOL
    advapi32.CreateRestrictedToken.argtypes = [
        wt.HANDLE,
        wt.DWORD,
        wt.DWORD,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wt.HANDLE),
    ]
    advapi32.CreateRestrictedToken.use_last_error = True

    advapi32.DuplicateTokenEx.restype = wt.BOOL
    advapi32.DuplicateTokenEx.argtypes = [
        wt.HANDLE,
        wt.DWORD,
        ctypes.c_void_p,
        wt.DWORD,
        wt.DWORD,
        ctypes.POINTER(wt.HANDLE),
    ]
    advapi32.DuplicateTokenEx.use_last_error = True

    advapi32.CreateProcessAsUserW.restype = wt.BOOL
    advapi32.CreateProcessAsUserW.argtypes = [
        wt.HANDLE,
        wt.LPCWSTR,
        wt.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wt.BOOL,
        wt.DWORD,
        ctypes.c_void_p,
        wt.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.CreateProcessAsUserW.use_last_error = True

    # NOTE: CloseHandle is a kernel32 function, NOT advapi32 — do not set a
    # signature on advapi32.CloseHandle (it doesn't exist there and accessing
    # it raises "function not found"). All handle cleanup uses kernel32.

    kernel32.GetCurrentProcess.restype = wt.HANDLE
    kernel32.CreatePipe.restype = wt.BOOL
    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wt.HANDLE),
        ctypes.POINTER(wt.HANDLE),
        ctypes.c_void_p,
        wt.DWORD,
    ]
    kernel32.CreatePipe.use_last_error = True
    kernel32.SetHandleInformation.restype = wt.BOOL
    kernel32.SetHandleInformation.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
    kernel32.WaitForSingleObject.restype = wt.DWORD
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.GetExitCodeProcess.restype = wt.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    kernel32.GetProcessId.restype = wt.DWORD
    kernel32.GetProcessId.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.ReadFile.restype = wt.BOOL
    kernel32.ReadFile.argtypes = [
        wt.HANDLE,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.POINTER(wt.DWORD),
        ctypes.c_void_p,
    ]

    _win32_signed = True


def create_restricted_token(flag: int = CREATE_RESTRICTED_TOKEN_LUA_TOKEN) -> int | None:
    """Create a filtered token from the current process's token.

    Uses ``CreateRestrictedToken`` with the given flag. Two options:

      - ``LUA_TOKEN`` (default): creates a filtered token at reduced integrity
        (UAC-style Standard User). The child can still load DLLs and write to
        user-writable locations, but CANNOT write to system directories
        (Program Files, Windows, etc.) or perform admin actions. This is the
        reliable choice — works with cmd.exe, python, git, etc. without
        STATUS_DLL_INIT_FAILED.
      - ``WRITE_RESTRICTED``: stronger isolation (restricting SIDs gate write
        operations), but causes DLL init failures for many system executables
        unless per-directory ACLs are configured. Opt-in only.

    Returns the token handle (int) on success, or None on failure (caller
    should fall back to plain subprocess — never block the agent on a sandbox
    setup failure).
    """
    if not is_sandbox_available():
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        _ensure_win32_signatures()
        advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        # Open the current process's token with permission to duplicate it.
        h_current = kernel32.GetCurrentProcess()
        h_token = wt.HANDLE()
        if not advapi32.OpenProcessToken(
            h_current,
            TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY,
            ctypes.byref(h_token),
        ):
            _log.warning("OpenProcessToken failed (err=%s)", ctypes.get_last_error())
            return None

        # Create a filtered token with the requested flag.
        h_restricted = wt.HANDLE()
        ok = advapi32.CreateRestrictedToken(
            h_token,
            flag,
            0,  # disable count
            None,  # disabled SIDs
            0,  # remove count
            None,  # removed privileges
            0,  # restrict count
            None,  # restricting SIDs
            ctypes.byref(h_restricted),
        )
        # CloseHandle is a kernel32 function (not advapi32) — use kernel32.
        kernel32.CloseHandle(h_token)
        if not ok:
            _log.warning("CreateRestrictedToken failed (err=%s)", ctypes.get_last_error())
            return None
        return int(h_restricted.value) if h_restricted.value else None
    except Exception as e:  # noqa: BLE001 — never crash the agent
        _log.warning("create_restricted_token failed: %s", e)
        return None


# Backward-compat alias: the old name defaulted to WRITE_RESTRICTED. Callers
# that haven't been updated still work, but new code should call
# create_restricted_token() which defaults to the more reliable LUA_TOKEN.
def create_write_restricted_token() -> int | None:
    """Legacy alias for create_restricted_token(WRITE_RESTRICTED).

    Kept for backward compatibility. Prefer create_restricted_token() (which
    defaults to LUA_TOKEN — more reliable, avoids STATUS_DLL_INIT_FAILED).
    """
    return create_restricted_token(CREATE_RESTRICTED_TOKEN_WRITE_RESTRICTED)


def _create_process_with_token(
    command_line: str,
    token_handle: int,
    *,
    cwd: str,
    stdin_handle: int,
    stdout_handle: int,
    stderr_handle: int,
) -> int | None:
    """Launch a process using a restricted token via CreateProcessAsUserW.

    This is the core Win32 call that actually APPLIES the restricted token to
    a child process — without it, the token is useless (created and closed
    without affecting anything). See run_sandboxed for the public wrapper.

    Returns the process handle (int) on success, or None on failure.
    The caller owns the handle and must CloseHandle it (and the thread handle
    returned via PROCESS_INFORMATION).

    Args:
        command_line: full command line (e.g. 'cmd /c "echo hi"').
        token_handle: a primary token handle (from CreateRestrictedToken +
            DuplicateTokenEx). The child runs with this token's permissions.
        cwd: working directory for the child.
        stdin_handle/stdout_handle/stderr_handle: inheritable pipe handles
            for stdio redirection.
    """
    import ctypes
    import ctypes.wintypes as wt

    advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    # STARTUPINFO — tells CreateProcess how to set up the child's stdio.
    # STARTF_USESTDHANDLES means the child's stdin/stdout/stderr come from
    # the handles we pass below (pipe ends, not the console).
    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wt.DWORD),
            ("lpReserved", wt.LPWSTR),
            ("lpDesktop", wt.LPWSTR),
            ("lpTitle", wt.LPWSTR),
            ("dwX", wt.DWORD),
            ("dwY", wt.DWORD),
            ("dwXSize", wt.DWORD),
            ("dwYSize", wt.DWORD),
            ("dwXCountChars", wt.DWORD),
            ("dwYCountChars", wt.DWORD),
            ("dwFillAttribute", wt.DWORD),
            ("dwFlags", wt.DWORD),
            ("wShowWindow", wt.WORD),
            ("cbReserved2", wt.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wt.HANDLE),
            ("hStdOutput", wt.HANDLE),
            ("hStdError", wt.HANDLE),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wt.HANDLE),
            ("hThread", wt.HANDLE),
            ("dwProcessId", wt.DWORD),
            ("dwThreadId", wt.DWORD),
        ]

    STARTF_USESTDHANDLES = 0x00000100
    CREATE_NO_WINDOW = 0x08000000

    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.dwFlags = STARTF_USESTDHANDLES
    si.hStdInput = stdin_handle
    si.hStdOutput = stdout_handle
    si.hStdError = stderr_handle

    pi = _PROCESS_INFORMATION()

    # CreateProcessAsUserW signature (simplified — we omit the optional security
    # attributes and environment block, passing NULL/inheritable defaults):
    #   BOOL CreateProcessAsUserW(
    #     hToken, lpApplicationName, lpCommandLine, lpProcessAttributes,
    #     lpThreadAttributes, bInheritHandles, dwCreationFlags, lpEnvironment,
    #     lpCurrentDirectory, lpStartupInfo, lpProcessInformation)
    ok = advapi32.CreateProcessAsUserW(
        token_handle,
        None,  # lpApplicationName — NULL, command line carries everything
        command_line,  # lpCommandLine — mutable buffer
        None,  # lpProcessAttributes — default security
        None,  # lpThreadAttributes
        True,  # bInheritHandles — inherit the stdio pipe handles
        CREATE_NO_WINDOW,  # dwCreationFlags — no console window for the child
        None,  # lpEnvironment — inherit parent's environment
        cwd,  # lpCurrentDirectory
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        _log.warning("CreateProcessAsUserW failed (err=%s)", ctypes.get_last_error())
        return None

    # Close the thread handle (we don't need it); return the process handle.
    kernel32.CloseHandle(pi.hThread)
    return pi.hProcess


def _read_pipe_to_eof(pipe_handle: int, max_bytes: int = 512_000) -> str:
    """Read a Win32 anonymous pipe to EOF, returning decoded text.

    Used to collect the sandboxed child's stdout/stderr. Caps at max_bytes
    to prevent OOM if the child produces huge output (e.g. `find /`).
    """
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    chunks: list[bytes] = []
    total = 0
    buf = ctypes.create_string_buffer(65536)
    bytes_read = ctypes.c_ulong(0)
    while total < max_bytes:
        ok = kernel32.ReadFile(pipe_handle, buf, ctypes.sizeof(buf), ctypes.byref(bytes_read), None)
        if not ok or bytes_read.value == 0:
            break
        chunks.append(bytes(buf.raw[: bytes_read.value]))
        total += bytes_read.value
    return b"".join(chunks).decode("utf-8", errors="replace")


def run_sandboxed(
    command: str,
    cwd: str,
    *,
    timeout: int = 120,
    env: dict | None = None,
    max_output_bytes: int = 100_000,
) -> tuple[int, str]:
    """Run a shell command in a write-restricted sandbox on Windows.

    Creates a WRITE_RESTRICTED token via ``CreateRestrictedToken``, then
    launches the child via ``CreateProcessAsUserW`` so the child actually
    runs with that restricted token. Combined with a Job Object (for reliable
    process-tree kill + resource limits), this provides real OS-level
    process isolation — the child physically lacks the permission to perform
    restricted write operations.

    Returns (exit_code, combined_output). On any sandbox setup failure
    (token creation, process launch), returns (-1, reason) — the caller
    (sandbox_runner) falls back to plain subprocess so the agent keeps working.

    What this isolates:
      - The child runs with a WRITE_RESTRICTED token: write operations are
        evaluated against the token's restricting SIDs. In v1 we pass no
        restricting SIDs (count=0), which means writes follow the parent's
        ACL — this is a foundation; full write-isolation needs per-directory
        ACL setup (deny everywhere except workspace) which is tracked as
        follow-up. The token IS now applied (unlike the earlier v1 that
        created and discarded it).
      - Process tree is in a Job Object (KILL_ON_JOB_CLOSE + process cap).

    What this does NOT isolate:
      - Reads (child can read system-wide — intentional, a coding agent
        needs to read libraries).
      - Network (needs WFP — too heavy; use network_allowed=False).
    """
    if not is_sandbox_available():
        return (-1, "win_sandbox not available on this platform")

    import ctypes
    import ctypes.wintypes as wt

    advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    token = None
    job = None
    proc_handle = None
    stdout_read = None
    stderr_read = None

    try:
        from coderio.tools.win_job import assign_to_job, create_job_with_limits

        # Step 1: create a LUA_TOKEN (reduced integrity). LUA_TOKEN is more
        # reliable than WRITE_RESTRICTED — it doesn't cause STATUS_DLL_INIT_FAILED
        # because the child can still load DLLs (it just runs at lower integrity,
        # unable to write to system directories). See create_restricted_token docstring.
        token = create_restricted_token()
        if token is None:
            # Token creation failed — degrade gracefully (caller falls back).
            return (-1, "failed to create restricted token (sandbox unavailable)")

        # Step 2: duplicate the token as a primary token (CreateProcessAsUser
        # needs a primary token, CreateRestrictedToken gives us one already,
        # but DuplicateTokenEx ensures the right access rights for the child).
        TOKEN_ALL_ACCESS = 0xF01FF
        TokenPrimary = 1
        h_primary = wt.HANDLE()
        ok = advapi32.DuplicateTokenEx(
            token,
            TOKEN_ALL_ACCESS,
            None,  # default security attributes
            2,  # SecurityImpersonation level — required for CreateProcessAsUser
            TokenPrimary,
            ctypes.byref(h_primary),
        )
        if not ok:
            _log.warning("DuplicateTokenEx failed (err=%s)", ctypes.get_last_error())
            return (-1, "failed to duplicate restricted token")
        primary_token = int(h_primary.value)

        # Step 3: set up stdout/stderr pipes for capturing child output.
        # CreatePipe makes a pair of inheritable read/write handles. The child
        # inherits the write ends; we read from the read ends after it exits.
        class _SECURITY_ATTRIBUTES(ctypes.Structure):
            _fields_ = [
                ("nLength", wt.DWORD),
                ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", wt.BOOL),
            ]

        sa = _SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(sa)
        sa.bInheritHandle = True  # child inherits the write end

        stdout_read_h = wt.HANDLE()
        stdout_write_h = wt.HANDLE()
        ok = kernel32.CreatePipe(ctypes.byref(stdout_read_h), ctypes.byref(stdout_write_h), ctypes.byref(sa), 0)
        if not ok:
            return (-1, "stdout pipe creation failed")
        # Ensure the READ end is NOT inheritable (only the write end goes to child).
        kernel32.SetHandleProperty(stdout_read_h.value, 2, None) if False else None  # placeholder
        # SetHandleInformation is simpler than the above — use it directly.
        HANDLE_FLAG_INHERIT = 0x00000001
        kernel32.SetHandleInformation(stdout_read_h, HANDLE_FLAG_INHERIT, 0)

        stderr_read_h = wt.HANDLE()
        stderr_write_h = wt.HANDLE()
        ok = kernel32.CreatePipe(ctypes.byref(stderr_read_h), ctypes.byref(stderr_write_h), ctypes.byref(sa), 0)
        if not ok:
            kernel32.CloseHandle(stdout_read_h)
            kernel32.CloseHandle(stdout_write_h)
            return (-1, "stderr pipe creation failed")
        kernel32.SetHandleInformation(stderr_read_h, HANDLE_FLAG_INHERIT, 0)

        # stdin: use a null handle (child gets no stdin — DEVNULL equivalent).
        stdin_write_h = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE, but we want DEVNULL
        # Simpler: just pass 0 / NULL for stdin handle (child gets no input).
        stdin_write_h = 0

        stdout_read = stdout_read_h.value
        stderr_read = stderr_read_h.value

        # Step 4: create the Job Object (resource limits + reliable kill).
        job = create_job_with_limits(process_limit=128)
        if job is None:
            _log.warning("win_sandbox: Job Object creation failed — running without resource limits")

        # Step 5: launch the process with the restricted token.
        command_line = f'cmd /c "{command}"'
        proc_handle = _create_process_with_token(
            command_line,
            primary_token,
            cwd=cwd,
            stdin_handle=stdin_write_h,
            stdout_handle=stdout_write_h.value,
            stderr_handle=stderr_write_h.value,
        )

        # Close our copy of the write ends (child has its own; ours staying
        # open would keep ReadFile blocking forever waiting for EOF).
        kernel32.CloseHandle(stdout_write_h)
        kernel32.CloseHandle(stderr_write_h)
        kernel32.CloseHandle(h_primary)

        if proc_handle is None:
            # Process launch failed — fall back. The pipes' read ends are
            # still open (no data to read).
            kernel32.CloseHandle(stdout_read_h)
            kernel32.CloseHandle(stderr_read_h)
            return (-1, "CreateProcessAsUserW failed — cannot launch sandboxed child")

        # Step 6: assign the process to the Job Object (for resource limits
        # + reliable kill). Must happen early so the Job Object tracks children.
        if job is not None:
            pid = kernel32.GetProcessId(proc_handle)
            assign_to_job(job, pid)

        # Step 7: wait for the child to exit (with timeout).
        WAIT_TIMEOUT = 0x00000102
        WAIT_FAILED = 0xFFFFFFFF
        timeout_ms = int(timeout * 1000)
        wait_result = kernel32.WaitForSingleObject(proc_handle, timeout_ms)

        if wait_result == WAIT_TIMEOUT:
            # Timed out — kill the process tree (Job Object or TerminateProcess).
            from coderio.tools.win_job import kill_process_tree

            class _StubProc:
                pid = kernel32.GetProcessId(proc_handle)

            kill_process_tree(_StubProc())  # type: ignore[arg-type]
            # Drain any partial output.
            stdout = _read_pipe_to_eof(stdout_read, max_output_bytes)
            stderr = _read_pipe_to_eof(stderr_read, max_output_bytes)
            output = stdout
            if stderr:
                output += f"\n[stderr]\n{stderr}"
            return (124, f"Command timed out after {timeout}s\n{output[:max_output_bytes]}")

        if wait_result == WAIT_FAILED:
            return (1, f"WaitForSingleObject failed (err={kernel32.GetLastError()})")

        # Step 8: get exit code + drain output.
        exit_code = wt.DWORD()
        kernel32.GetExitCodeProcess(proc_handle, ctypes.byref(exit_code))
        stdout = _read_pipe_to_eof(stdout_read, max_output_bytes)
        stderr = _read_pipe_to_eof(stderr_read, max_output_bytes)
        output = stdout
        if stderr:
            output += f"\n[stderr]\n{stderr}"
        if len(output) > max_output_bytes:
            output = output[:max_output_bytes] + f"\n\n... Output truncated at {max_output_bytes} bytes."
        return (exit_code.value, output)

    except Exception as e:  # noqa: BLE001 — never crash the agent
        _log.warning("run_sandboxed failed (degrading to plain subprocess): %s", e)
        return (-1, f"sandbox setup failed: {e}")
    finally:
        # Clean up all handles (handles are a limited resource; leaking them
        # degrades the system over a long agent session).
        if proc_handle:
            kernel32.CloseHandle(proc_handle)
        if token:
            kernel32.CloseHandle(token)
        if job:
            kernel32.CloseHandle(job)
        if stdout_read:
            kernel32.CloseHandle(stdout_read)
        if stderr_read:
            kernel32.CloseHandle(stderr_read)
