"""Regression tests for Input.Submitted event isolation.

SECURITY BUG (observed in session 165807-ds7p): Input.Submitted bubbles up
the widget tree by default, so a submission in OnboardingScreen's
`#onboard-input` (API key, model id, base_url) would also land in
CoderioTUI.on_input_submitted and be dispatched to _on_input → run_agent →
session.append. Real-world consequence: a 64-char API key was persisted as a
user message in the session jsonl.

The fix: CoderioTUI.on_input_submitted only handles Input.Submitted from
`#msg` (the chat input), ignoring submissions from any modal's Input. This
test verifies that guard.
"""

import pytest
from textual.widgets import Input

from coderio.cli.tui import CoderioTUI


@pytest.mark.asyncio
async def test_main_input_submitted_dispatches_to_on_input():
    """Normal chat input submission IS dispatched to _on_input."""
    received = []

    def fake_on_input(line):
        received.append(line)

    app = CoderioTUI(on_input=fake_on_input)
    async with app.run_test() as pilot:
        await pilot.pause()
        main_input = app.query_one("#msg", Input)
        main_input.value = "hello world"
        await pilot.press("enter")
        await pilot.pause()
    assert received == ["hello world"], (
        f"main input submission must reach _on_input, got {received}"
    )


@pytest.mark.asyncio
async def test_non_main_input_submitted_does_not_dispatch():
    """REGRESSION: an Input.Submitted from a non-#msg Input (e.g. a modal's
    onboarding field) MUST NOT reach _on_input. Without the guard, sensitive
    fields like API keys leak into the session jsonl as user messages.
    """
    received = []

    def fake_on_input(line):
        received.append(line)

    app = CoderioTUI(on_input=fake_on_input)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Mount a foreign Input (simulates OnboardingScreen's #onboard-input)
        from textual.widgets import Input as _Input

        foreign = _Input(id="foreign-input")
        app.query_one("#history").mount(foreign)
        await pilot.pause()
        foreign.value = "sk-secret-api-key-DO-NOT-CAPTURE-1234567890"
        # Synthesize a Submitted event from the foreign input by pressing Enter
        # while it's focused. This mimics the real flow: user types API key in
        # the onboarding modal, presses Enter, the Submitted bubbles up.
        foreign.focus()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
    assert received == [], (
        "non-main Input.Submitted leaked to _on_input — this is the security "
        "bug. captured: " + str(received)
    )
