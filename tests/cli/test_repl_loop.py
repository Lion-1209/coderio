def test_build_runtime_assembles_pieces(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Windows: Path.home() reads USERPROFILE, not HOME — without this the
    # test reads the DEVELOPER'S real ~/.coderio/config.toml and becomes
    # machine-dependent (exposed when the retired "auto" mode was rejected).
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from coderio.cli.repl import build_runtime

    cfg, store, model, tools, gate, session, active, stream = build_runtime(
        search_from="no-creds",
        save_dir=tmp_path,
        creds_path=tmp_path / "no-creds",
    )
    assert len(tools) >= 9
    assert gate is not None
    assert session.id
    assert stream is not None


def test_build_runtime_with_model_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows Path.home() isolation
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from coderio.cli.repl import build_runtime

    cfg, store, model, tools, gate, session, active, stream = build_runtime(
        search_from="no-creds",
        save_dir=tmp_path,
        creds_path=tmp_path / "no-creds",
        model_override="custom-model",
    )
    assert cfg.model.default == "custom-model"


def test_build_gate_returns_auto_for_full_mode(tmp_path, monkeypatch):
    from coderio.cli.repl import build_gate
    from coderio.config import Config, ToolsConfig
    from coderio.tools.permission import AutoPermissionGate, PermissionMode

    cfg = Config(tools=ToolsConfig(permission_mode=PermissionMode.FULL))
    gate = build_gate(cfg)
    assert isinstance(gate, AutoPermissionGate)


def test_build_gate_rejects_legacy_auto(tmp_path, monkeypatch):
    """'auto' is retired (WhaleDock incident 2026-09-23): silently mapping it
    to FULL gave a zero-prompt tier an intuitive-sounding alias. build_gate
    must propagate the rejection instead of ever defaulting to FULL."""
    import pytest

    from coderio.cli.repl import build_gate
    from coderio.config import Config, ToolsConfig

    cfg = Config(tools=ToolsConfig(permission_mode="auto"))
    with pytest.raises(ValueError, match="auto_edit"):
        build_gate(cfg)
