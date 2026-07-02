from __future__ import annotations

import re

STAGE_SKILL_MAP: dict[str, str] = {
    "implement": "executing-plans",
    "commit": "commit-message",
}

_PATTERNS: dict[str, list[re.Pattern]] = {
    "implement": [
        re.compile(r"\b(start|begin)\s+implement(ing)?\b", re.IGNORECASE),
        re.compile("开始实现|开始执行|开跑"),
    ],
    "commit": [
        re.compile(r"\bcommit\b", re.IGNORECASE),
        re.compile("提交(一下|吧)?"),
    ],
}


def detect_stage(user_input: str) -> str | None:
    """Return stage key if user input signals a stage transition, else None.

    This is the harness-side stage auto-inject (spec §2.3 (2)), NOT keyword skill
    matching (3) - it only detects a small set of stage transitions.
    """
    for stage, patterns in _PATTERNS.items():
        for p in patterns:
            if p.search(user_input):
                return stage
    return None


def stage_skill(stage: str | None) -> str | None:
    return STAGE_SKILL_MAP.get(stage)
