from __future__ import annotations

import logging
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import tomli_w

_DEFAULT = Path.home() / ".coderio" / "credentials"

_log = logging.getLogger(__name__)


def _restrict_permissions(p: Path) -> None:
    """Restrict the credentials file to the current user only.

    POSIX: chmod 0600. Windows: icacls to remove inherited access and grant only
    the current user (Python's os.chmod is a no-op for mode bits on Windows).

    Hardened per the 2026-09-02 audit (P3-7):
    - the /inheritance:r step runs ONLY when the username resolved — without a
      grant after stripping inheritance the file has NO readable ACE at all
      (USERNAME and USER both unset left the credentials file unreadable by
      anyone, including its owner);
    - icacls failures are logged instead of being silently swallowed
      (check=False was deliberate — a failed restriction must not abort the
      key write — but the user has to learn that the file is still wide).
    """
    if sys.platform == "win32":
        user = os.environ.get("USERNAME") or os.environ.get("USER", "")
        if not user:
            _log.warning(
                "could not resolve the Windows username (USERNAME/USER unset) — "
                "skipping ACL restriction on %s; the file keeps inherited permissions. "
                "Restrict it manually if this directory is shared.",
                p,
            )
            return
        try:
            r1 = subprocess.run(["icacls", str(p), "/inheritance:r"], check=False, capture_output=True)
            r2 = subprocess.run(["icacls", str(p), "/grant:r", f"{user}:F"], check=False, capture_output=True)
            if r1.returncode != 0 or r2.returncode != 0:
                _log.warning(
                    "icacls could not fully restrict %s (rc=%s/%s) — the file may keep "
                    "inherited permissions. Check the ACL manually.",
                    p,
                    r1.returncode,
                    r2.returncode,
                )
        except FileNotFoundError:
            _log.warning("icacls not found — could not restrict permissions on %s", p)
        return
    os.chmod(p, 0o600)


def read_credentials(path: Path | str | None = None) -> dict[str, str]:
    """Read provider_id -> key mapping from the credentials file."""
    p = Path(path) if path else _DEFAULT
    if not p.is_file():
        return {}
    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        # Corrupt credentials (manual edit gone wrong, or a truncated write
        # from before the 2026-09-04 atomic-write fix) must not crash /setup.
        # UnicodeDecodeError must be caught explicitly (third-party seam
        # review, 2026-09-04): a non-UTF-8 file raises it from tomllib.load
        # and it is NOT a TOMLDecodeError subclass — without it, /setup,
        # onboarding and get_key all crash on a binary-corrupted file.
        # But treating the file as empty and continuing let the NEXT save
        # silently persist that emptiness — wiping every other stored key
        # (audit P0-4). Back the corrupt bytes up first so they remain
        # recoverable, then rebuild from empty.
        backup = p.with_name(p.name + ".corrupt")
        try:
            if not backup.exists():
                backup.write_bytes(p.read_bytes())
                _log.warning(
                    "credentials file %s is corrupt (%s) — backed up to %s and treating as "
                    "empty; re-run onboarding or /setup to rebuild it",
                    p,
                    e,
                    backup,
                )
            else:
                # Deliberate: the FIRST backup is preserved — overwriting it
                # with each new corruption would trade the original recoverable
                # bytes for whatever broke latest (third-party review note).
                _log.warning(
                    "credentials file %s is corrupt (%s) — treating as empty (an earlier backup already exists at %s)",
                    p,
                    e,
                    backup,
                )
        except OSError:
            _log.warning("credentials file %s is corrupt (%s) — treating as empty; backing it up failed", p, e)
        return {}
    return {section: v.get("key", "") for section, v in data.items() if isinstance(v, dict)}


def write_credentials(mapping: dict[str, str], path: Path | str | None = None) -> Path:
    """Merge provider_id -> key entries into the credentials file.

    Reads any existing keys first and merges the new mapping on top, so adding a
    second provider via /setup doesn't erase the first provider's key. Keys for
    an existing provider_id are overwritten (re-entering a key updates it).

    Atomic write (2026-09-04 audit P0-4): the payload goes to a temp file in
    the SAME directory, which is permission-restricted BEFORE any key bytes
    hit the disk (P3-7), then renamed over the target with os.replace. The old
    in-place truncate-and-rewrite had a crash window: a half-written file is
    read back as {} (read_credentials tolerates corrupt TOML), and the NEXT
    save would silently persist that emptiness — wiping every stored key.
    A crash now leaves the previous credentials file intact.
    """
    p = Path(path) if path else _DEFAULT
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = read_credentials(p)
    existing.update(mapping)
    data = {pid: {"key": key} for pid, key in existing.items()}
    # PID-suffixed temp (third-party adversarial review C5, 2026-09-04): a
    # fixed ".tmp" name made two concurrent writers stomp each other's temp
    # file (PermissionError + lost update). Per-process temps never collide.
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb"):
            pass  # create the temp empty so restriction covers a zero-byte file
        _restrict_permissions(tmp)
        with open(tmp, "wb") as f:
            tomli_w.dump(data, f)
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass  # best-effort cleanup; the temp is already restricted
    _restrict_permissions(p)
    return p


def get_key(provider_id: str, path: Path | str | None = None) -> str | None:
    return read_credentials(path).get(provider_id)
