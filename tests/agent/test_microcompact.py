"""Tests for MicrocompactMiddleware (local old-tool-result trimming).

Contract under test:
  - disabled unless a context window is known (never guess);
  - only TOOL results are trimmed, and only outside the recent tail;
  - trimming happens on a COPY (the request's original messages survive);
  - a projected saving below the floor is a no-op;
  - user/assistant messages are never touched.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from coderio.agent.microcompact import (
    KEEP_RECENT,
    MIN_SAVING_TOKENS,
    PLACEHOLDER,
    MicrocompactMiddleware,
)


class _Req:
    def __init__(self, messages):
        self.messages = messages

    def override(self, **kw):
        return _Req(kw["messages"])


def _handler_capture(seen: list):
    def handler(request):
        seen.append(request)
        return "ok"

    return handler


def _tool_result(i: int, size: int = 4000) -> ToolMessage:
    return ToolMessage(content=f"result {i}: " + "x" * size, tool_call_id=f"tc{i}", name="execute")


def _history(n_tools: int, size: int = 4000) -> list:
    """A plausible turn: user → (assistant tool_call → tool result) * n."""
    msgs: list = [HumanMessage(content="do the thing")]
    for i in range(n_tools):
        call = {"name": "execute", "args": {"command": f"step {i}"}, "id": f"tc{i}", "type": "tool_call"}
        msgs.append(AIMessage(content="", tool_calls=[call]))
        msgs.append(_tool_result(i, size))
    return msgs


# ------------------------------------------------------------- disabled paths
def test_disabled_without_known_context_limit():
    mw = MicrocompactMiddleware(context_limit=0)
    seen: list = []
    msgs = _history(10, size=100_000)  # enormous, but no known window
    out = mw.wrap_model_call(_Req(msgs), _handler_capture(seen))
    assert out == "ok"
    assert seen[0].messages is msgs, "unknown window = pass-through, untouched"


def test_under_budget_is_passthrough():
    mw = MicrocompactMiddleware(context_limit=200_000)
    seen: list = []
    msgs = _history(8, size=100)  # tiny
    mw.wrap_model_call(_Req(msgs), _handler_capture(seen))
    assert seen[0].messages is msgs


# ------------------------------------------------------------- trimming
def test_trims_old_tool_results_keeps_recent():
    mw = MicrocompactMiddleware(context_limit=2000)  # budget 1800 tokens
    seen: list = []
    msgs = _history(10, size=4000)
    mw.wrap_model_call(_Req(msgs), _handler_capture(seen))
    trimmed = seen[0].messages
    assert trimmed is not msgs, "trimming must operate on a copy"
    # The most recent KEEP_RECENT tool results survive verbatim.
    tool_msgs = [m for m in trimmed if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 10
    kept = [m for m in tool_msgs if m.content != PLACEHOLDER]
    assert len(kept) == KEEP_RECENT
    # Older ones carry the placeholder.
    cleared = [m for m in tool_msgs if m.content == PLACEHOLDER]
    assert cleared, "old tool results must be cleared"
    # User/assistant messages are never touched.
    assert trimmed[0].content == "do the thing"
    assert sum(1 for m in trimmed if isinstance(m, AIMessage)) == 10


def test_placeholder_preserves_tool_call_id():
    mw = MicrocompactMiddleware(context_limit=2000)
    seen: list = []
    msgs = _history(10, size=4000)
    mw.wrap_model_call(_Req(msgs), _handler_capture(seen))
    cleared = [m for m in seen[0].messages if isinstance(m, ToolMessage) and m.content == PLACEHOLDER]
    assert all(m.tool_call_id.startswith("tc") for m in cleared), "tool_call_id pairing must survive"


def test_small_saving_is_noop():
    """A rewrite that saves ~nothing is churn — skip it."""
    mw = MicrocompactMiddleware(context_limit=2000)
    seen: list = []
    # Just over budget, but only barely: trimming a few small results saves
    # less than MIN_SAVING_TOKENS.
    msgs = _history(8, size=110)  # 8 * (110//4≈27) ≈ 216 tokens + overhead
    mw.wrap_model_call(_Req(msgs), _handler_capture(seen))
    assert seen[0].messages is msgs or mw.last_saving_tokens >= MIN_SAVING_TOKENS


def test_saving_floor_constant_is_sane():
    assert MIN_SAVING_TOKENS >= 128, "the floor must be meaningful, not noise-level"
