"""Harness as a deepagents AgentMiddleware.

This ports coderio's structural harness (the "wrote code but never verified →
block done" hard constraint) into deepagents' middleware layer. The existing
Harness/HarnessState logic in agent/harness.py is reused unchanged; this module
is the adapter that wires it into deepagents' after_model / wrap_tool_call hooks.

Why this exists: deepagents is a batteries-included harness (planning tool,
filesystem, subagents, context management) but it does NOT enforce verification
before "done" — it trusts the agent. coderio's harness is the one structural
constraint that must survive the migration. As a middleware, it intercepts:
  - wrap_tool_call: observe writes/executions (ground truth) + nudge (PlanGate)
  - after_model:    decide whether the model's "no tool_calls" (want-to-end) is
                    allowed, or force-continue via jump_to='model' (VerifyGate)

Verified feasible via PoC: after_model returning {'jump_to':'model',
'messages':[...]} forces the deepagents loop to continue (the core harness mechanic).
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from coderio.agent.harness import Harness, HarnessState
from coderio.tools.todo import TodoStore

# deepagents tool-name mapping. deepagents uses 'execute' for shell (coderio
# used 'bash'), and write_file/edit_file (no multi_edit). We translate so the
# existing Harness logic works without modification.
_DEEP_VERIFY_TOOL = "execute"
_DEEP_WRITE_TOOLS = frozenset({"write_file", "edit_file"})
# deepagents' planning tool is 'write_todos' (coderio used 'todo'). The
# CompletionGate checks for pending todos — map deepagents' todo tool too.
_DEEP_TODO_TOOL = "write_todos"


def _to_coderio_name(name: str) -> str:
    """Translate a deepagents tool name to the coderio name Harness expects."""
    if name == _DEEP_VERIFY_TOOL:
        return "bash"
    if name == _DEEP_TODO_TOOL:
        return "todo"
    return name


def _result_to_text(result: Any) -> str:
    """Normalize a deepagents tool result (ToolMessage/str/object) to text for
    the harness success/failure heuristic."""
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # list of content blocks
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(result) if result is not None else ""


class HarnessMiddleware(AgentMiddleware):
    """Enforces coderio's verification harness inside a deepagents agent loop.

    Holds a Harness instance (with its own TodoStore). Observes every tool call
    to track writes-since-verify, and intercepts the model's termination to block
    "done" when code was written but never run (escalating: force-continue twice,
    then release with a warning — never silent, never infinite).
    """

    def __init__(self, stream=None, enabled: bool = True) -> None:
        # deepagents manages its own todos via the write_todos tool; we keep a
        # local TodoStore mirror so the CompletionGate has something to read.
        # (In practice the VerifyGate is the critical one; CompletionGate is
        # best-effort here since deepagents' todo model differs from coderio's.)
        self.harness = Harness(state=HarnessState(), todos=TodoStore(), enabled=enabled)
        self.stream = stream

    # ------------------------------------------------------- observe tool calls
    def wrap_tool_call(self, request, handler):
        """Observe every tool execution (ground truth) + apply PlanGate nudge."""
        tc = getattr(request, "tool_call", None) or {}
        name = tc.get("name", "")
        args = dict(tc.get("args", {}) or {})
        # deepagents uses 'file_path' for write/edit tools; coderio's harness
        # expects 'path'. Normalize so observe() records the right path.
        if "file_path" in args and "path" not in args:
            args["path"] = args["file_path"]

        result = handler(request)
        result_text = _result_to_text(result)

        # Feed ground truth to the harness (translate deepagents → coderio names).
        coderio_name = _to_coderio_name(name)
        self.harness.observe(coderio_name, args, result_text)

        # PlanGate: nudge if writing without a todo list (soft, appends to result).
        aug = self.harness.after_tool_call(coderio_name, args, result_text)
        if aug and isinstance(result, str):
            result = result + aug
        elif aug:
            # result is a ToolMessage-like object; append to its content if possible
            try:
                result.content = _result_to_text(result) + aug
            except (AttributeError, TypeError):
                pass
        return result

    # ------------------------------------------------------- intercept termination
    def after_model(self, state, runtime):
        """The model produced output. If it wants to end (no tool_calls) but the
        harness says verification is missing, force the loop to continue.

        Returns a state update dict: {'jump_to':'model','messages':[...]} to
        force-continue, or None to let the agent end normally. On escalation
        release, fires stream.on_harness_warn (if a stream is attached).
        """
        messages = state.get("messages", []) if hasattr(state, "get") else getattr(state, "messages", [])
        last = messages[-1] if messages else None
        # Only intercept when the model returned final text (no tool calls).
        if not isinstance(last, AIMessage) or getattr(last, "tool_calls", None):
            return None

        text = ""
        content = getattr(last, "content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(b.get("text", "") for b in content if isinstance(b, dict))

        cont, inject, warn = self.harness.check_termination(text)
        if cont and inject:
            # The shared harness.py says "use bash" / "call bash"; deepagents' shell
            # tool is named 'execute'. Rewrite so the model calls the right tool.
            inject = inject.replace("call bash", "call execute").replace("use bash", "use execute").replace("with bash", "with execute").replace("Run them with bash", "Run them with execute")
            # Force-continue: inject the harness demand as a user message.
            return {"jump_to": "model", "messages": [HumanMessage(content=inject)]}
        if warn and self.stream is not None and hasattr(self.stream, "on_harness_warn"):
            self.stream.on_harness_warn(warn)
        return None
