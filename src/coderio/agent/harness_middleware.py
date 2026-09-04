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
bug in the original version (verified against langchain factory.py
_get_can_jump_to, which reads method.__can_jump_to__).
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage

from coderio.agent._deepagents_compat import get_state_todos
from coderio.agent.harness import Harness, HarnessState

# Engine↔harness tool-name mapping. Single source of truth:
# tools/taxonomy.py (2026-08-28 audit A2 — six ad-hoc copies drifted apart).
from coderio.tools.taxonomy import to_harness_name as _to_harness_name
from coderio.tools.taxonomy import translate_bash_prose as _translate_bash_prose
from coderio.tools.todo import TodoStore


def _stream_supports_phase(stream: Any) -> bool:
    """Does this stream consumer actually display phase changes?

    Avoids paying AgentStateTracker overhead (and polluting the session jsonl
    timeline) when no one is watching: NullStream (headless tests) and ad-hoc
    stubs without on_phase_change return False. The real TUI StreamHandler and
    the live-verify PrintStream opt in by defining on_phase_change.
    """
    return stream is not None and hasattr(stream, "on_phase_change")


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

    def __init__(
        self,
        stream=None,
        enabled: bool = True,
        todos: "TodoStore | None" = None,
        plan_artifact=None,
        permission_gate=None,
    ) -> None:
        # Wire the phase-observation tracker when a stream consumer is present.
        # The display pipeline (TUI StatusBar / stream.on_phase_change) already
        # exists; without a tracker, Harness._track_phase is a no-op and the
        # status bar's phase slot stays empty. We instantiate the tracker
        # whenever the stream declares on_phase_change support — that's the
        # signal a real UI is listening (NullStream and test stubs don't).
        from coderio.agent.state import AgentStateTracker

        tracker = AgentStateTracker() if _stream_supports_phase(stream) else None
        self.harness = Harness(
            state=HarnessState(),
            todos=todos if todos is not None else TodoStore(),
            enabled=enabled,
            state_tracker=tracker,
            stream=stream,
        )
        self.stream = stream
        # Optional plan-artifact mirror (.coderio/plan.md). The MAIN agent gets
        # one from deep_loop; subagents deliberately don't — the plan has one
        # owner. When provided, every successful write_todos materializes it —
        # EXCEPT in PLAN mode (see _plan_mode_blocks_writes).
        self.plan_artifact = plan_artifact
        # Optional permission gate reference; PLAN mode suppresses the
        # plan.md disk mirror (audit 2026-09-04 P1-10).
        self._permission_gate = permission_gate
        self._runtime = None  # captured in wrap_tool_call / after_model

    def _plan_mode_blocks_writes(self) -> bool:
        """PLAN mode is documented as read-only ("blocks ALL writes"). The
        write_todos → plan.md mirror is a DISK WRITE that used to slip past
        that contract: any todo content could land in
        <project>/.coderio/plan.md — fixed path, arbitrary content, no
        permission gate, outside /undo. In PLAN mode the mirror is
        suppressed; todos still work in memory (audit 2026-09-04 P1-10).
        """
        gate = self._permission_gate
        return gate is not None and getattr(gate, "mode", "") == "plan"

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
        coderio_name = _to_harness_name(name)
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
                # Plan artifact: mirror the fresh task list to
                # .coderio/plan.md so the user can view/edit it between turns.
                # Suppressed under a PLAN gate — PLAN is read-only by contract
                # and must not write the project file (audit 2026-09-04 P1-10).
                if self.plan_artifact is not None and not self._plan_mode_blocks_writes():
                    self.plan_artifact.materialize()
                    # The model re-authored the plan — its version supersedes
                    # any turn-start adoption; drop that pending signal so
                    # after_model's state sync runs normally.
                    self.plan_artifact.clear_adoption()

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

        # PlanGate: nudge if writing without a todo list (soft, appends to
        # result). Suppressed in PLAN mode: the nudge advertises the plan.md
        # mirror, which PLAN suppresses (read-only contract, audit P1-10) —
        # advertising it there would point the model at a write that cannot
        # happen (third-party adversarial review note, 2026-09-04).
        if not self._plan_mode_blocks_writes():
            aug = self.harness.after_tool_call(coderio_name, args, result_text)
        else:
            aug = None
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

        # Sync todos from graph state before checking termination. This covers
        # the case where write_todos was called in a PREVIOUS turn (restored
        # from checkpoint) but the HarnessMiddleware's TodoStore was reset (it's
        # recreated each run_deep_agent call). Without this, CompletionGate
        # would see an empty todo list even though graph state has pending todos.
        state_todos = get_state_todos(state)
        todos_update: dict | None = None
        if state_todos and isinstance(state_todos, list):
            if self.plan_artifact is not None and self.plan_artifact.consume_adoption():
                # The user's plan.md edit was adopted at turn start, but the
                # checkpointed graph state still holds the PRE-edit todos
                # (TodoListMiddleware is back in the stack, so state todos are
                # non-empty again). The sync below would clobber the adoption
                # and the gates would keep judging the plan the user just
                # changed (2026-08-27 adversarial review Y2). plan.md is the
                # user-facing authority — push the ADOPTED plan into state.
                todos_update = {"todos": [{"content": t.content, "status": t.status} for t in self.harness.todos.todos]}
            else:
                from coderio.tools.todo import Todo

                self.harness.todos.todos = [
                    Todo(content=t.get("content", ""), status=t.get("status", "pending"))
                    for t in state_todos
                    if isinstance(t, dict)
                ]

        text = ""
        content = getattr(last, "content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(b.get("text", "") for b in content if isinstance(b, dict))

        cont, inject, warn = self.harness.check_termination(text)
        if cont and inject:
            # The shared harness.py writes tool-name-agnostic prose that happens
            # to say "bash" (its original engine's name for the shell tool).
            # deepagents' shell tool is named 'execute'. Translate the standalone
            # word "bash" → "execute" so the model calls the right tool.
            # Word-boundary regex (not literal phrase replace) is robust to
            # harness.py prose changes: "use bash", "call bash now", "run with
            # bash" all map correctly without a per-phrase .replace() chain.
            inject = _translate_bash_prose(inject)
            # Emit a visible signal so the TUI explains why the agent keeps running.
            self._emit(runtime, {"type": "harness_continue", "reason": inject})
            # Force-continue: inject the harness demand as a user message.
            # todos_update (adoption push-back, see above) rides along so the
            # state and the store agree even on a force-continued turn.
            if todos_update:
                return {"jump_to": "model", "messages": [HumanMessage(content=inject)], **todos_update}
            return {"jump_to": "model", "messages": [HumanMessage(content=inject)]}
        if warn:
            self._emit(runtime, {"type": "harness_warn", "message": warn})
        # Fire the final phase transition (COMPLETE) when the turn is truly
        # ending — either a clean finish or an escalation release. The TUI's
        # on_phase_change clears the status bar's phase slot on 'complete'.
        if self.harness.state_tracker is not None:
            self.harness.state_tracker.finish(hint="turn end")
            if self.stream is not None and hasattr(self.stream, "on_phase_change"):
                self.stream.on_phase_change("complete", 0, "turn end")
        # Plain state update (no jump_to) — normal termination, just carrying
        # the adopted todos into state when one is pending.
        return todos_update
