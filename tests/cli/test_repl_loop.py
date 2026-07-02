from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_build_runtime_assembles_pieces(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from coderio.cli.repl import build_runtime
    cfg, store, model, tools, gate, session, active, stream = build_runtime(
        search_from="no-creds", save_dir=tmp_path, creds_path=tmp_path / "no-creds",
    )
    assert len(tools) >= 9
    assert gate is not None
    assert session.id
    assert stream is not None


def test_slash_exit_handled(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from coderio.cli.credentials import write_credentials
    creds = tmp_path / "credentials"
    write_credentials({"bigmodel_coding_plan": "sk-test"}, path=creds)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from coderio.cli.repl import run_repl
    import coderio.cli.repl as repl_mod

    def fake_input(self, prompt, **kw):
        raise EOFError

    monkeypatch.setattr("rich.console.Console.input", fake_input)
    run_repl(search_from=".", save_dir=tmp_path, creds_path=creds)
