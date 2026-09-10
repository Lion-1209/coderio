"""Corrupt credential backups must retain the secret file's protection."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from coderio.cli.credentials import read_credentials, write_credentials


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits; Windows uses ACLs")
def test_corrupt_backup_is_private_under_permissive_umask(tmp_path: Path) -> None:
    p = tmp_path / "credentials"
    old_umask = os.umask(0o022)
    try:
        write_credentials({"audit": "fake-audit-key"}, p)
        damaged = p.read_bytes() + b"\n[broken"
        p.write_bytes(damaged)
        assert read_credentials(p) == {}
        backup = p.with_name("credentials.corrupt")
        assert backup.read_bytes() == damaged
        assert backup.stat().st_mode & 0o777 == 0o600
        p.write_bytes(b"[different-broken")
        assert read_credentials(p) == {}
        assert backup.read_bytes() == damaged
    finally:
        os.umask(old_umask)
