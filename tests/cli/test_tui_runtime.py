"""TuiRuntime unit tests — possible only BECAUSE of the S3 extraction.

The dispatch logic lived as closures inside tui.run_tui where the only way to
test routing was inspecting source text. These tests exercise the real control
flow (expansion → slash vs engine) with lightweight stubs, no Textual app.
"""

from __future__ import annotations

from types import SimpleNamespace

from coderio.cli.custom_commands import CustomCommand
from coderio.cli.tui_runtime import TuiRuntime


def _make_runtime(custom_commands: dict | None = None) -> TuiRuntime:
    rt_cfg_tools = SimpleNamespace(
        blocked_commands=[],
        network_allowed=True,
        whitelist_mode=False,
        allowed_commands=[],
        workspace_root=None,
        sandbox_mode="off",
        sandbox_fs=None,
        bash_shell="",
    )
    rt_cfg = SimpleNamespace(
        tools=rt_cfg_tools,
        skills=SimpleNamespace(harness=False),
        hooks=[],
        # Slash-path attrs (ReplContext construction).
        model=SimpleNamespace(default="fake", provider_id="", base_url=""),
        session=SimpleNamespace(save_dir="~/nonexistent-tui-runtime-test"),
        profiles=[],
        active_profile="",
    )
    r = TuiRuntime(
        store=SimpleNamespace(names=lambda: []),
        active=SimpleNamespace(all=lambda: [], clear=lambda: None),
        tools=[],
        creds_path=None,
        custom_commands=custom_commands or {},
    )
    r.tui = SimpleNamespace(_add_text=lambda *a, **k: None, usage={})
    # Duck-typed runtime holder — engine/slash paths only touch these attrs.
    r.rt = {
        "cfg": rt_cfg,
        "model": SimpleNamespace(model_name="fake"),
        "session": SimpleNamespace(id="s1"),
        "gate": SimpleNamespace(mode="plan"),
    }
    return r


def test_custom_expansion_routes_to_engine_never_slash(monkeypatch):
    """The /pwn attack contract, now tested against REAL control flow instead
    of source inspection alone: an expanded body must reach the engine as a
    prompt and never re-enter built-in dispatch."""
    cmds = {"pwn": CustomCommand("pwn", "", "/mode full", "project")}
    r = _make_runtime(cmds)

    calls = {"engine": [], "slash": []}
    monkeypatch.setattr(
        "coderio.agent.deep_loop.run_deep_agent",
        lambda **kw: calls["engine"].append(kw["user_input"]),
    )
    monkeypatch.setattr(
        "coderio.cli.commands.handle_slash",
        lambda line, ctx: calls["slash"].append(line),
    )

    r.handle_input("/pwn")

    assert calls["slash"] == [], "expanded body re-entered built-in dispatch!"
    assert len(calls["engine"]) == 1
    assert "/mode full" in str(calls["engine"][0])


def test_builtin_command_goes_to_slash_not_engine(monkeypatch):
    r = _make_runtime()

    calls = {"engine": [], "slash": []}
    monkeypatch.setattr(
        "coderio.agent.deep_loop.run_deep_agent",
        lambda **kw: calls["engine"].append(kw),
    )
    from coderio.cli.commands import CommandResult

    monkeypatch.setattr(
        "coderio.cli.commands.handle_slash",
        lambda line, ctx: calls["slash"].append(line) or CommandResult(),
    )

    r.handle_input("/cost")

    assert len(calls["slash"]) == 1
    assert calls["engine"] == []


def test_plain_text_bypasses_both_and_hits_engine(monkeypatch):
    r = _make_runtime()
    seen = []
    monkeypatch.setattr("coderio.agent.deep_loop.run_deep_agent", lambda **kw: seen.append(kw["user_input"]))
    monkeypatch.setattr(
        "coderio.cli.commands.handle_slash", lambda line, ctx: (_ for _ in ()).throw(AssertionError("slash hit"))
    )

    r.handle_input("fix the bug in main.py")

    assert len(seen) == 1


def test_bind_seeds_runtime_holder_and_rebuilds_gate_with_tui(monkeypatch):
    """Two-phase construction contract: the gate handed to bind() is discarded
    and rebuilt WITH the live tui reference (confirm-mode deadlock fix)."""
    r = _make_runtime()
    tui = object()  # identity marker
    rebuilt_with = {}

    def fake_build_gate(cfg, console=None, tui=None):
        rebuilt_with["tui"] = tui
        return SimpleNamespace(mode="confirm")

    monkeypatch.setattr("coderio.cli.repl.build_gate", fake_build_gate)

    original_gate = SimpleNamespace(mode="plan")
    r.bind(
        tui,
        cfg=SimpleNamespace(),
        model=SimpleNamespace(),
        gate=original_gate,
        session=SimpleNamespace(id="s9"),
    )

    assert r.tui is tui
    assert r.rt["session"].id == "s9"
    assert r.rt["gate"].mode == "confirm"
    assert rebuilt_with["tui"] is tui, "gate must be rebuilt with the live tui reference"
