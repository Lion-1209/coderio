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

CRITICAL: after_model MUST be decorated with @hook_config(can_jump_to=["model"]).
Without it, langchain's factory sees can_jump_to=[] and does NOT build the
conditional edge for this middleware, so jump_to='model' silently does nothing —
the agent ends despite the harness wanting to force-continue. This was a latent
bug in the original experimental version (verified against langchain factory.py
_get_can_jump_to, which reads method.__can_jump_to__).
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, hook_config
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
    the harness success/failure heuristic.

    For ExecuteResponse (deepagents shell results), appends the exit_code as
    ``[exit_code: N]`` so the harness's VerifyGate can parse it. Without this,
    a failed test run (exit != 0) would be treated as "verified" because the
    raw output text doesn't contain the exit code marker.
    """
    if isinstance(result, str):
        return result
    # deepagents ReadResult has an `error` attr for failed reads, and `file_data`
    # for successful ones. Extract the error so the harness's not-found detection
    # (harness.py: "not found" in result) works.
    error = getattr(result, "error", None)
    if isinstance(error, str) and error:
        return error
    # Build the text from content/output fields.
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
    # Append exit_code marker if the result has one (ExecuteResponse). This
    # lets _parse_exit_code in harness.py extract it for VerifyGate.
    exit_code = getattr(result, "exit_code", None)
    if exit_code is not None and "[exit_code:" not in text:
        text = f"{text}\n[exit_code: {exit_code}]"
    return text


class HarnessMiddleware(AgentMiddleware):
    """Enforces coderio's verification harness inside a deepagents agent loop.

    Holds a Harness instance (with its own TodoStore). Observes every tool call
    to track writes-since-verify, and intercepts the model's termination to block
    "done" when code was written but never run (escalating: force-continue twice,
    then release with a warning — never silent, never infinite).

    Harness signals (force-continue / escalation-warn) are emitted via
    runtime.stream_writer so the TUI's StreamHandler can show them. This is the
    'custom' stream channel — distinct from 'messages' (token stream) and
    'updates' (complete messages).
    """

    def __init__(self, stream=None, enabled: bool = True) -> None:
        self.harness = Harness(state=HarnessState(), todos=TodoStore(), enabled=enabled)
        self.stream = stream
        self._runtime = None  # captured in wrap_tool_call / after_model

    def _emit(self, runtime: Any, payload: dict) -> None:
        """Send a custom stream event (harness_continue / harness_warn).

        Uses runtime.stream_writer so the stream consumer (deep_loop.py) picks
        it up via stream_mode='custom'. Falls back to the legacy stream callback
        if no runtime (e.g. in unit tests without a real graph).
        """
        try:
            if runtime is not None and hasattr(runtime, "stream_writer"):
                runtime.stream_writer(payload)
                return
        except Exception:  # noqa: S110 — stream_writer may not be available in tests; fall back
            pass
        # Fallback: direct stream callback (for tests / non-graph contexts).
        if self.stream is not None:
            t = payload.get("type")
            if t == "harness_continue" and hasattr(self.stream, "on_harness_continue"):
                self.stream.on_harness_continue(payload.get("reason", ""))
            elif t == "harness_warn" and hasattr(self.stream, "on_harness_warn"):
                self.stream.on_harness_warn(payload.get("message", ""))

    # ------------------------------------------------------- observe tool calls
    def wrap_tool_call(self, request, handler):
        """Observe every tool execution (ground truth) + apply PlanGate nudge.

        Also captures the runtime reference for after_model's stream_writer use.
        """
        self._runtime = getattr(request, "runtime", None) or self._runtime
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

        # Sync deepagents' write_todos into the harness's TodoStore so
        # CompletionGate can check for pending todos. Only sync when the tool
        # actually succeeded — a failed write_todos (Error result) means the
        # graph state wasn't updated, so syncing args would create a mismatch.
        if name == "write_todos" and "todos" in args and not result_text.startswith("Error"):
            from coderio.tools.todo import Todo

            todos_data = args["todos"]
            if isinstance(todos_data, list):
                self.harness.todos.todos = [
                    Todo(content=t.get("content", ""), status=t.get("status", "pending"))
                    for t in todos_data
                    if isinstance(t, dict)
                ]

        # Subagent delegation: the task tool returns a subagent's findings,
        # which may cite files the subagent read but the MAIN agent didn't.
        # Without this, GroundingGate would flag those citations as ungrounded
        # and force-continue — turning a complete analysis into a "correction".
        # We extract file paths from the subagent's result and add them to the
        # main agent's read set, so the gate treats them as "read via subagent".
        if name == "task" and result_text:
            from coderio.agent.harness import _cited_files, _norm_path

            for cited in _cited_files(result_text):
                # Strip :line suffix, normalize.
                clean = cited.rsplit(":", 1)[0] if ":" in cited else cited
                self.harness.state.content_read_files.add(_norm_path(clean))

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
    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        """The model produced output. If it wants to end (no tool_calls) but the
        harness says verification is missing, force the loop to continue.

        Returns a state update dict: {'jump_to':'model','messages':[...]} to
        force-continue, or None to let the agent end normally. On escalation
        release, emits a harness_warn signal via stream_writer.

        The @hook_config(can_jump_to=["model"]) decorator is REQUIRED — without
        it, langchain's factory doesn't build the conditional edge and jump_to
        silently fails (the agent ends anyway).
        """
        self._runtime = runtime or self._runtime
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
            inject = (
                inject.replace("call bash", "call execute")
                .replace("use bash", "use execute")
                .replace("with bash", "with execute")
                .replace("Run them with bash", "Run them with execute")
            )
            # Emit a visible signal so the TUI explains why the agent keeps running.
            self._emit(runtime, {"type": "harness_continue", "reason": inject})
            # Force-continue: inject the harness demand as a user message.
            return {"jump_to": "model", "messages": [HumanMessage(content=inject)]}
        if warn:
            self._emit(runtime, {"type": "harness_warn", "message": warn})
        return None
