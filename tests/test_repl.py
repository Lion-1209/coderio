from coderio.cli.repl import _resolve_resume, build_gate, build_runtime
from coderio.config import Config
from coderio.session import Message
from coderio.session.store import Session
from coderio.tools.permission import (
    AutoPermissionGate,
    PermissionMode,
    RichPromptPermissionGate,
)


def test_build_runtime_assembles_pieces(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg, store, model, tools, gate, session, active, stream = build_runtime(
        search_from=str(tmp_path),
        save_dir=tmp_path / "no-creds",
        mode_override="confirm",
    )
    assert cfg.tools.permission_mode == "confirm"
    assert len(tools) >= 9
    assert gate.mode == "confirm"
    assert session.id


def test_default_mode_uses_concrete_gate_not_abstract(tmp_path, monkeypatch):
    """Spec §5.6 #5: confirm mode must not crash on destructive tools (FIX C1)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _, _, _, _, gate, _, _, _ = build_runtime(
        search_from=str(tmp_path),
        save_dir=tmp_path / "no-creds",
        mode_override="confirm",
    )
    assert isinstance(gate, RichPromptPermissionGate)

    import sys
    from io import StringIO

    old, sys.stdin = sys.stdin, StringIO("\n")
    try:
        result = gate.check("bash", {"command": "ls"})
    finally:
        sys.stdin = old
    # No answer -> not allowed (default N), but no crash.
    assert result is False


def test_build_gate_auto_mode():
    cfg = Config()
    object.__setattr__(cfg.tools, "permission_mode", PermissionMode.AUTO)
    gate = build_gate(cfg)
    assert isinstance(gate, AutoPermissionGate)


def test_build_gate_plan_mode():
    cfg = Config()
    object.__setattr__(cfg.tools, "permission_mode", PermissionMode.PLAN)
    gate = build_gate(cfg)
    assert gate.mode == "plan"


def test_resolve_resume_loads_existing(tmp_path):
    save = tmp_path / "sessions"
    s = Session.create(save, {"model": "m"})
    s.append(Message.user("hello"))
    cfg = Config()
    object.__setattr__(cfg, "session", cfg.session)  # ensure mutable path
    object.__setattr__(cfg.session, "save_dir", str(save))
    resumed = _resolve_resume(cfg, s.id, False)
    assert resumed.id == s.id
    assert resumed.messages[0].content == "hello"
