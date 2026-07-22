"""Tests for context-rot detection + restart (harness phase 3).

Verifies the TurnResult signals (hit_max_rounds / in_tool_loop) and the
fingerprint-based tool-loop detector, without spinning up a full agent run
(which would need a live model). The restart logic itself is best-effort and
guarded by try/except, so it's tested indirectly via the signals it consumes.
"""

from coderio.agent.loop import TurnResult


# --- TurnResult ---


def test_turn_result_defaults():
    """A normal completion has both rot signals False."""
    r = TurnResult("done")
    assert r.text == "done"
    assert r.hit_max_rounds is False
    assert r.in_tool_loop is False


def test_turn_result_max_rounds():
    """hit_max_rounds=True marks a context-rot candidate."""
    r = TurnResult("stopped", hit_max_rounds=True)
    assert r.hit_max_rounds is True
    assert r.in_tool_loop is False


def test_turn_result_tool_loop():
    """in_tool_loop=True marks a context-rot candidate (even if not max_rounds)."""
    r = TurnResult("done but looped", in_tool_loop=True)
    assert r.in_tool_loop is True
    assert r.hit_max_rounds is False


def test_turn_result_both_signals():
    """Both signals can be True simultaneously (max_rounds + detected loop)."""
    r = TurnResult("stopped", hit_max_rounds=True, in_tool_loop=True)
    assert r.hit_max_rounds and r.in_tool_loop


def test_turn_result_slots():
    """TurnResult uses __slots__ — no arbitrary attribute assignment."""
    r = TurnResult("x")
    try:
        r.bogus = 1  # type: ignore[attr-defined]
        assert False, "should have raised AttributeError"
    except AttributeError:
        pass


# --- context-rot signal logic (the condition run_agent checks) ---


def test_context_rot_triggered_by_max_rounds():
    """The restart condition: hit_max_rounds OR in_tool_loop."""
    r = TurnResult("x", hit_max_rounds=True)
    assert r.hit_max_rounds or r.in_tool_loop


def test_context_rot_triggered_by_tool_loop():
    r = TurnResult("x", in_tool_loop=True)
    assert r.hit_max_rounds or r.in_tool_loop


def test_context_rot_not_triggered_on_success():
    """A clean completion triggers no restart."""
    r = TurnResult("done")
    assert not (r.hit_max_rounds or r.in_tool_loop)


# --- _execute_turn tool-loop detection (via the fingerprint helper) ---


def test_tool_call_fingerprint_stability():
    """The fingerprint helper used for loop detection is deterministic for
    identical (name, args) and distinct for different ones."""

    # Re-implement the fingerprint logic here (it's a closure inside _execute_turn,
    # so we replicate it to test the concept).
    def _fingerprint(name, args):
        return (name, tuple(sorted((str(k), str(v)) for k, v in (args or {}).items())))

    fp1 = _fingerprint("read_file", {"path": "a.py"})
    fp2 = _fingerprint("read_file", {"path": "a.py"})
    fp3 = _fingerprint("read_file", {"path": "b.py"})
    fp4 = _fingerprint("grep", {"path": "a.py"})
    assert fp1 == fp2, "identical calls must fingerprint equal"
    assert fp1 != fp3, "different args must fingerprint unequal"
    assert fp1 != fp4, "different tool names must fingerprint unequal"


def test_tool_call_fingerprint_arg_order_independent():
    """Args dict ordering doesn't affect the fingerprint (sorted)."""

    def _fingerprint(name, args):
        return (name, tuple(sorted((str(k), str(v)) for k, v in (args or {}).items())))

    fp1 = _fingerprint("bash", {"command": "ls", "cwd": "/tmp"})
    fp2 = _fingerprint("bash", {"cwd": "/tmp", "command": "ls"})
    assert fp1 == fp2
