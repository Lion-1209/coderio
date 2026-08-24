"""Custom subagents: ``.coderio/agents/*.md`` (project) + ``~/.coderio/agents/*.md`` (user).

A file's filename (minus .md) is the subagent NAME used via
``task(subagent_type="<name>")``; optional frontmatter ``description`` tells
the MAIN model when to delegate; the body is the subagent's SYSTEM PROMPT.

SECURITY MODEL — this is persona customization only, never tool escalation.
Every discovered agent is assembled onto the SAME read-only middleware stack
as the built-in research subagent (hooks + PermissionMiddleware(PLAN) +
CommandReviewMiddleware): it can read and search, never write or execute,
regardless of what its prompt says or which permission mode the caller runs
in. A hostile repo can therefore at worst add a misleading persona/prompt —
the same trust surface as SKILL.md bodies.

Names colliding with built-in subagent types ("research", "general-purpose")
are dropped at discovery — exact AND case variants — so a repo file can't
hijack or spoof a trusted type (same near-miss logic as custom commands).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coderio.skills.parser import sanitize_description, split_frontmatter

# Subagent types coderio itself provides. Discovered files with these names
# (any casing) are ignored — task(subagent_type=...) is matched by deepagents
# verbatim, so an exact-collision file would shadow trusted behavior, and on
# Windows's case-insensitive FS a case variant is reachable while looking like
# the trusted type in transcripts.
RESERVED_AGENT_NAMES = frozenset({"research", "general-purpose"})
RESERVED_CI = frozenset(n.lower() for n in RESERVED_AGENT_NAMES)

MAX_AGENT_BYTES = 128 * 1024  # system prompts are small; bigger = junk/bomb


@dataclass(frozen=True)
class CustomAgent:
    """One discovered custom subagent definition."""

    name: str  # task(subagent_type=...) value, without leading slash
    description: str  # shown to the MAIN model for delegation decisions
    system_prompt: str  # the subagent's persona/instructions
    source_layer: str  # "user" | "project" — for /help-style listings


def _load_layer(layer_dir: Path | None, layer_name: str) -> dict[str, CustomAgent]:
    out: dict[str, CustomAgent] = {}
    if not layer_dir:
        return out
    layer_dir = Path(layer_dir)
    if not layer_dir.is_dir():
        return out
    for md in sorted(layer_dir.glob("*.md")):
        # Name from FILENAME (not frontmatter) — mirrors custom commands: the
        # tree the user sees must match the string they pass to task().
        name = md.stem
        if not name or any(ch.isspace() for ch in name):
            continue  # task() takes a bare token; spaced names are unreachable
        try:
            if md.stat().st_size > MAX_AGENT_BYTES:
                continue
            raw = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        raw = raw.replace("\x00", "")  # providers 400 on NUL
        fm, body = split_frontmatter(raw)
        body = body.strip()
        if not body:
            continue  # empty persona would send the subagent in blind
        out[name] = CustomAgent(
            name=name,
            description=sanitize_description(fm.get("description", "")),
            system_prompt=body,
            source_layer=layer_name,
        )
    return out


def discover_custom_agents(
    project_dir: Path | str | None = None,
    user_dir: Path | str | None = None,
) -> dict[str, CustomAgent]:
    """Discover custom subagent definitions from both layers.

    Both params are LAYER DIRECTORIES whose immediate ``*.md`` children define
    agents (e.g. ``<project>/.coderio/agents``) — NOT a project root; callers
    join the anchor themselves (same convention as load_skill_store). Project
    layer overrides user layer on conflict. Reserved names are dropped.
    """
    merged = _load_layer(Path(user_dir) if user_dir else None, "user")
    merged.update(_load_layer(Path(project_dir) if project_dir else None, "project"))
    return {k: v for k, v in merged.items() if k.lower() not in RESERVED_CI and k not in RESERVED_AGENT_NAMES}
