import re
import time

from rich.console import Console

from coderio.cli.stream import RichStream


def _console():
    return Console(record=True, width=60, force_terminal=False)


def test_tokens_accumulated_then_finish_renders():
    console = _console()
    s = RichStream(console)
    s.on_token("hello ")
    s.on_token("world")
    assert s.buffer == "hello world"
    s.on_finish()
    out = console.export_text()
    assert "hello world" in out


def test_tool_start_end_recorded():
    console = _console()
    s = RichStream(console)
    s.on_tool_start("bash", {"command": "ls"})
    s.on_tool_end("bash", "file1\nfile2")
    out = console.export_text()
    assert "bash" in out
    assert "file1" in out


def test_usage_tracking():
    console = _console()
    s = RichStream(console)
    s.add_usage({"input_tokens": 100, "output_tokens": 50})
    s.add_usage({"input_tokens": 20, "output_tokens": 10})
    assert s.usage["input_tokens"] == 120
    assert s.usage["output_tokens"] == 60


def test_step_start_arms_busy_indicator():
    """on_step_start must arm the busy indicator (the model-wait spinner) so the
    UI is visibly active even before the first token/thinking block arrives."""
    console = _console()
    s = RichStream(console)
    assert s._busy_live is None
    s.on_step_start()
    assert s._busy_live is not None, "busy indicator should be running after on_step_start"
    assert s._busy_start > 0
    s.on_finish()


def test_first_token_stops_busy_indicator():
    """The first visible token must tear down the busy indicator and switch to
    streaming text (otherwise the spinner lingers behind the token stream)."""
    console = _console()
    s = RichStream(console)
    s.on_step_start()
    assert s._busy_live is not None
    s.on_token("hello")
    assert s._busy_live is None, "busy indicator must stop once tokens arrive"
    assert s.buffer == "hello"
    s.on_finish()


def test_thinking_appends_to_busy_preview():
    """on_thinking accumulates into the busy indicator's preview without starting
    a second indicator (single always-on indicator, not one-per-stage)."""
    console = _console()
    s = RichStream(console)
    s.on_step_start()
    first_live = s._busy_live
    s.on_thinking("planning the work")
    assert s._busy_live is first_live, "thinking must reuse the existing indicator"
    assert "planning the work" in s._busy_buf
    s.on_finish()


def test_busy_elapsed_timer_advances():
    """The elapsed timer must reflect real time, so the user sees motion (this is
    the fix for 'UI looks frozen during long silent waits')."""
    import time as _time
    console = _console()
    s = RichStream(console)
    s.on_step_start()
    start_elapsed = _time.monotonic() - s._busy_start
    _time.sleep(0.05)
    later_elapsed = _time.monotonic() - s._busy_start
    assert later_elapsed > start_elapsed, "elapsed timer must advance"
    s.on_finish()


def test_busy_timer_text_advances_without_on_thinking():
    """Regression for the 'spinner dots animate but the seconds freeze' bug.

    The root cause was passing a STATIC renderable to Live: Rich's auto-refresh
    redraws the same stored Spinner, so its `text=` (a frozen Text with the
    seconds baked in) never changed — only the dots moved. The fix uses Live's
    get_renderable callback so each refresh rebuilds the Spinner with a fresh
    time.monotonic() read. This must hold even when NO on_thinking fires (the
    silent-wait case the user reported)."""
    import re
    import time as _time
    console = _console()
    s = RichStream(console)
    s.on_step_start()
    t1 = s._busy_renderable().text.plain
    _time.sleep(0.2)
    t2 = s._busy_renderable().text.plain
    m1 = re.search(r"([\d.]+)s", t1)
    m2 = re.search(r"([\d.]+)s", t2)
    v1 = float(m1.group(1))
    v2 = float(m2.group(1))
    assert v2 > v1, "timer text must advance during silent wait: " + str(v1) + "s -> " + str(v2) + "s"
    s.on_finish()
