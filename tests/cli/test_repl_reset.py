"""Regression tests for the S1 review fixes: /mode and --model/--provider overrides."""
import io
import sys
from pathlib import Path

import pytest


def _patch_console_input(monkeypatch, answers):
    """Make rich Console.input return scripted answers, then EOF."""
    it = iter(answers)

    def fake_input(self, prompt, **kw):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("rich.console.Console.input", fake_input)


def test_mode_override_applied(tmp_path, monkeypatch):
    """build_runtime(mode_override=...) must change the gate mode."""
    from coderio.cli.credentials import write_credentials
    from coderio.cli.repl import build_runtime
    creds = tmp_path / "credentials"
    write_credentials({"bigmodel_coding_plan": "sk-x"}, path=creds)
    monkeypatch.setenv("HOME", str(tmp_path))
    _, _, _, _, gate, _, _, _ = build_runtime(
        search_from=".", save_dir=tmp_path, creds_path=creds, mode_override="plan",
    )
    assert gate.mode == "plan"


def test_model_override_applied(tmp_path, monkeypatch):
    """build_runtime(model_override=...) must set the model name."""
    from coderio.cli.credentials import write_credentials
    from coderio.cli.repl import build_runtime
    creds = tmp_path / "credentials"
    write_credentials({"bigmodel_coding_plan": "sk-x"}, path=creds)
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg, _, model, _, _, _, _, _ = build_runtime(
        search_from=".", save_dir=tmp_path, creds_path=creds, model_override="glm-5.1",
    )
    assert cfg.model.default == "glm-5.1"
    assert model is not None


def test_provider_override_applied(tmp_path, monkeypatch):
    from coderio.cli.credentials import write_credentials
    from coderio.cli.repl import build_runtime
    creds = tmp_path / "credentials"
    write_credentials({"stepfun_coding_plan": "sk-x"}, path=creds)
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg, _, _, _, _, _, _, _ = build_runtime(
        search_from=".", save_dir=tmp_path, creds_path=creds, provider_override="stepfun_coding_plan",
    )
    assert cfg.model.provider_id == "stepfun_coding_plan"
