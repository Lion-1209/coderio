from pathlib import Path

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


def test_build_runtime_with_model_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from coderio.cli.repl import build_runtime
    cfg, store, model, tools, gate, session, active, stream = build_runtime(
        search_from="no-creds", save_dir=tmp_path, creds_path=tmp_path / "no-creds",
        model_override="custom-model",
    )
    assert cfg.model.default == "custom-model"


def test_build_gate_returns_auto_for_auto_mode(tmp_path, monkeypatch):
    from coderio.config import Config, ToolsConfig
    from coderio.tools.permission import PermissionMode, AutoPermissionGate
    from coderio.cli.repl import build_gate
    cfg = Config(tools=ToolsConfig(permission_mode=PermissionMode.AUTO))
    gate = build_gate(cfg)
    assert isinstance(gate, AutoPermissionGate)
