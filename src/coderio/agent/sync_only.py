"""Explicit sync-only contract for coderio's agent middleware (audit P1-15).

All production middlewares drive REAL blocking work — subprocess hooks,
stdin/interactive permission prompts, filesystem writes — and are implemented
ONLY as sync hooks (``wrap_tool_call`` / ``after_model``).

Why this is a marker base and NOT an async override: langgraph builds the
after_model node differently once a middleware defines ``aafter_model``
(release-gate round, 2026-09-04: an overriding raise broke even the SYNC path
with "No synchronous function provided to aafter_model"). The async surface
must therefore stay UNDEFINED; ``test_sync_only_middleware.py`` machine-checks
that. If the engine ever gains a real async path, the fix is proper async
middleware here — a deliberate, tested change, never a silent one.
"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware


class SyncOnlyMiddleware(AgentMiddleware):
    """Marker base: declares "no async surface, on purpose".

    Inherits ``AgentMiddleware`` unchanged. Its async methods are langchain's
    own defaults (which raise a generic ``NotImplementedError`` only when an
    async engine actually calls them — the sync engine never does). The
    contract test pins that no production middleware narrows or widens this.
    """
