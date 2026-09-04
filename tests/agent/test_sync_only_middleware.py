"""Tests for the explicit sync-only middleware contract (audit P1-15).

The contract: production middlewares define ONLY sync hooks. langchain's
generic NotImplementedError on the async surface is the intended behavior —
the sync engine never calls it. What must be guarded:

1. every production middleware inherits SyncOnlyMiddleware (declared, not
   accidental);
2. NONE of them defines a custom async surface — langgraph wires after_model
   differently when aafter_model exists, and an overriding raise broke even
   the sync path (release-gate round, 2026-09-04). If someone adds an async
   variant, this test forces that to be a deliberate, tested decision.
"""

from langchain.agents.middleware import AgentMiddleware

from coderio.agent.command_review import CommandReviewMiddleware
from coderio.agent.harness_middleware import HarnessMiddleware
from coderio.agent.hooks import HookRunner, HooksMiddleware
from coderio.agent.permission_middleware import PermissionMiddleware
from coderio.agent.sync_only import SyncOnlyMiddleware
from coderio.tools.command_policy import CommandPolicy
from coderio.tools.permission import AutoPermissionGate


def _middleware_instances():
    """Fresh middleware instances per call — construction stays out of
    collection, and constructors keep their real requirements."""
    return [
        HarnessMiddleware(),
        HooksMiddleware(HookRunner(specs=[], project_dir=".")),
        PermissionMiddleware(AutoPermissionGate()),
        CommandReviewMiddleware(CommandPolicy()),
    ]


def test_production_middlewares_declare_sync_only_base():
    for mw in _middleware_instances():
        assert isinstance(mw, SyncOnlyMiddleware), f"{type(mw).__name__} must inherit SyncOnlyMiddleware"


def test_no_production_middleware_defines_a_custom_async_surface():
    """The async methods must be langchain's own defaults. Overriding
    aafter_model changes how langgraph builds the node and breaks the SYNC
    path — so any async definition here must come with a real async
    implementation and this test's conscious update."""
    for mw in _middleware_instances():
        for hook in ("awrap_tool_call", "aafter_model"):
            own = getattr(type(mw), hook, None)
            base = getattr(AgentMiddleware, hook, None)
            assert own is base, (
                f"{type(mw).__name__} defines its own {hook} — that changes langgraph node "
                "wiring (breaks even sync paths). Implement a real async surface and "
                "update this contract test, or drop the override."
            )


def test_sync_only_base_adds_no_behavior():
    """The marker base must not quietly grow behavior — its async hooks stay
    the inherited defaults and it defines no sync hooks of its own."""
    assert SyncOnlyMiddleware.awrap_tool_call is AgentMiddleware.awrap_tool_call
    assert SyncOnlyMiddleware.aafter_model is AgentMiddleware.aafter_model
    for name in ("wrap_tool_call", "after_model"):
        assert name not in SyncOnlyMiddleware.__dict__, f"marker base must not define {name}"
