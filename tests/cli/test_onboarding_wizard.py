"""Regression tests for the live-testing bug batch (2026-09-16).

#25 — /setup wizard crashed 0.8s after save: the set_timer callback was a
lambda RETURNING dismiss()'s AwaitComplete; Textual's callback invoker awaits
awaitable returns, and awaiting dismiss from the screen's own pump raises
ScreenError.
"""

from __future__ import annotations

from inspect import isawaitable
from types import SimpleNamespace

import pytest

from coderio.cli.tui_onboarding import OnboardingScreen


@pytest.mark.asyncio
async def test_setup_finish_close_callback_returns_no_awaitable(monkeypatch):
    """_finish() schedules dismiss via set_timer — the callback must return
    None. A returned AwaitComplete gets awaited by Textual's callback invoker
    and raises ScreenError from the screen's own pump (#25)."""

    from coderio.cli.tui import CoderioTUI

    # Stub the persisters: the real ones write the user's home-dir files.
    monkeypatch.setattr("coderio.cli.credentials.write_credentials", lambda *a, **k: None)
    import coderio.cli.onboarding as onboarding_mod

    monkeypatch.setattr(onboarding_mod, "_save_profile_to_config", lambda *a, **k: None)
    monkeypatch.setattr(onboarding_mod, "OnboardingResult", lambda **k: SimpleNamespace(**k))

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = OnboardingScreen()
        screen._chosen_provider = SimpleNamespace(id="test-provider", label="Test Provider", kind="anthropic")
        screen._api_key = "test-key"
        screen._chosen_model = "test-model"
        screen._base_url = ""
        screen._context_limit = 0
        screen._profile_name = "test-profile"

        app.push_screen(screen)
        await pilot.pause()

        captured: dict = {}

        def _capture_set_timer(delay, callback):
            captured["delay"] = delay
            captured["callback"] = callback

        screen.set_timer = _capture_set_timer  # type: ignore[method-assign]
        screen._finish()

        assert captured["delay"] == 0.8
        callback = captured["callback"]
        result = callback()
        assert not isawaitable(result), (
            "close callback returned an awaitable — Textual's invoker will await "
            "it inside the screen's pump and raise ScreenError (#25)"
        )
        await pilot.pause()


@pytest.mark.asyncio
async def test_setup_finish_real_timer_dismisses_without_screenerror(monkeypatch):
    """End-to-end variant of #25 with the REAL 0.8s timer firing (the unit
    test above captures the callback; this one lets Textual schedule and run
    it exactly as production does). On the pre-fix code the timer task raised
    ScreenError and killed the app; now the screen must dismiss cleanly."""
    from coderio.cli.tui import CoderioTUI

    monkeypatch.setattr("coderio.cli.credentials.write_credentials", lambda *a, **k: None)
    import coderio.cli.onboarding as onboarding_mod

    monkeypatch.setattr(onboarding_mod, "_save_profile_to_config", lambda *a, **k: None)
    monkeypatch.setattr(onboarding_mod, "OnboardingResult", lambda **k: SimpleNamespace(**k))

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = OnboardingScreen()
        screen._chosen_provider = SimpleNamespace(id="test-provider", label="Test Provider", kind="anthropic")
        screen._api_key = "test-key"
        screen._chosen_model = "test-model"
        screen._base_url = ""
        screen._context_limit = 0
        screen._profile_name = "test-profile"

        app.push_screen(screen)
        await pilot.pause()

        screen._finish()  # schedules the REAL 0.8s timer
        await pilot.pause(1.5)  # let production fire it

        assert screen not in app.screen_stack, "onboarding screen should have dismissed itself cleanly"
