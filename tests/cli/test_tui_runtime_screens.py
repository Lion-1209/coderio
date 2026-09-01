"""TuiRuntime slash-picker + session-lifecycle coverage (P2-4).

The existing test_tui_runtime.py covers dispatch routing; this file covers
the interactive-screen callbacks (__OPEN_PICKER__ / __OPEN_ONBOARDING__ /
__OPEN_PROFILE_PICKER__ / __OPEN_MODE_PICKER__), the explicit-resume and
/clear //model runtime-reset branches, bind(), load_session(), and
clear_context() — with REAL Session objects and REAL config dataclasses so
dataclasses.replace paths run for real.
"""

from __future__ import annotations

from dataclasses import replace as _dc_replace
from types import SimpleNamespace

from coderio.cli.commands import CommandResult
from coderio.cli.tui_runtime import TuiRuntime, _switch_active_profile
from coderio.cli.tui_screens import SessionPickerScreen
from coderio.config import load_config
from coderio.session.store import Session


class _Tui:
    """Duck-typed CoderioTUI stub: marshals synchronously, records everything."""

    def __init__(self):
        self.texts: list[tuple[str, str]] = []
        self.pushed: list[tuple[object, object | None]] = []
        self.exited = False
        self.history_cleared = False
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def _add_text(self, text, style=""):
        self.texts.append((str(text), style))

    def push_screen(self, screen, callback=None):
        self.pushed.append((screen, callback))

    def call_from_thread(self, fn, *a, **kw):
        return fn(*a, **kw)

    def exit(self):
        self.exited = True

    def _clear_history(self):
        self.history_cleared = True


def _make_real_runtime(tmp_path, monkeypatch, profiles=None) -> tuple[TuiRuntime, _Tui]:
    """Runtime wired to a REAL config (dataclass) + REAL on-disk session."""
    cfg = load_config(search_from=str(tmp_path))
    cfg = _dc_replace(
        cfg,
        session=_dc_replace(cfg.session, save_dir=str(tmp_path / "sessions")),
        profiles=profiles or [],
    )
    session = Session.create(cfg.session.save_dir, {"model": cfg.model.default})
    r = TuiRuntime(
        store=SimpleNamespace(names=lambda: []),
        active=SimpleNamespace(all=lambda: [], clear=lambda: None),
        tools=[],
        creds_path=tmp_path / "creds",
        custom_commands={},
    )
    tui = _Tui()
    r.tui = tui
    r.rt = {
        "cfg": cfg,
        "model": SimpleNamespace(model_name="fake"),
        "session": session,
        "gate": SimpleNamespace(mode="plan"),
    }
    return r, tui


def _patch_slash(monkeypatch, result: CommandResult) -> None:
    monkeypatch.setattr("coderio.cli.commands.handle_slash", lambda line, ctx: result)


# ------------------------------------------------------------- picker screens


def test_resume_no_arg_opens_session_picker(monkeypatch, tmp_path):
    r, tui = _make_real_runtime(tmp_path, monkeypatch)
    _patch_slash(monkeypatch, CommandResult(message="__OPEN_PICKER__"))
    original_id = r.rt["session"].id

    r.handle_input("/resume")

    assert len(tui.pushed) == 1
    screen, callback = tui.pushed[0]
    assert isinstance(screen, SessionPickerScreen)
    # Picking nothing (cancelled) must not touch the runtime holder.
    callback(None)
    assert r.rt["session"].id == original_id
    # Picking a listed session id swaps the holder to a LOADED session.
    sid = Session.list_recent(tmp_path / "sessions")[0]
    callback(sid)
    assert r.rt["session"].id == sid


def test_setup_opens_onboarding_and_rebuilds_model(monkeypatch, tmp_path):
    r, tui = _make_real_runtime(tmp_path, monkeypatch)
    _patch_slash(monkeypatch, CommandResult(message="__OPEN_ONBOARDING__"))
    monkeypatch.chdir(tmp_path)  # load_config(search_from=".") reads tmp cwd

    rebuilt = {}
    import coderio.llm as llm

    def _fake_build(cfg, creds_path=None):
        rebuilt["cfg"] = cfg
        return SimpleNamespace(model_name="rebuilt")

    monkeypatch.setattr(llm, "build_chat_model", _fake_build)

    r.handle_input("/setup")

    assert len(tui.pushed) == 1
    _, callback = tui.pushed[0]
    callback(None)  # cancelled → runtime untouched
    assert rebuilt == {}

    callback(SimpleNamespace())  # completed → cfg reloaded + model rebuilt
    assert rebuilt["cfg"].model.default == r.rt["cfg"].model.default
    assert r.rt["model"].model_name == "rebuilt"
    assert any("已重新配置" in t for t, _ in tui.texts)


def test_profile_picker_without_profiles_warns(monkeypatch, tmp_path):
    r, tui = _make_real_runtime(tmp_path, monkeypatch)  # profiles=[]
    _patch_slash(monkeypatch, CommandResult(message="__OPEN_PROFILE_PICKER__"))

    r.handle_input("/profile")

    assert tui.pushed == []
    assert any("还没有保存的 profile" in t for t, _ in tui.texts)


def test_profile_picker_switches_profile(monkeypatch, tmp_path):
    profiles = [SimpleNamespace(name="work"), SimpleNamespace(name="home")]
    r, tui = _make_real_runtime(tmp_path, monkeypatch, profiles=profiles)
    _patch_slash(monkeypatch, CommandResult(message="__OPEN_PROFILE_PICKER__"))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)

    import coderio.llm as llm

    monkeypatch.setattr(llm, "build_chat_model", lambda cfg, creds_path=None: SimpleNamespace(model_name="new"))

    r.handle_input("/profile")

    _, callback = tui.pushed[0]
    callback("home")  # pick a different profile

    assert r.rt["model"].model_name == "new"
    assert any("已切换到配置 → home" in t for t, _ in tui.texts)
    # active_profile was persisted to the fake HOME's config.toml
    assert "home" in (tmp_path / ".coderio" / "config.toml").read_text(encoding="utf-8")


def test_mode_picker_rebuilds_gate(monkeypatch, tmp_path):
    r, tui = _make_real_runtime(tmp_path, monkeypatch)
    _patch_slash(monkeypatch, CommandResult(message="__OPEN_MODE_PICKER__"))

    gates = []

    def _fake_build_gate(cfg, console=None, tui=None):
        g = SimpleNamespace(mode=cfg.tools.permission_mode)
        gates.append(g)
        return g

    monkeypatch.setattr("coderio.cli.repl.build_gate", _fake_build_gate)

    r.handle_input("/mode")

    _, callback = tui.pushed[0]
    callback("full")

    assert r.rt["gate"].mode == "full"
    assert r.rt["cfg"].tools.permission_mode == "full"
    assert any("已切换到 full 模式" in t for t, _ in tui.texts)


# --------------------------------------------------- plain results + resets


def test_message_and_exit_paths(monkeypatch, tmp_path):
    r, tui = _make_real_runtime(tmp_path, monkeypatch)

    _patch_slash(monkeypatch, CommandResult(message="hello message"))
    r.handle_input("/cost")
    assert any("hello message" in t for t, _ in tui.texts)
    assert not tui.exited

    _patch_slash(monkeypatch, CommandResult(continue_loop=False, message="bye"))
    r.handle_input("/exit")
    assert tui.exited, "continue_loop=False must exit the TUI"


def test_resume_explicit_id_loads_session(monkeypatch, tmp_path):
    r, tui = _make_real_runtime(tmp_path, monkeypatch)
    saved = Session.create(str(tmp_path / "sessions"), {"model": "m"})
    Session.create(str(tmp_path / "sessions"), {"model": "m"})

    _patch_slash(monkeypatch, CommandResult(new_session_id=saved.id))
    r.handle_input(f"/resume {saved.id}")

    assert r.rt["session"].id == saved.id
    assert r.rt["session"].path == saved.path
    assert any(saved.id in t for t, _ in tui.texts)


def test_reset_runtime_clear_branch(monkeypatch, tmp_path):
    r, tui = _make_real_runtime(tmp_path, monkeypatch)
    old_session = r.rt["session"]
    _patch_slash(monkeypatch, CommandResult(reset_runtime=True))

    r.handle_input("/clear")

    assert r.rt["session"] is not old_session, "/clear must start a FRESH session"
    assert r.rt["session"].id != old_session.id
    assert tui.history_cleared, "/clear must wipe the history pane"
    assert any("已开启新会话" in t for t, _ in tui.texts)


def test_reset_runtime_model_branch(monkeypatch, tmp_path):
    r, tui = _make_real_runtime(tmp_path, monkeypatch)
    _patch_slash(monkeypatch, CommandResult(reset_runtime=True, new_permission_mode="full"))
    monkeypatch.setattr(
        "coderio.cli.repl.build_gate",
        lambda cfg, console=None, tui=None: SimpleNamespace(mode=cfg.tools.permission_mode),
    )
    import coderio.llm as llm

    monkeypatch.setattr(llm, "build_chat_model", lambda cfg, creds_path=None: SimpleNamespace(model_name="m2"))

    r.handle_input("/model m2")

    assert r.rt["cfg"].model.default == "m2"
    assert r.rt["model"].model_name == "m2"
    assert r.rt["gate"].mode == "full"


# ------------------------------------------------------- session lifecycle


def test_load_session_renders_history(monkeypatch, tmp_path):
    r, tui = _make_real_runtime(tmp_path, monkeypatch)
    # Build a prior session WITH conversation content (incl. multimodal user).
    from coderio.session import Message

    prior = Session.create(str(tmp_path / "sessions"), {"model": "m"})
    prior.append(Message.user("plain question"))
    prior.append(Message.user([{"type": "text", "text": "look at this"}]))
    prior.append(Message.assistant("the answer"))

    r.load_session(prior.id)

    assert r.rt["session"].id == prior.id
    joined = "\n".join(t for t, _ in tui.texts)
    assert "plain question" in joined
    assert "look at this" in joined, "multimodal text blocks must render as text"
    assert "the answer" in joined
    assert f"已恢复会话 {prior.id}" in joined


def test_clear_context_starts_fresh(monkeypatch, tmp_path):
    r, tui = _make_real_runtime(tmp_path, monkeypatch)
    old = r.rt["session"]

    r.clear_context()

    assert r.rt["session"].id != old.id
    assert tui.history_cleared
    assert any("已开启新会话" in t for t, _ in tui.texts)


# ------------------------------------------------------------------- bind


def test_bind_rebuilds_gate_with_tui(monkeypatch, tmp_path):
    r = TuiRuntime(
        store=SimpleNamespace(names=lambda: []),
        active=SimpleNamespace(all=lambda: [], clear=lambda: None),
        tools=[],
        creds_path=None,
        custom_commands={},
    )
    tui = _Tui()
    cfg = load_config(search_from=str(tmp_path))
    gates = []
    monkeypatch.setattr(
        "coderio.cli.repl.build_gate",
        lambda cfg_, console=None, tui=None: (
            gates.append(SimpleNamespace(mode=cfg_.tools.permission_mode)) or gates[-1]
        ),
    )
    session = Session.create(str(tmp_path / "s"), {"model": "m"})

    r.bind(tui, cfg=cfg, model=SimpleNamespace(model_name="m"), gate=SimpleNamespace(mode="old"), session=session)

    assert r.tui is tui
    assert r.rt["session"] is session
    assert r.rt["gate"] is gates[-1], "bind must REBUILD the gate with the live TUI attached"
    assert len(gates) == 1


# ------------------------------------------------------- _switch_active_profile


def test_switch_active_profile_preserves_other_keys(monkeypatch, tmp_path):
    import tomli_w

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    cfg_dir = tmp_path / ".coderio"
    cfg_dir.mkdir(parents=True)
    existing = {"profiles": [{"name": "a"}], "custom_key": 42}
    with open(cfg_dir / "config.toml", "wb") as f:
        tomli_w.dump(existing, f)

    written = _switch_active_profile("b")

    import tomllib

    with open(cfg_dir / "config.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["active_profile"] == "b"
    assert data["custom_key"] == 42, "read-modify-write must preserve unrelated sections"
    assert data["profiles"] == [{"name": "a"}]
    assert written == "b"
