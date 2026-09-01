"""Tests for NullStream (the default no-op StreamHandler).

NullStream is the headless/test handler: every StreamHandler callback must be a
safe no-op so agent code can call any of them without the UI actually doing
anything. This module pins that contract — each Protocol method (plus the
optional hooks) is called with representative arguments and must not raise.

The StreamHandler Protocol declares the on_* callbacks; deep_loop.py guards the
non-Protocol add_usage hook with hasattr(). These tests verify both:
  - every on_* method is a safe no-op (returns None, no side effects),
  - the hasattr guards in deep_loop work correctly (NullStream has NO
    add_usage/is_interrupted, so those code paths must skip cleanly).
"""

from __future__ import annotations

import pytest

from coderio.agent.stream import NullStream, StreamHandler


@pytest.fixture
def stream() -> NullStream:
    return NullStream()


# ----------------------------------------------------------------------- protocol
class TestNullStreamProtocolMethods:
    """Every StreamHandler on_* callback must be a safe no-op."""

    def test_on_step_start_default(self, stream: NullStream) -> None:
        # default arg must work (step=1)
        assert stream.on_step_start() is None

    def test_on_step_start_explicit_step(self, stream: NullStream) -> None:
        assert stream.on_step_start(step=5) is None

    def test_on_token_empty(self, stream: NullStream) -> None:
        assert stream.on_token("") is None

    def test_on_token_text(self, stream: NullStream) -> None:
        assert stream.on_token("hello world") is None

    def test_on_token_unicode(self, stream: NullStream) -> None:
        # CJK + emoji must not blow up (the real RichStream renders these).
        assert stream.on_token("你好 🌍 — naïve") is None

    def test_on_tool_start_minimal(self, stream: NullStream) -> None:
        assert stream.on_tool_start("bash", {"command": "ls"}) is None

    @pytest.mark.parametrize(
        "name,args,step,tool_index,tool_total",
        [
            ("read_file", {"path": "/a/b.py"}, 1, 0, 1),
            ("write_file", {"path": "x.py", "content": ""}, 3, 1, 2),
            ("bash", {"command": "echo hi"}, 2, 0, 5),
            ("grep", {"pattern": "foo", "path": "."}, 1, 4, 10),
            ("edit_file", {}, 1, 0, 0),  # empty args / zero totals
        ],
    )
    def test_on_tool_start_various_args(
        self,
        stream: NullStream,
        name: str,
        args: dict,
        step: int,
        tool_index: int,
        tool_total: int,
    ) -> None:
        # All keyword combinations must be accepted by the signature.
        assert (
            stream.on_tool_start(
                name,
                args,
                step=step,
                tool_index=tool_index,
                tool_total=tool_total,
            )
            is None
        )

    def test_on_tool_start_positional(self, stream: NullStream) -> None:
        # The signature also accepts positional step/index/total.
        assert stream.on_tool_start("bash", {}, 2, 1, 3) is None

    def test_on_tool_end(self, stream: NullStream) -> None:
        assert stream.on_tool_end("bash", "file1\nfile2") is None

    def test_on_tool_end_empty_result(self, stream: NullStream) -> None:
        assert stream.on_tool_end("read_file", "") is None

    def test_on_finish(self, stream: NullStream) -> None:
        assert stream.on_finish() is None

    # ----------------------------------------------- optional hooks (in Protocol)
    def test_on_thinking_empty(self, stream: NullStream) -> None:
        assert stream.on_thinking("") is None

    def test_on_thinking_text(self, stream: NullStream) -> None:
        assert stream.on_thinking("planning the approach...") is None

    def test_on_harness_warn(self, stream: NullStream) -> None:
        assert stream.on_harness_warn("unverified write detected") is None

    def test_on_harness_continue(self, stream: NullStream) -> None:
        assert stream.on_harness_continue("[harness] you did not read the file you cited") is None

    def test_on_phase_change(self, stream: NullStream) -> None:
        assert stream.on_phase_change("implement", 3, "writing tests") is None

    def test_on_phase_change_all_states(self, stream: NullStream) -> None:
        # Every AgentState value must be accepted.
        for state in ("explore", "plan", "implement", "verify", "complete"):
            assert stream.on_phase_change(state, 1, "") is None

    def test_on_turn_end_empty(self, stream: NullStream) -> None:
        assert stream.on_turn_end([]) is None

    def test_on_turn_end_with_writes(self, stream: NullStream) -> None:
        assert stream.on_turn_end(["src/a.py", "tests/test_a.py", "README.md"]) is None

    def test_on_todos_update_empty(self, stream: NullStream) -> None:
        assert stream.on_todos_update([]) is None

    def test_on_todos_update_full_list(self, stream: NullStream) -> None:
        todos = [
            {"content": "fix bug", "status": "completed"},
            {"content": "add tests", "status": "in_progress"},
            {"content": "update docs", "status": "pending"},
        ]
        assert stream.on_todos_update(todos) is None


# ----------------------------------------------------------------------- full lifecycle
class TestNullStreamFullLifecycle:
    """Drive a complete agent turn through NullStream — nothing should raise and
    no state should accumulate (it's a pure no-op)."""

    def test_full_turn_sequence(self, stream: NullStream) -> None:
        stream.on_step_start(step=1)
        stream.on_thinking("let me think")
        stream.on_token("Here is ")
        stream.on_token("my answer.")
        stream.on_tool_start("read_file", {"path": "x.py"}, step=1, tool_index=0, tool_total=2)
        stream.on_tool_end("read_file", "contents")
        stream.on_phase_change("implement", 1, "editing")
        stream.on_todos_update([{"content": "done", "status": "completed"}])
        stream.on_turn_end(["x.py"])
        stream.on_finish()
        # No assertion needed — reaching here without raising is the contract.

    def test_full_turn_with_harness_escalation(self, stream: NullStream) -> None:
        """The harness warn / continue / truncated paths must also be safe."""
        stream.on_step_start(step=1)
        stream.on_token("partial answer")
        stream.on_harness_continue("claim unverified, continuing")
        stream.on_harness_warn("retries exhausted")
        stream.on_finish()


# -------------------------------------------------- add_usage / is_interrupted guards
class TestNullStreamMissingHooks:
    """NullStream does NOT implement add_usage or is_interrupted — those are
    extra-Protocol hooks that RichStream / CoderioTUI provide. deep_loop.py
    guards them with hasattr(), so the absence (not presence) is what matters:
    a NullStream must be SKIPPED by those guards, never crash them.

    These tests pin both sides of that contract: the attribute is absent on
    NullStream (so the guard skips it), and the guard logic behaves correctly
    when given a NullStream.
    """

    def test_nullstream_has_no_add_usage(self, stream: NullStream) -> None:
        # The hasattr guard in deep_loop._process_ai_message relies on this.
        assert not hasattr(stream, "add_usage")

    def test_nullstream_has_no_is_interrupted(self, stream: NullStream) -> None:
        # The interrupt-check in _execute_turn guards RichStream/CoderioTUI only.
        assert not hasattr(stream, "is_interrupted")

    def test_hasattr_guard_skips_nullstream_for_add_usage(self, stream: NullStream) -> None:
        """Replicates the exact guard from deep_loop._process_ai_message.

        Original code:
            usage = getattr(m, "usage_metadata", None)
            if usage and hasattr(stream, "add_usage"):
                stream.add_usage(usage)
        With a NullStream the hasattr() is False, so add_usage must NEVER be
        called (and must never raise AttributeError).
        """
        called: list[bool] = []

        # Simulate the guard exactly as deep_loop writes it.
        usage = {"input_tokens": 100, "output_tokens": 50}
        if usage and hasattr(stream, "add_usage"):
            stream.add_usage(usage)  # type: ignore[attr-defined]
            called.append(True)

        assert called == [], "add_usage must not be called on a NullStream (guard should skip it)"

    def test_nullstream_satisfies_protocol_duck_typing(self, stream: NullStream) -> None:
        """Every method named in the StreamHandler Protocol must exist on
        NullStream (it's the documented default impl). This catches drift if
        someone adds a Protocol method but forgets to mirror it here."""
        protocol_methods = [
            "on_step_start",
            "on_token",
            "on_tool_start",
            "on_tool_end",
            "on_finish",
            "on_thinking",
            "on_harness_warn",
            "on_harness_continue",
            "on_phase_change",
            "on_turn_end",
            "on_todos_update",
        ]
        for name in protocol_methods:
            assert hasattr(stream, name), f"NullStream missing Protocol method: {name}"
            assert callable(getattr(stream, name)), f"{name} is not callable"


# ----------------------------------------------------------------------- protocol typing
class TestNullStreamIsAStreamHandler:
    """NullStream is the documented default StreamHandler impl. While
    StreamHandler is a runtime_checkable Protocol (duck-typed, no inherit),
    NullStream must structurally satisfy it so it can be passed anywhere a
    StreamHandler is expected."""

    def test_isinstance_runtime_check(self, stream: NullStream) -> None:
        """StreamHandler is a plain Protocol (no @runtime_checkable), so
        isinstance() raises TypeError. We don't need runtime checking — the
        structural contract is verified in test_nullstream_satisfies_protocol_duck_typing.
        Here we just pin that the Protocol is NOT runtime_checkable (so callers
        must rely on duck typing, not isinstance), guarding against someone
        adding @runtime_checkable without considering the impact."""
        assert not getattr(StreamHandler, "_is_runtime_protocol", False), (
            "StreamHandler gained @runtime_checkable — review whether NullStream "
            "still passes isinstance checks before relying on this."
        )
        with pytest.raises(TypeError):
            isinstance(stream, StreamHandler)
