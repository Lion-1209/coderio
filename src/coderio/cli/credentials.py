from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import tomli_w

_DEFAULT = Path.home() / ".coderio" / "credentials"


def _restrict_permissions(p: Path) -> None:
    """Restrict the credentials file to the current user only.

    POSIX: chmod 0600. Windows: icacls to remove inherited access and grant only
    the current user (Python's os.chmod is a no-op for mode bits on Windows).
    """
    if sys.platform == "win32":
        user = os.environ.get("USERNAME") or os.environ.get("USER", "")
        try:
            subprocess.run(
                ["icacls", str(p), "/inheritance:r"],
                check=False,
                capture_output=True,
            )
            if user:
                subprocess.run(
                    ["icacls", str(p), "/grant:r", f"{user}:F"],
                    check=False,
                    capture_output=True,
                )
                return
            return
        except FileNotFoundError:
            return
    os.chmod(p, 0o600)


def read_credentials(path: Path | str | None = None) -> dict[str, str]:
    """Read provider_id -> key mapping from the credentials file."""
    p = Path(path) if path else _DEFAULT
    if not p.is_file():
        return {}
    with open(p, "rb") as f:
        data = tomllib.load(f)
    return {
        section: v.get("key", "") for section, v in data.items() if isinstance(v, dict)
    }


def write_credentials(mapping: dict[str, str], path: Path | str | None = None) -> Path:
    """Merge provider_id -> key entries into the credentials file.

    Reads any existing keys first and merges the new mapping on top, so adding a
    second provider via /setup doesn't erase the first provider's key. Keys for
    an existing provider_id are overwritten (re-entering a key updates it).
    """
    p = Path(path) if path else _DEFAULT
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = read_credentials(p)
    existing.update(mapping)
    data = {pid: {"key": key} for pid, key in existing.items()}
    with open(p, "wb") as f:
        tomli_w.dump(data, f)
    _restrict_permissions(p)
    return p


def get_key(provider_id: str, path: Path | str | None = None) -> str | None:
    return read_credentials(path).get(provider_id)
