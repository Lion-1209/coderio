import os
import sys
from pathlib import Path

from coderio.cli.credentials import read_credentials, write_credentials, get_key


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
    import sys

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
