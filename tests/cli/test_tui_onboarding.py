"""Headless Textual tests for OnboardingScreen (the TUI configuration wizard).

OnboardingScreen is a ModalScreen — it must be tested inside an App context.
We use _OnboardingApp (the thin wrapper from tui_onboarding.py) with Textual's
run_test() harness, which renders to an off-screen buffer without a real terminal.

OnboardingScreen is pushed via push_screen, so it becomes app.screen (the
active screen), not a widget in the main tree — use app.screen to access it.

These tests exercise the step-transition state machine: provider → model →
key → name, the ollama shortcut (skip key), the openai_custom base_url step,
verification result handling, and the cancel path.

Network/file I/O is monkeypatched so no real API calls or disk writes happen.
"""

from __future__ import annotations

import pytest

from coderio.cli.tui_onboarding import OnboardingScreen, _OnboardingApp


def _patch_first_run(monkeypatch):
    """Patch the screen to look like a first run (no existing profiles)."""
    monkeypatch.setattr(OnboardingScreen, "_load_existing_profiles", staticmethod(lambda: []))
    monkeypatch.setattr("coderio.cli.credentials.read_credentials", lambda *a, **kw: {})


# --------------------------------------------------------------- compose / mount


@pytest.mark.asyncio
async def test_on_mount_shows_provider_step(monkeypatch):
    """First run (no existing profiles) → on_mount goes to provider step."""
    _patch_first_run(monkeypatch)
    app = _OnboardingApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        assert screen._step == "provider"
        from textual.widgets import ListView

        lv = screen.query_one("#onboard-list", ListView)
        assert lv.display is True
        assert len(lv) > 0


@pytest.mark.asyncio
async def test_compose_creates_all_widgets(monkeypatch):
    """The widget tree must have all 5 expected elements."""
    _patch_first_run(monkeypatch)
    app = _OnboardingApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        from textual.widgets import Input, ListView, Static

        screen.query_one("#onboard-title", Static)
        screen.query_one("#onboard-hint", Static)
        screen.query_one("#onboard-list", ListView)
        screen.query_one("#onboard-input", Input)
        screen.query_one("#onboard-status", Static)


# --------------------------------------------------------------- provider step


@pytest.mark.asyncio
async def test_provider_list_includes_known_providers(monkeypatch):
    """The provider list must include bigmodel_coding_plan and stepfun_coding_plan."""
    _patch_first_run(monkeypatch)
    app = _OnboardingApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        provider_ids = [p.id for p in screen._provider_items]
        assert "bigmodel_coding_plan" in provider_ids
        assert "stepfun_coding_plan" in provider_ids
        assert "ollama" in provider_ids


# --------------------------------------------------------------- ollama shortcut


@pytest.mark.asyncio
async def test_ollama_skips_key_step(monkeypatch):
    """Selecting ollama should skip the key step (sets _api_key='ollama',
    jumps straight to name)."""
    _patch_first_run(monkeypatch)
    app = _OnboardingApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        ollama = next(p for p in screen._provider_items if p.id == "ollama")
        screen._chosen_provider = ollama
        screen._base_url = ollama.base_url
        # _show_key_step checks for ollama and short-circuits to name.
        screen._show_key_step()
        assert screen._api_key == "ollama"
        assert screen._step == "name"


# --------------------------------------------------------------- openai_custom base_url


@pytest.mark.asyncio
async def test_openai_custom_shows_base_url_step(monkeypatch):
    """Selecting openai_custom should show the base_url input step."""
    _patch_first_run(monkeypatch)
    app = _OnboardingApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        custom = next(p for p in screen._provider_items if p.id == "openai_custom")
        screen._chosen_provider = custom
        screen._show_base_url_step()
        assert screen._step == "base_url"


# --------------------------------------------------------------- _on_verify_result


@pytest.mark.asyncio
async def test_verify_result_success_advances_to_name(monkeypatch):
    """A successful verification stores context_limit and advances to name."""
    _patch_first_run(monkeypatch)
    app = _OnboardingApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        bigmodel = next(p for p in screen._provider_items if p.id == "bigmodel_coding_plan")
        screen._chosen_provider = bigmodel
        screen._api_key = "test-key"
        screen._chosen_model = bigmodel.default_model
        screen._base_url = bigmodel.base_url
        screen._on_verify_result(ok=True, msg="验证成功", context_limit=256_000)
        assert screen._context_limit == 256_000
        assert screen._step == "name"


@pytest.mark.asyncio
async def test_verify_result_failure_returns_to_key(monkeypatch):
    """A failed verification shows an error and returns to the key step."""
    _patch_first_run(monkeypatch)
    app = _OnboardingApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        bigmodel = next(p for p in screen._provider_items if p.id == "bigmodel_coding_plan")
        screen._chosen_provider = bigmodel
        screen._step = "verifying"
        screen._on_verify_result(ok=False, msg="Auth failed", context_limit=0)
        assert screen._step == "key"


# --------------------------------------------------------------- cancel


@pytest.mark.asyncio
async def test_escape_cancels(monkeypatch):
    """Pressing Escape dismisses the screen with None (cancel)."""
    _patch_first_run(monkeypatch)
    app = _OnboardingApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        screen.action_cancel()
        await pilot.pause(0.3)
        assert app._result is None


# --------------------------------------------------------------- _finish


@pytest.mark.asyncio
async def test_finish_writes_credentials_and_dismisses(monkeypatch, tmp_path):
    """_finish should call write_credentials + _save_profile_to_config and
    dismiss with a result dict."""
    _patch_first_run(monkeypatch)
    written_creds = {}
    saved_profiles = []

    def _fake_write_creds(mapping, path=None):
        written_creds.update(mapping)

    def _fake_save_profile(result, name, config_path):
        saved_profiles.append((result.provider_id, name, result.context_limit))

    monkeypatch.setattr("coderio.cli.credentials.write_credentials", _fake_write_creds)
    monkeypatch.setattr("coderio.cli.onboarding._save_profile_to_config", _fake_save_profile)

    app = _OnboardingApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        bigmodel = next(p for p in screen._provider_items if p.id == "bigmodel_coding_plan")
        screen._chosen_provider = bigmodel
        screen._chosen_model = "GLM-5.2"
        screen._base_url = bigmodel.base_url
        screen._api_key = "test-key-xyz"
        screen._profile_name = "test-profile"
        screen._context_limit = 128_000

        screen._finish()
        await pilot.pause(0.2)

    assert written_creds.get("bigmodel_coding_plan") == "test-key-xyz"
    assert len(saved_profiles) == 1
    assert saved_profiles[0][0] == "bigmodel_coding_plan"
    assert saved_profiles[0][1] == "test-profile"
    assert saved_profiles[0][2] == 128_000


# --------------------------------------------------------------- action step (edit existing)


@pytest.mark.asyncio
async def test_action_step_shown_when_profiles_exist(monkeypatch):
    """When profiles already exist (the /setup reconfiguration path),
    on_mount shows the action step (new vs edit) instead of the provider step."""
    from coderio.config.models import Profile

    fake_profile = Profile(
        name="existing",
        provider_id="bigmodel_coding_plan",
        model="GLM-5.2",
        base_url="https://open.bigmodel.cn/api/anthropic",
        kind="anthropic",
    )
    monkeypatch.setattr(OnboardingScreen, "_load_existing_profiles", staticmethod(lambda: [fake_profile]))
    monkeypatch.setattr("coderio.cli.credentials.read_credentials", lambda *a, **kw: {})
    app = _OnboardingApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.5)
        screen = app.screen
        assert screen._step == "action"
        assert len(screen._action_items) == 2  # None (new) + 1 profile
