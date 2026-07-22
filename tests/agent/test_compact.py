"""Tests for context compaction (harness phase 2).

Verifies the compaction algorithm preserves tool_call/tool_result pairs, handles
model failures gracefully, and the should_compact threshold logic.
"""

from coderio.agent.compact import (
    compact_convo,
    should_compact,
    _find_safe_split,
)
from coderio.session.message import Message, ToolCall


# --- Fake model for summary calls ---


class _FakeModel:
    """Returns a canned summary. Records the prompt it received."""

    def __init__(self, summary_text="这是摘要", fail=False):
        self._summary = summary_text
        self._fail = fail
        self.received_prompt = ""

    def invoke(self, prompt, **kwargs):
        self.received_prompt = prompt
        if self._fail:
            raise RuntimeError("simulated model failure")

        # langchain-like response object
        class _Resp:
            content = self._summary

        return _Resp()


def _make_convo(n_pairs: int = 5) -> list[Message]:
    """Build a convo with n_pairs of (user + assistant) messages."""
    msgs = []
    for i in range(n_pairs):
        msgs.append(Message.user(f"用户消息 {i}"))
        msgs.append(Message.assistant(f"助手回复 {i}"))
    return msgs


def _make_convo_with_tool_calls() -> list[Message]:
    """Build a convo where the tail has tool_call/tool_result pairs."""
    msgs = []
    # Old conversation (to be compacted)
    for i in range(3):
        msgs.append(Message.user(f"用户消息 {i}"))
        msgs.append(Message.assistant(f"助手回复 {i}"))
    # Recent tool activity (must be kept verbatim)
    msgs.append(
        Message.assistant(
            "读取文件",
            tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "a.py"})],
        )
    )
    msgs.append(
        Message.tool_result(tool_call_id="c1", name="read_file", content="file body")
    )
    msgs.append(Message.user("继续"))
    msgs.append(Message.assistant("好的"))
    return msgs


# --- should_compact ---


def test_should_compact_above_threshold():
    assert should_compact(100_000, context_limit=128_000, trigger_ratio=0.75) is True


def test_should_compact_below_threshold():
    assert should_compact(50_000, context_limit=128_000, trigger_ratio=0.75) is False


def test_should_compact_disabled_when_limit_zero():
    assert should_compact(999_999, context_limit=0, trigger_ratio=0.75) is False


def test_should_compact_disabled_when_ratio_zero():
    assert should_compact(999_999, context_limit=128_000, trigger_ratio=0) is False


# --- _find_safe_split (tool_call pair protection) ---


def test_safe_split_keeps_tool_call_pair_together():
    """Split point must not land between an assistant(tool_calls) and its tool result."""
    convo = _make_convo_with_tool_calls()
    split = _find_safe_split(convo, keep_recent=4)
    # Everything from `split` onward must be balanced: no orphan tool results.
    kept = convo[split:]
    # If there's a tool result in kept, its originating assistant tool_call must also be in kept.
    tool_call_ids_in_kept = set()
    for m in kept:
        if m.role == "assistant" and m.tool_calls:
            for tc in m.tool_calls:
                tool_call_ids_in_kept.add(tc.id)
    for m in kept:
        if m.role == "tool":
            assert m.tool_call_id in tool_call_ids_in_kept, (
                f"tool result {m.tool_call_id} orphaned by split at {split}"
            )


def test_safe_split_never_negative():
    """Split index is always >= 0."""
    convo = _make_convo_with_tool_calls()
    split = _find_safe_split(convo, keep_recent=100)  # keep_recent > len
    assert split >= 0


# --- compact_convo: core behavior ---


def test_compact_short_convo_returns_unchanged():
    """Too few messages → no compaction, original convo returned."""
    model = _FakeModel()
    convo = _make_convo(2)  # 4 messages, keep_recent=8 → too few
    result = compact_convo(convo, model, keep_recent=8)
    assert result is convo  # same object, unchanged


def test_compact_produces_summary_plus_kept():
    """A long convo compacts to [summary_system_msg] + kept tail."""
    model = _FakeModel(summary_text="压缩后的摘要内容")
    convo = _make_convo(8)  # 16 messages
    result = compact_convo(convo, model, keep_recent=4)
    assert len(result) < len(convo), "compacted should be shorter"
    assert result[0].role == "system"
    assert result[0].kind == "context_summary"
    assert "压缩后的摘要内容" in result[0].content


def test_compact_model_failure_returns_original():
    """When the summarizer model raises, the original convo is returned unchanged."""
    model = _FakeModel(fail=True)
    convo = _make_convo(8)
    result = compact_convo(convo, model, keep_recent=4)
    assert result is convo  # fallback: return original on failure


def test_compact_empty_summary_returns_original():
    """When the model returns an empty summary, keep the original convo."""
    model = _FakeModel(summary_text="   ")  # whitespace-only
    convo = _make_convo(8)
    result = compact_convo(convo, model, keep_recent=4)
    assert result is convo


def test_compact_preserves_tool_call_pairs_in_kept():
    """After compaction, no orphan tool results exist in the result."""
    model = _FakeModel()
    convo = _make_convo_with_tool_calls()
    result = compact_convo(convo, model, keep_recent=4)
    # Verify tool_call/tool_result balance in the result.
    issued_ids = set()
    for m in result:
        if m.role == "assistant" and m.tool_calls:
            for tc in m.tool_calls:
                issued_ids.add(tc.id)
    for m in result:
        if m.role == "tool":
            assert m.tool_call_id in issued_ids, (
                "compaction produced an orphan tool result"
            )


def test_compact_does_not_mutate_input():
    """compact_convo must not mutate the input convo list (returns a new list)."""
    model = _FakeModel()
    convo = _make_convo(8)
    original_len = len(convo)
    _ = compact_convo(convo, model, keep_recent=4)
    assert len(convo) == original_len, "input convo was mutated"


def test_compact_drops_phase_timeline_messages():
    """Phase-timeline system messages in the to-compact section are not summarized
    (they're metadata); they just disappear from the compacted result."""
    model = _FakeModel()
    convo = _make_convo(8)
    # Insert a phase-timeline message in the middle (old section)
    convo.insert(2, Message.system('[{"state":"explore"}]', kind="phase_timeline"))
    result = compact_convo(convo, model, keep_recent=4)
    # No phase_timeline messages should survive in the result
    for m in result:
        assert m.kind != "phase_timeline"
