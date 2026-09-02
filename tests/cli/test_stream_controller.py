"""Unit tests for ChatStreamController (P2-3 extracted from CoderioTUI).

Covers the cross-thread confirmation wait's interruptibility (2026-09-02
audit finding 6) — previously a full 120s block left the agent thread
un-interruptible while a permission prompt was on screen.
"""

from __future__ import annotations

import threading
import time

from coderio.cli.stream_controller import ChatStreamController


class FakeUI:
    """Duck-typed CoderioTUI stub: marshals synchronously, records menus."""

    def __init__(self):
        self.events: list[tuple] = []

    def call_from_thread(self, fn, *a, **kw):
        return fn(*a, **kw)

    def _show_confirm_menu(self, tool_name, args_str, detail=None):
        self.events.append(("show", tool_name))

    def _hide_confirm_menu(self):
        self.events.append(("hide",))


def test_confirmation_wait_is_interruptible():
    """request_interrupt() must break the 120s confirmation wait within the
    0.5s slice (resolving as DENY) instead of blocking until answered."""
    ui = FakeUI()
    c = ChatStreamController(ui)
    result_box: dict = {}

    def agent_thread():
        result_box["result"] = c.request_confirmation("write_file", {"path": "x"})

    th = threading.Thread(target=agent_thread, daemon=True, name="agent")
    th.start()
    time.sleep(0.3)  # let the agent thread enter the wait
    assert ui.events == [("show", "write_file")]

    t0 = time.monotonic()
    c.request_interrupt()
    th.join(timeout=5)
    dt = time.monotonic() - t0

    assert not th.is_alive(), "agent thread still stuck in the confirmation wait"
    assert result_box["result"] is False, "interrupt must resolve the prompt as DENY"
    assert dt < 3, f"interrupt must break the wait within a slice, took {dt:.1f}s"
    assert ui.events[-1] == ("hide",), "the menu must be hidden on the interrupt path"


def test_confirmation_normal_resolution_still_works():
    """The sliced wait must not change the happy path: resolve_confirmation()
    wakes the agent thread and returns the user's answer."""
    ui = FakeUI()
    c = ChatStreamController(ui)
    result_box: dict = {}

    th = threading.Thread(
        target=lambda: result_box.update(result=c.request_confirmation("edit_file", {"path": "y"})),
        daemon=True,
    )
    th.start()
    time.sleep(0.3)
    c.resolve_confirmation(True)
    th.join(timeout=5)

    assert not th.is_alive()
    assert result_box["result"] is True
