"""Content/result → text normalization — single source (P2, 2026-09-04).

Three near-duplicate implementations had drifted: the hooks variant dropped
exit codes (PostToolUse `tool_response` lacked them, so hooks couldn't see a
command's exit status) and ReadResult errors; deep_loop's handled only message
content; harness_middleware's was the richest. One implementation now, imported
everywhere. Semantics are pinned by the importing modules' tests.
"""

from __future__ import annotations

from typing import Any


def content_to_text(content: Any) -> str:
    """Normalize message content (str or Anthropic-style block list) to text.

    Text blocks are concatenated WITHOUT separators — message content flows
    as one body. Non-str/non-list content falls back to str() (empty for
    None/empty).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return str(content) if content else ""


def result_to_text(result: Any) -> str:
    """Normalize a deepagents tool result (ToolMessage / ExecuteResponse /
    ReadResult / str / block list) to text.

    - ``error`` attr wins first: deepagents' ReadResult carries read failures
      there, and the harness's not-found detection matches on the text.
    - ``content`` (str or block list) then ``output`` (ExecuteResponse) are
      the text carriers.
    - An ``exit_code`` attr (ExecuteResponse) is appended as
      ``[exit_code: N]`` so harness.py's VerifyGate can parse it — without
      this marker a failed test run would be treated as "verified".
    """
    if isinstance(result, str):
        return result
    error = getattr(result, "error", None)
    if isinstance(error, str) and error:
        return error
    text = ""
    content = getattr(result, "content", None)
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    else:
        output = getattr(result, "output", None)
        if isinstance(output, str):
            text = output
        elif result is not None:
            text = str(result)
    exit_code = getattr(result, "exit_code", None)
    if exit_code is not None and "[exit_code:" not in text:
        text = f"{text}\n[exit_code: {exit_code}]"
    return text
