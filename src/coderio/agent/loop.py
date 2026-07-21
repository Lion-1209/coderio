from __future__ import annotations

from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from coderio.agent.prompts import ActiveSkills, build_system_prompt
from coderio.agent.skill_tool import ActivateSkillTool, DeactivateSkillTool
from coderio.agent.stream import NullStream
from coderio.session import Message, ToolCall
from coderio.session.store import Session
from coderio.skills.store import SkillStore
from coderio.skills.triggers import detect_stage, stage_skill
from coderio.tools import to_langchain_tools
from coderio.tools.permission import PermissionGate

# Approximate chars-per-token for the active-skill budget guard (spec §2.4).
# 30% of a 128k context ~ 38k tokens ~ ~150k chars. We warn on the body char total.
_BUDGET_WARN_CHARS = 150_000


def _content_to_text(content) -> str:
    """Normalize an AIMessage.content (str OR list of Anthropic content blocks) to text.

    Anthropic-style models return content as a list of blocks like
    [{"type": "text", "text": "..."}] (and possibly "thinking" blocks); other
    providers return a plain string. The loop must handle both.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    parts.append(block["text"])
                # ignore non-text blocks (thinking/tool_use handled via tool_calls)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content) if content else ""


def _extract_thinking(content) -> str:
    """Extract 'thinking' block text from Anthropic-style content (for the UI
    'is thinking' indicator). Returns '' if no thinking blocks."""
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            parts.append(block.get("thinking", "") or block.get("text", ""))
    return "".join(parts)


def _to_langchain_messages(system_prompt: str, convo: list[Message]) -> list:
    """Convert our Message list to langchain message objects (spec §4.2)."""
    msgs: list = [SystemMessage(content=system_prompt)]
    for m in convo:
        if m.role == "user":
            msgs.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            tcs = None
            if m.tool_calls:
                tcs = [
                    {"name": tc.name, "args": tc.args, "id": tc.id, "type": "tool_call"}
                    for tc in m.tool_calls
                ]
            msgs.append(AIMessage(content=m.content, tool_calls=tcs or []))
        elif m.role == "tool":
            msgs.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id or ""))
    return msgs


def _build_skill_index(tools, activate: ActivateSkillTool) -> dict[str, Any]:
    """Map tool name -> coderio tool object, for execution by the harness."""
    idx = {t.name: t for t in tools}
    idx[activate.name] = activate
    return idx


def _invoke_tool(tool, name: str, args: dict) -> str:
    """Run a tool and turn ANY execution failure into a tool-result string.

    Tool errors (bad args, file not found, non-zero exit, syntax errors) are NOT
    fatal for an agent — they are signal the model should react to and self-correct
    (e.g. drop the bad `path` kwarg and retry). Only base-model API errors
    (auth/network/rate-limit, which surface from run_step, not here) are truly
    fatal. So we catch everything from tool.run and return it as a normal result.

    Error CLASSIFICATION gives the model an actionable signal rather than a flat
    "failed": it distinguishes retryable (fix the call and try again) from
    non-retryable (this tool won't work for this task — switch approach). This lets
    the model stop hammering a tool that will never succeed (e.g. PermissionError
    on a protected path is not going away by rephrasing args).
    """
    try:
        return tool.run(**args)
    except TypeError as e:
        # Most common case: the model passed an arg the tool doesn't accept
        # (e.g. bash(path=...) when bash takes cwd). Retryable — fix the call.
        return (f"[retryable] tool {name!r} rejected the arguments ({e}). "
                f"Check the tool's accepted parameters and retry with valid args.")
    except (PermissionError, FileNotFoundError, IsADirectoryError, NotADirectoryError) as e:
        # Non-retryable: the environment refuses this operation. Re-trying with
        # tweaked args won't help — the model should pick another path or method.
        return (f"[non-retryable] tool {name!r} cannot proceed: "
                f"{type(e).__name__}: {e}. This is an environmental constraint — "
                f"do not repeat the same call; choose a different path or approach.")
    except Exception as e:  # noqa: BLE001 — tool failures must not kill the turn
        # Unknown failure — conservatively retryable. Surface the type so the
        # model can reason about it (a ValueError vs OSError mean different things).
        return f"[retryable] tool {name!r} failed: {type(e).__name__}: {e}"


class _BoundModelCache:
    """Binds langchain tools to the model once per (system prompt, active skills) signature.

    Re-binds when a skill is activated (changes tool set / prompt). Avoids rebinding
    every round when nothing changed.
    """

    def __init__(self, model) -> None:
        self.model = model
        self._signature = None
        self._bound = None

    def get(self, langchain_tools: list, signature: tuple) -> Any:
        if self._signature != signature or self._bound is None:
            self._bound = self.model.bind_tools(langchain_tools) if langchain_tools else self.model
            self._signature = signature
        return self._bound


def run_step(
    bound_cache: _BoundModelCache,
    langchain_tools: list,
    system_prompt: str,
    convo: list[Message],
    stream,
) -> AIMessage:
    """Execute a single ReAct step: call the model (streamed) and return its AIMessage.

    This is the S2 crewAI reuse seam (spec §4.5): one model call -> one assistant turn.
    Streaming text is forwarded token-by-token; tool_call chunks are aggregated
    (via AIMessageChunk __add__) so the returned AIMessage always carries complete
    tool_calls regardless of provider streaming style.
    """
    bound = bound_cache.get(langchain_tools, (system_prompt,))
    lc_msgs = _to_langchain_messages(system_prompt, convo)
    aggregated = None  # AIMessageChunk accumulator; its tool_calls are complete at end.
    try:
        for event in bound.stream(lc_msgs):
            raw = getattr(event, "content", "")
            token = _content_to_text(raw)
            if token:
                stream.on_token(token)
            thinking = _extract_thinking(raw)
            if thinking and hasattr(stream, "on_thinking"):
                stream.on_thinking(thinking)
            aggregated = event if aggregated is None else (aggregated + event)
    except Exception as e:
        # Map common provider errors to actionable user-facing messages instead
        # of letting a raw traceback surface. The error string is fed back to
        # the TUI's error handler, which displays it in the history pane.
        msg = str(e).lower()
        if any(k in msg for k in ("authentication", "401", "api key", "unauthorized")):
            raise RuntimeError(
                "API 认证失败：API key 无效或已过期。请运行 coderio config 检查配置，"
                "或设置 ANTHROPIC_API_KEY 环境变量。") from e
        if any(k in msg for k in ("rate_limit", "429", "rate limit", "quota")):
            raise RuntimeError(
                "API 速率限制：请求过于频繁或额度用完（SDK 已自动重试 3 次仍失败）。"
                "请稍后重试，或检查账户额度。") from e
        if any(k in msg for k in ("connect", "timeout", "unreachable", "dns", "refused")):
            raise RuntimeError(
                f"网络错误：无法连接到模型端点 ({type(e).__name__})，"
                "已自动重试 3 次。请检查网络连接和 base_url 配置。") from e
        if any(k in msg for k in ("not found", "404", "model")):
            raise RuntimeError(
                f"模型错误：模型名可能无效 ({type(e).__name__}: {e})。"
                "请运行 coderio config 检查 model 配置。") from e
        raise  # unknown error — let the TUI's generic handler surface it
    usage = getattr(aggregated, "usage_metadata", None) or {}
    if usage and hasattr(stream, "add_usage"):
        stream.add_usage(usage)
    # Detect truncation: a non-end_turn stop reason means output was cut off
    # (e.g. max_tokens) — tell the user instead of silently producing partial work.
    meta = getattr(aggregated, "response_metadata", {}) or {}
    stop_reason = meta.get("stop_reason")
    if stop_reason and stop_reason not in ("end_turn", "stop", "tool_use", None) \
            and hasattr(stream, "on_truncated"):
        stream.on_truncated(stop_reason)
    if aggregated is not None and getattr(aggregated, "tool_calls", None):
        return AIMessage(content=_content_to_text(getattr(aggregated, "content", "")),
                         tool_calls=list(aggregated.tool_calls),
                         usage_metadata=usage or None)
    text = _content_to_text(getattr(aggregated, "content", "")) if aggregated is not None else ""
    return AIMessage(content=text, tool_calls=[])


def _execute_turn(
    model,
    bound_cache: _BoundModelCache,
    langchain_tools: list,
    system_prompt: str,
    convo: list,
    skill_index: dict,
    gate: PermissionGate,
    stream,
    max_rounds: int,
    on_message=None,
    on_activate_skill=None,
    harness=None,
) -> str:
    """Run one agent turn: loop run_step + tool execution until final text or max_rounds.

    Shared by run_agent (S0) and CrewOrchestrator (S2). `convo` is the message list
    fed to the model (appended in place). `on_message(msg)` is called for EVERY
    message produced (assistant + tool results) so callers can persist them
    (run_agent writes to the session jsonl; S2 just uses convo). Returns the final
    assistant text.

    `harness` (Harness | None): when provided and enabled, the harness layer exerts
    structural control over termination (VerifyGate/CompletionGate) and augments
    tool results (PlanGate). Pass None to keep the original behavior unchanged
    (this is what the S2 crew does — it has its own verify→fix loop).
    """
    def _emit(msg):
        convo.append(msg)
        if on_message is not None:
            on_message(msg)

    active_prompt = system_prompt
    for step_num in range(1, max_rounds + 1):
        # Signal the UI to start its busy indicator BEFORE the model call. The
        # step number lets the UI show progress ("步骤 2") so the user knows the
        # agent is iterating, not frozen.
        if hasattr(stream, "on_step_start"):
            stream.on_step_start(step_num)
        ai = run_step(bound_cache, langchain_tools, active_prompt, convo, stream)
        tool_calls = list(getattr(ai, "tool_calls", []) or [])
        if not tool_calls:
            text = _content_to_text(getattr(ai, "content", ""))
            # Empty response: the model returned NO text and NO tool_calls. This
            # is a real failure mode (seen with step-3.7-flash: after several
            # rounds of read_file tool_calls it returned a bare empty message).
            # Treating it as "done" makes the turn end silently — the user sees
            # the screen freeze with no output and no explanation. Instead, emit a
            # visible notice (persisted to session + shown in UI) so the user knows
            # the model produced nothing (vs. the UI being broken).
            if not text.strip():
                notice = ("(模型返回了空响应 — 既无文本也无工具调用。可能是上下文过长或模型提前结束。"
                          "请重试，或用 /clear 清理上下文后继续。)")
                stream.on_tool_start("_empty_response", {})
                stream.on_tool_end("_empty_response", notice)
                _emit(Message.tool_result(tool_call_id="_empty", name="_empty_response", content=notice))
            # --- Harness termination control (spec §3) ---
            # The model wants to stop, but the harness decides whether that's allowed.
            # Based on observed tool calls (ground truth), not the model's self-report.
            if harness is not None and getattr(harness, "enabled", False):
                cont, inject, warn = harness.check_termination(text)
                if cont and inject is not None:
                    # Force-continue: do NOT return. Inject the harness demand as a
                    # user message (the model sees a hard follow-up requirement) and
                    # keep looping. Persisted via on_message so it's auditable.
                    msg = Message.user(inject)
                    convo.append(msg)
                    if on_message is not None:
                        on_message(msg)
                    continue
                if warn:
                    stream.on_harness_warn(warn)
            _emit(Message.assistant(text))
            stream.on_finish()
            return text
        _emit(Message.assistant(
            _content_to_text(getattr(ai, "content", "")),
            tool_calls=[ToolCall(id=tc["id"], name=tc["name"], args=dict(tc.get("args", {})))
                        for tc in tool_calls],
        ))
        for tool_index, tc in enumerate(tool_calls):
            name = tc["name"]
            args = dict(tc.get("args", {}))
            stream.on_tool_start(name, args, step=step_num, tool_index=tool_index, tool_total=len(tool_calls))
            if not gate.check(name, args):
                result = f"Permission denied: tool {name!r} blocked in {gate.mode} mode."
            else:
                tool = skill_index.get(name)
                result = _invoke_tool(tool, name, args) if tool else f"Error: unknown tool {name!r}"
            # --- Harness observation + PlanGate augmentation (spec §3) ---
            # observe() records ground truth (writes / verifications) for the gates;
            # after_tool_call() may append a soft nudge to the result (never blocks).
            if harness is not None and getattr(harness, "enabled", False):
                harness.observe(name, args, result)
                aug = harness.after_tool_call(name, args, result)
                if aug:
                    result = aug
            stream.on_tool_end(name, result)
            _emit(Message.tool_result(tool_call_id=tc["id"], name=name, content=result))
            # Both activate_skill and deactivate_skill change which skill bodies
            # are in the system prompt — refresh it so the next round sees the
            # updated active set.
            if name in ("activate_skill", "deactivate_skill") and on_activate_skill is not None:
                new_prompt = on_activate_skill()
                if new_prompt:
                    active_prompt = new_prompt
    out = f"Stopped: reached max rounds ({max_rounds})."
    _emit(Message.assistant(out))
    stream.on_finish()
    return out


def run_agent(
    user_input: str,
    model,
    tools: list,
    gate: PermissionGate,
    skill_store: SkillStore,
    active_skills: ActiveSkills,
    session: Session,
    stream=None,
    max_rounds: int = 25,
    stage_auto_inject: bool = True,
    harness_enabled: bool = True,
) -> str:
    """Run the ReAct loop until the model returns final text or max_rounds hit.

    Returns the final assistant text (also persisted to session).
    `stage_auto_inject`: when False, skip harness-side stage detection (spec §2.2 switch).
    `harness_enabled`: when True (default), the structural harness layer is active —
    it blocks "done" on unverified writes (hard, escalating) and nudges a plan before
    writing (soft). Pass False to disable (original soft-rule behavior). See
    agent/harness.py.
    """
    stream = stream or NullStream()
    activate_tool = ActivateSkillTool(skill_store, active_skills)
    deactivate_tool = DeactivateSkillTool(active_skills)
    skill_index = _build_skill_index(tools, activate_tool)
    skill_index[deactivate_tool.name] = deactivate_tool

    # Stage auto-inject uses the TEXT part of the input (user_input may be a
    # multimodal content-block list; detect_stage needs a plain string).
    _input_text = user_input if isinstance(user_input, str) else " ".join(
        b.get("text", "") for b in user_input if isinstance(b, dict) and b.get("type") == "text"
    )
    if stage_auto_inject:
        if stage := detect_stage(_input_text):
            skill_name = stage_skill(stage)
            if skill_name and skill_store.has(skill_name) and not active_skills.is_active(skill_name):
                active_skills.activate(skill_store.get(skill_name))

    system_prompt = build_system_prompt(skill_store, active_skills)
    session.append(Message.user(user_input))
    bound_cache = _BoundModelCache(model)

    # Active-skill token-budget warning (spec §2.4): warn (don't force) at 30%.
    body_chars = sum(len(s.body) for s in active_skills.all())
    if body_chars > _BUDGET_WARN_CHARS:
        stream.on_tool_end(
            "_budget",
            f"Warning: active skill bodies total ~{body_chars} chars (>30% budget); "
            "consider deactivate_skill to free context.",
        )

    langchain_tools = to_langchain_tools(
        tools,
        extra={
            activate_tool.name: activate_tool.args_schema,
            deactivate_tool.name: deactivate_tool.args_schema,
        },
    )
    from coderio.tools.base import to_langchain_tool as _adapt
    langchain_tools = langchain_tools + [
        _adapt(activate_tool, activate_tool.args_schema),
        _adapt(deactivate_tool, deactivate_tool.args_schema),
    ]

    def _refresh_prompt():
        return build_system_prompt(skill_store, active_skills)

    # Build the structural harness. It reads the SAME TodoStore the todo tool
    # writes to (find it in the tools list) so the gates see live todo state.
    harness = None
    if harness_enabled:
        from coderio.agent.harness import Harness, HarnessState
        todo_store = next((t.store for t in tools if getattr(t, "name", "") == "todo"), None)
        # If no TodoStore is reachable, the gates degrade gracefully (todos empty
        # -> plan gate always nudges, completion gate always skips). Build anyway.
        from coderio.tools.todo import TodoStore as _TS
        harness = Harness(state=HarnessState(), todos=todo_store or _TS())

    # convo feeds the model; on_message persists every produced message to the session.
    convo = list(session.messages)
    return _execute_turn(
        model=model, bound_cache=bound_cache, langchain_tools=langchain_tools,
        system_prompt=system_prompt, convo=convo, skill_index=skill_index,
        gate=gate, stream=stream, max_rounds=max_rounds,
        on_message=session.append, on_activate_skill=_refresh_prompt,
        harness=harness,
    )
