import sys

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
    """P3-7: the file must exist and be permission-restricted BEFORE key bytes
    hit the disk — write-then-restrict leaves the plaintext keys under the
    directory's inherited ACL for a window."""

    from coderio.cli import credentials as creds

    p = tmp_path / "sub" / "credentials"
    monkeypatch.setattr(creds, "_DEFAULT", p)
    # POSIX-only assertion: on win32 _restrict_permissions shells out to icacls.
    calls = []
    real_restrict = creds._restrict_permissions

    def spy_restrict(path):
        calls.append((path.exists(), p.read_bytes() if path.exists() else b""))
        real_restrict(path)

    monkeypatch.setattr(creds, "_restrict_permissions", spy_restrict)
    monkeypatch.setattr(creds.os, "chmod", lambda *a, **k: None)  # POSIX branch no-op

    creds.write_credentials({"prov": {"key": "k"}}, p)

    # First restriction call happened at creation time — before content.
    assert calls, "restrict must run at creation (write-then-restrict window closed)"
    assert calls[0][0] is True and calls[0][1] == b"", "restrict must run on the EMPTY file"
    assert b"prov" in p.read_bytes(), "keys land after the file is already restricted"


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
