"""Microcompact: zero-model-call context trimming (change plan Phase 3,
borrowed from ZCode's compact/microcompact.ts, 2026-09-21 source study).

deepagents owns compaction, and its compaction is all-or-nothing and
model-driven (a summarization call costs tokens and latency). The cheapest
context win is purely local: in a long turn the OLDEST tool results are
usually the largest messages in the history, and their verbatim text is
rarely needed once the agent has moved past them. Replacing them with a
one-line placeholder defers the expensive summarization — sometimes avoids
it entirely.

Contract (deliberately conservative):
  - runs in wrap_model_call, BEFORE the request reaches the model;
  - only TOOL results are eligible; user/assistant/system messages are never
    touched (dropping the user's request or the assistant's reasoning would
    change the conversation's meaning, not just its size);
  - the most recent KEEP_RECENT tool results are always preserved verbatim;
  - operates on a COPY passed via request.override — the graph state and the
    session jsonl keep the full history, so /resume, /export and audits stay
    truthful;
  - no-op unless the estimated size exceeds 90% of the known context window
    (context_limit <= 0 = unknown = disabled — never guess a window);
  - no-op when the projected saving is below MIN_SAVING_TOKENS: a rewrite
    that saves ~nothing is pure churn.

Estimation is the chars/4 heuristic — deliberately crude. This middleware
is a DELAY mechanism for summarization, not an exact budget enforcer; being
off by 10% changes when trimming happens, never what the model sees.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import ToolMessage

from coderio.agent.sync_only import SyncOnlyMiddleware

_log = logging.getLogger(__name__)

# Most recent tool results always kept verbatim (the agent is very likely
# still reasoning about them).
KEEP_RECENT = 5
# Below this projected saving, don't rewrite anything.
MIN_SAVING_TOKENS = 256
# Replacement text for a cleared tool result.
PLACEHOLDER = "[Old tool result content cleared]"
# Trigger threshold as a fraction of the known context window.
BUDGET_FRACTION = 0.9


def _estimate_tokens(text: str) -> int:
    """Crude token estimate (chars/4). Good enough for a delay mechanism."""
    return len(text) // 4


def _message_text(m: Any) -> str:
    content = getattr(m, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return str(content)


def _is_tool_message(m: Any) -> bool:
    return isinstance(m, ToolMessage) or getattr(m, "type", "") == "tool"


class MicrocompactMiddleware(SyncOnlyMiddleware):
    """Clear old tool results locally when the context runs long."""

    def __init__(self, context_limit: int) -> None:
        # <= 0 = unknown window = disabled (never guess).
        self.context_limit = context_limit
        self.enabled = context_limit > 0
        self.last_saving_tokens = 0  # observability: what the last pass saved

    def _trim(self, messages: list) -> tuple[list, int]:
        """Return (possibly-trimmed copy, estimated tokens saved)."""
        tool_idx = [i for i, m in enumerate(messages) if _is_tool_message(m)]
        if len(tool_idx) <= KEEP_RECENT:
            return messages, 0
        budget = int(self.context_limit * BUDGET_FRACTION)
        est = sum(_estimate_tokens(_message_text(m)) for m in messages)
        if est <= budget:
            return messages, 0
        new_messages = list(messages)
        saved = 0
        # Oldest first, never touching the most recent KEEP_RECENT results.
        for i in tool_idx[: len(tool_idx) - KEEP_RECENT]:
            m = new_messages[i]
            text = _message_text(m)
            if not text or text == PLACEHOLDER:
                continue
            new_messages[i] = ToolMessage(
                content=PLACEHOLDER,
                tool_call_id=getattr(m, "tool_call_id", "") or "",
                name=getattr(m, "name", None),
            )
            saved += _estimate_tokens(text) - _estimate_tokens(PLACEHOLDER)
            est -= _estimate_tokens(text) - _estimate_tokens(PLACEHOLDER)
            if est <= budget:
                break
        return new_messages, saved

    def wrap_model_call(self, request, handler):
        if not self.enabled:
            return handler(request)
        try:
            trimmed, saved = self._trim(list(request.messages))
        except Exception as e:  # noqa: BLE001 — trimming must never break a turn
            _log.warning("microcompact skipped (%s)", e)
            return handler(request)
        self.last_saving_tokens = saved
        if saved < MIN_SAVING_TOKENS:
            return handler(request)
        return handler(request.override(messages=trimmed))
