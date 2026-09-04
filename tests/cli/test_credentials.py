import os
import sys

import pytest

from coderio.cli.credentials import get_key, read_credentials, write_credentials


def test_write_and_read(tmp_path):
    creds_file = tmp_path / ".coderio" / "credentials"
    write_credentials({"bigmodel_coding_plan": "sk-abc123"}, path=creds_file)
    assert creds_file.is_file()
    data = read_credentials(path=creds_file)
    assert data == {"bigmodel_coding_plan": "sk-abc123"}


def test_get_key(tmp_path):
    creds_file = tmp_path / ".coderio" / "credentials"
    write_credentials({"stepfun_coding_plan": "sk-step"}, path=creds_file)
    assert get_key("stepfun_coding_plan", path=creds_file) == "sk-step"
    assert get_key("missing", path=creds_file) is None


def test_permissions_restricted(tmp_path):

    creds_file = tmp_path / ".coderio" / "credentials"
    write_credentials({"bigmodel_api": "sk-x"}, path=creds_file)
    assert creds_file.is_file()
    if sys.platform != "win32":
        mode = creds_file.stat().st_mode & 0o777
        assert mode == 0o600, "expected 0600, got " + oct(mode)


def test_read_missing_returns_empty(tmp_path):
    assert read_credentials(path=tmp_path / "nope") == {}


def test_file_is_toml_format(tmp_path):
    creds_file = tmp_path / ".coderio" / "credentials"
    write_credentials({"bigmodel_coding_plan": "sk-1"}, path=creds_file)
    text = creds_file.read_text(encoding="utf-8")
    assert "[bigmodel_coding_plan]" in text


# ------------------------------------------------- credentials hardening (P3-7)


def test_credentials_created_before_write_are_restricted(tmp_path, monkeypatch):
    """P3-7 + P0-4 (2026-09-04): keys may only ever hit disk inside an
    ALREADY-restricted file. Atomic write: the temp file is restricted while
    still empty, the payload is written there, then it is renamed over the
    target; the renamed file is restricted again afterwards (idempotent)."""

    from pathlib import Path

    from coderio.cli import credentials as creds

    p = tmp_path / "sub" / "credentials"
    monkeypatch.setattr(creds, "_DEFAULT", p)
    calls = []
    real_restrict = creds._restrict_permissions

    def spy_restrict(path):
        path = Path(path)
        calls.append((path, path.exists(), path.read_bytes() if path.exists() else b""))
        real_restrict(path)

    monkeypatch.setattr(creds, "_restrict_permissions", spy_restrict)
    monkeypatch.setattr(creds.os, "chmod", lambda *a, **k: None)  # POSIX branch no-op

    creds.write_credentials({"prov": "k"}, p)

    assert len(calls) >= 2, "restrict must run on the temp AND on the final file"
    first_path, first_exists, first_body = calls[0]
    assert first_exists and first_body == b"", "first restrict must run on the EMPTY temp file"
    assert first_path != p and first_path.parent == p.parent, "temp must live in the same dir (atomic rename)"
    assert b"prov" in p.read_bytes(), "keys land in the target file after the rename"
    assert not p.with_name("credentials.tmp").exists(), "temp file must be cleaned up after the rename"


def test_corrupt_credentials_backed_up_then_rebuilt(tmp_path):
    """P0-4 (2026-09-04): a corrupt credentials file is backed up BEFORE being
    treated as empty, so the next save can never silently destroy the old
    key bytes. The rebuild works and the backup survives it."""
    p = tmp_path / "credentials"
    write_credentials({"alpha": "key-a"}, path=p)
    p.write_bytes(b"[alpha\nkey = broken!!!")  # corrupt TOML

    assert read_credentials(p) == {}
    backup = p.with_name("credentials.corrupt")
    assert backup.exists(), "corrupt bytes must be backed up before the empty fallback"
    assert b"broken" in backup.read_bytes(), "the backup preserves the corrupt bytes verbatim"

    # Rebuild: the save works and the backup survives it.
    write_credentials({"beta": "key-b"}, path=p)
    assert get_key("beta", path=p) == "key-b"
    assert b"broken" in backup.read_bytes(), "rebuilding must not touch the corrupt backup"


def test_write_leaves_no_temp_file(tmp_path):
    """The atomic-write temp file (PID-suffixed, adversarial review C5) must
    be gone after a successful save."""
    p = tmp_path / "credentials"
    write_credentials({"a": "k"}, path=p)
    assert p.is_file()
    assert list(tmp_path.glob("credentials*.tmp")) == [], "no temp file may survive the rename"


def test_failed_rename_preserves_original(tmp_path, monkeypatch):
    """Mutation round (2026-09-04): 'no temp left' is vacuous on HEAD, which
    created no temp at all — it never proved the write is ATOMIC. Simulate a
    crash at the rename: the previous credentials file must survive intact
    and the temp must still be cleaned up."""
    p = tmp_path / "credentials"
    write_credentials({"alpha": "key-a"}, path=p)
    original = p.read_bytes()

    def crashing_replace(src, dst, *a, **kw):
        raise OSError("simulated crash during rename")

    monkeypatch.setattr(os, "replace", crashing_replace)
    with pytest.raises(OSError):
        write_credentials({"beta": "key-b"}, path=p)
    assert p.read_bytes() == original, "a failed rename must leave the previous file intact"
    assert list(tmp_path.glob("credentials*.tmp")) == [], "temp must be cleaned up even after failure"


def test_corrupt_non_utf8_backed_up_not_crash(tmp_path, caplog):
    """Seam review (2026-09-04): a NON-UTF-8 file raises UnicodeDecodeError
    from tomllib.load, which is not a TOMLDecodeError — the old except let it
    crash /setup, onboarding and get_key. It must take the same
    backup-then-empty path as any other corruption."""
    import logging

    p = tmp_path / "credentials"
    p.write_bytes(b"\xff\xfe\x00binary garbage")
    with caplog.at_level(logging.WARNING):
        assert read_credentials(p) == {}
    backup = p.with_name("credentials.corrupt")
    assert backup.exists() and backup.read_bytes() == b"\xff\xfe\x00binary garbage"
    assert any("corrupt" in r.message for r in caplog.records)


def test_second_provider_write_preserves_first(tmp_path):
    """The merge contract: adding a second provider must not erase the first
    (this is the path the pre-atomic-write crash window used to wipe)."""
    p = tmp_path / "credentials"
    write_credentials({"alpha": "key-a"}, path=p)
    write_credentials({"beta": "key-b"}, path=p)
    assert get_key("alpha", path=p) == "key-a"
    assert get_key("beta", path=p) == "key-b"


def test_credentials_windows_no_username_keeps_inheritance(tmp_path, monkeypatch, caplog):
    """P3-7: with USERNAME/USER both unset, /inheritance:r must NOT run —
    stripping inheritance without a grant left the file unreadable by anyone."""
    import logging as _logging

    from coderio.cli import credentials as creds

    p = tmp_path / "credentials"
    p.write_bytes(b"[x]\nkey = 'k'\n")

    ran = []
    monkeypatch.setattr(creds.sys, "platform", "win32")
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("USER", raising=False)

    class _FakeCompleted:
        returncode = 0

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return _FakeCompleted()

    monkeypatch.setattr(creds.subprocess, "run", fake_run)
    with caplog.at_level(_logging.WARNING):
        creds._restrict_permissions(p)

    assert ran == [], "no icacls call may run without a resolvable username"
    assert any("skipping ACL" in r.message for r in caplog.records)
