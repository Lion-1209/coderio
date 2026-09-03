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
    except tomllib.TOMLDecodeError as e:
        # Corrupt (e.g. half-written) credentials must not crash /setup — the
        # user can rebuild by re-entering the key (same tolerance as the trust
        # store). P2-2, 2026-09-03 audit.
        _log.warning(
            "credentials file %s is corrupt (%s) — treating as empty; re-run onboarding or /setup to rebuild it", p, e
        )
        return {}
    return {section: v.get("key", "") for section, v in data.items() if isinstance(v, dict)}


def write_credentials(mapping: dict[str, str], path: Path | str | None = None) -> Path:
    """Merge provider_id -> key entries into the credentials file.

    Reads any existing keys first and merges the new mapping on top, so adding a
    second provider via /setup doesn't erase the first provider's key. Keys for
    an existing provider_id are overwritten (re-entering a key updates it).

    Harden order (2026-09-02 audit P3-7): the file is created and permission-
    restricted BEFORE any key bytes hit the disk — restricting after the write
    leaves a window where the plaintext keys sit under the directory's
    inherited (world-readable) ACL.
    """
    p = Path(path) if path else _DEFAULT
    p.parent.mkdir(parents=True, exist_ok=True)
    # Restrict BEFORE writing, unconditionally (icacls/chmod are idempotent):
    # files created by older versions carry wide inherited ACLs, and the
    # write-then-restrict order left a plaintext-keys window on every rewrite.
    if not p.exists():
        p.touch()
    _restrict_permissions(p)
    existing = read_credentials(p)
    existing.update(mapping)
    data = {pid: {"key": key} for pid, key in existing.items()}
    with open(p, "wb") as f:
        tomli_w.dump(data, f)
    _restrict_permissions(p)
    return p


def get_key(provider_id: str, path: Path | str | None = None) -> str | None:
    return read_credentials(path).get(provider_id)
