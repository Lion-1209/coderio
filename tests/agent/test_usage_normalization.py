"""P2-N7: streamed usage must use the last cumulative chunk value, not the
merged (summed) AIMessage usage_metadata.

Streaming providers (OpenAI-compatible, verified on stepfun_api in the
2026-09-14 audit) attach CUMULATIVE usage to every SSE chunk; langchain's
AIMessageChunk merge SUMS those, so a complete message built from a stream
carries an inflated total (13 chunks × prompt=16 → 208 instead of 16).
_run_stream tracks the last chunk-level cumulative value per model call and
_emit_message consumes it in preference to the merged sum. Non-streaming
providers never populate chunk-level usage — the complete-message metadata
stays authoritative (fallback path).
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, AIMessageChunk

from coderio.agent.deep_loop import _run_stream


class _RecordingStream:
    """Minimal stream: records add_usage calls; every other hook is absent and
    deep_loop's hasattr guards skip it."""

    def __init__(self):
        self.usage_calls: list[dict] = []

    def add_usage(self, meta: dict) -> None:
        self.usage_calls.append(dict(meta))


class _FakeSession:
    def __init__(self):
        self.messages = []

    def append(self, m):
        self.messages.append(m)


class _FakeAgent:
    """Yields scripted (mode, event) pairs, exactly like graph.stream with
    stream_mode=["messages", "updates", "custom"]."""

    def __init__(self, events):
        self._events = events

    def stream(self, *a, **k):
        yield from self._events


def _chunk(text: str, inp: int, out: int):
    return (
        "messages",
        (
            AIMessageChunk(
                content=text,
                usage_metadata={"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out},
            ),
            {"langgraph_node": "model"},
        ),
    )


def test_streamed_usage_uses_last_cumulative_not_merged_sum():
    """The audit's exact shape: two chunks carrying cumulative 16/4 → 16/28,
    then a merged complete message with the inflated sum 208/180/388. The
    stream must be fed 16/28/44 once."""
    events = [
        _chunk("he", 16, 4),
        _chunk("llo", 16, 28),
        (
            "updates",
            {
                "model": {
                    "messages": [
                        AIMessage(
                            content="hello",
                            usage_metadata={"input_tokens": 208, "output_tokens": 180, "total_tokens": 388},
                        )
                    ]
                }
            },
        ),
    ]
    stream = _RecordingStream()
    _run_stream(_FakeAgent(events), {}, "t", 50, stream, _FakeSession(), set(), [])
    assert stream.usage_calls == [{"input_tokens": 16, "output_tokens": 28, "total_tokens": 44}], (
        "must use the last cumulative chunk value, never the merged sum"
    )


def test_nonstream_falls_back_to_complete_message_usage():
    """No streamed chunks carrying usage (non-streaming provider) — the
    complete message's usage_metadata is authoritative and must flow through
    unchanged."""
    events = [
        (
            "updates",
            {
                "model": {
                    "messages": [
                        AIMessage(
                            content="answer",
                            usage_metadata={"input_tokens": 16, "output_tokens": 28, "total_tokens": 44},
                        )
                    ]
                }
            },
        ),
    ]
    stream = _RecordingStream()
    _run_stream(_FakeAgent(events), {}, "t", 50, stream, _FakeSession(), set(), [])
    assert stream.usage_calls == [{"input_tokens": 16, "output_tokens": 28, "total_tokens": 44}]


def test_multi_turn_usage_accounted_per_model_call():
    """Tool round-trip: call1 (cum 16/28) → tool-call message → call2
    (cum 20/50) → final message. Each turn must contribute its own vendor
    total, so a summing consumer lands on 36/78 — not the merged inflation."""
    events = [
        _chunk("a", 16, 10),
        _chunk("b", 16, 28),
        (
            "updates",
            {
                "model": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {"name": "execute", "args": {"command": "ls"}, "id": "tc1", "type": "tool_call"}
                            ],
                            usage_metadata={"input_tokens": 32, "output_tokens": 38, "total_tokens": 70},
                        )
                    ]
                }
            },
        ),
        _chunk("c", 20, 30),
        _chunk("d", 20, 50),
        (
            "updates",
            {
                "model": {
                    "messages": [
                        AIMessage(
                            content="done",
                            usage_metadata={"input_tokens": 40, "output_tokens": 80, "total_tokens": 120},
                        )
                    ]
                }
            },
        ),
    ]
    stream = _RecordingStream()
    _run_stream(_FakeAgent(events), {}, "t", 50, stream, _FakeSession(), set(), [])
    assert stream.usage_calls == [
        {"input_tokens": 16, "output_tokens": 28, "total_tokens": 44},
        {"input_tokens": 20, "output_tokens": 50, "total_tokens": 70},
    ]
    # What RichStream.add_usage's accumulator would show after the turn:
    totals = {
        "input_tokens": sum(c["input_tokens"] for c in stream.usage_calls),
        "output_tokens": sum(c["output_tokens"] for c in stream.usage_calls),
    }
    assert totals == {"input_tokens": 36, "output_tokens": 78}


def test_chunk_usage_used_even_when_complete_message_has_none():
    """Some merge paths drop usage_metadata entirely — the chunk-tracked
    value must still survive."""
    events = [
        _chunk("hi", 5, 2),
        ("updates", {"model": {"messages": [AIMessage(content="hi")]}}),
    ]
    stream = _RecordingStream()
    _run_stream(_FakeAgent(events), {}, "t", 50, stream, _FakeSession(), set(), [])
    assert stream.usage_calls == [{"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}]
