"""Context compaction — summarize old messages when approaching the token limit.

The realistic context-rot scenario: a long turn does 20+ read_file/grep calls,
each adding a tool result, until the convo blows the model's context window and
the next run_step fails. This module compacts the convo in place: old messages
become a single system-role summary, recent messages (including any in-flight
tool_call/tool_result pairs) are preserved verbatim.

The compaction is conservative:
  1. Split at keep_recent_n from the end.
  2. Walk backward from the split point; if it falls inside a tool_call pair
     (an assistant message with tool_calls whose tool results would be summarized
     away), pull the whole pair into the kept section — langchain rejects a
     ToolMessage whose tool_call_id was never issued in a prior assistant turn.
  3. Summarize the to-compact section via one model.invoke call.
  4. Return [Message.system(summary, kind="context_summary")] + kept section.

Failure to summarize (model error / timeout) is non-fatal: the original convo is
returned unchanged and a warning is emitted.
"""

from __future__ import annotations

import logging
from typing import Any

from coderio.session.message import Message

_log = logging.getLogger(__name__)

# The summary prompt is deliberately structured: ask for the categories of info
# that matter for continuing the task (decisions made, files touched, open
# questions), not a free-form retelling. Keeps the summary focused and short.
_SUMMARY_PROMPT = """\
请将以下对话历史压缩成一段简洁的中文摘要，供 AI agent 继续工作时参考。
重点保留：
  • 用户的原始需求和关键约束
  • 已经做出的决策和理由
  • 已经读取/修改过的文件及其关键内容
  • 已发现的问题、错误或未解决的疑问
  • 当前任务的进展状态

省略：冗长的工具输出原文、重复的尝试、无关的寒暄。
输出一段 200-500 字的摘要，不要分点编号，用自然段落。

---对话历史---
"""


def _find_safe_split(convo: list[Message], keep_recent: int) -> int:
    """Find the index at which to split convo into (to_compact, to_keep).

    Starts at len(convo) - keep_recent and walks backward while that position
    falls inside a tool_call/tool_result group. A "group" is an assistant
    message carrying tool_calls followed by its tool result messages. Splitting
    inside such a group leaves dangling tool_call_ids that langchain/Anthropic
    reject, so the whole group must stay together on the kept side.

    Returns the split index (convo[:split] = to_compact, convo[split:] = kept).
    """
    split = max(0, len(convo) - keep_recent)
    # Walk backward while the message at `split - 1` is a tool result whose
    # originating assistant tool_call would land in the to_compact section.
    while split > 0:
        # If convo[split] is a tool result, its caller assistant msg is before
        # it — we'd split the pair. Pull the tool results into kept by moving
        # split left until we're past the whole group.
        if convo[split].role == "tool":
            split -= 1
            continue
        # If convo[split-1] is an assistant with tool_calls, those tool results
        # start at convo[split] — splitting here orphans them.
        if split > 0 and convo[split - 1].role == "assistant" and convo[split - 1].tool_calls:
            split -= 1
            continue
        break
    return split


def _format_for_summary(messages: list[Message]) -> str:
    """Render messages into a compact text form for the summarizer model.

    Drops phase-timeline system messages (they're metadata, not conversation).
    Truncates very long tool results so the summary call itself doesn't blow
    the window — each tool result is capped.
    """
    _TOOL_RESULT_CAP = 500
    lines = []
    for m in messages:
        if m.role == "system" and m.kind == "phase_timeline":
            continue  # observability metadata, not conversation
        if m.role == "user":
            c = m.content if isinstance(m.content, str) else str(m.content)
            lines.append(f"[用户] {c}")
        elif m.role == "assistant":
            tc_summary = ""
            if m.tool_calls:
                names = ", ".join(tc.name for tc in m.tool_calls)
                tc_summary = f" (调用工具: {names})"
            lines.append(f"[助手] {m.content}{tc_summary}")
        elif m.role == "tool":
            c = m.content if isinstance(m.content, str) else str(m.content)
            if len(c) > _TOOL_RESULT_CAP:
                c = c[:_TOOL_RESULT_CAP] + "…[截断]"
            lines.append(f"[工具结果:{m.name}] {c}")
        elif m.role == "system":
            lines.append(f"[系统] {m.content}")
    return "\n".join(lines)


def compact_convo(
    convo: list[Message],
    model: Any,
    keep_recent: int = 8,
) -> list[Message]:
    """Compact a convo by summarizing old messages into one system message.

    Args:
        convo: the full message list (will NOT be mutated; returns a new list)
        model: a langchain chat model (ChatOpenAI/ChatAnthropic) for the summary
        keep_recent: how many recent messages to preserve verbatim (default 8)

    Returns a new list. If compaction fails (model error) or there's nothing to
    compact (too few messages), returns the original convo unchanged.
    """
    if len(convo) <= keep_recent + 2:
        # Too few messages to meaningfully compact — keep as-is.
        return convo

    split = _find_safe_split(convo, keep_recent)
    if split <= 0:
        # Nothing safely compactable (the kept section absorbed everything).
        return convo

    to_compact = convo[:split]
    to_keep = convo[split:]

    history_text = _format_for_summary(to_compact)
    if not history_text.strip():
        return convo

    try:
        summary_response = model.invoke(_SUMMARY_PROMPT + history_text)
        summary = getattr(summary_response, "content", str(summary_response))
        if isinstance(summary, list):
            summary = " ".join(b.get("text", "") for b in summary if isinstance(b, dict) and b.get("type") == "text")
        if not summary or not summary.strip():
            _log.warning("compaction produced empty summary — keeping original convo")
            return convo
    except Exception as e:
        _log.warning("compaction failed (%s: %s) — keeping original convo", type(e).__name__, e)
        return convo

    summary_msg = Message.system(
        f"[上下文摘要] 以下为此前对话的压缩摘要：\n\n{summary}",
        kind="context_summary",
    )
    return [summary_msg] + to_keep


def should_compact(input_tokens: int, context_limit: int, trigger_ratio: float) -> bool:
    """Decide whether compaction should fire based on the last model call's usage.

    Uses the provider-reported input_tokens (exact, not a tokenizer heuristic)
    against a fraction of the model's context window.
    """
    if context_limit <= 0 or trigger_ratio <= 0:
        return False
    return input_tokens > context_limit * trigger_ratio
