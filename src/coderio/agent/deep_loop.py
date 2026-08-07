"""deepagents-backed production agent engine for coderio.

deepagents provides context management (offload + summarization), a
filesystem/shell backend, subagents, and task planning.

What coderio ADDS on top via middleware:
  - HarnessMiddleware: the "wrote code but never verified → block done" hard
    constraint (4 gates). deepagents trusts the agent; coderio does not.
  - PermissionMiddleware: 4-tier access system (plan/confirm/auto_edit/full).

This module is the sole production engine, invoked by the TUI.

Streaming: uses THREE stream modes in parallel —
  - 'messages': AIMessageChunk → on_token / on_thinking (token-by-token stream)
  - 'updates':  complete messages → on_tool_start / on_tool_end / usage
  - 'custom':   harness signals → on_harness_continue / on_harness_warn
Using only 'updates' degrades on_token to
whole-text dumps and misses token streaming entirely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from coderio.agent.harness_middleware import HarnessMiddleware
from coderio.agent.permission_middleware import PermissionMiddleware
from coderio.agent.stream import NullStream
from coderio.session import Message
from coderio.session.store import Session


def _content_to_text(content: Any) -> str:
    """Normalize content (str or list of Anthropic blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return str(content) if content else ""


def _extract_thinking(content: Any) -> str:
    """Extract thinking-block text from Anthropic-style content (for the UI)."""
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            parts.append(block.get("thinking", "") or block.get("text", ""))
    return "".join(parts)


class _WinLocalShellBackend:
    """LocalShellBackend subclass that decodes subprocess output lossily on Windows.

    deepagents' LocalShellBackend uses subprocess.run(text=True), which decodes
    stdout/stderr as strict UTF-8. On a non-UTF-8 Windows locale (e.g. GBK/CP936),
    shell commands emitting localized bytes (chcp banners, error messages, CJK
    output) raise UnicodeDecodeError and crash the agent. This subclass reads
    bytes and decodes with errors='replace' so the agent keeps running.

    IMPORTANT: must SUBCLASS LocalShellBackend (not just wrap it) so that
    deepagents' isinstance(backend, SandboxBackendProtocol) check passes —
    composite.py:560 gates the `execute` tool on this check. A composition
    wrapper with __getattr__ proxying fails the isinstance test and the agent
    gets "backend doesn't support command execution" errors.
    """

    # Subclass created lazily at instantiation time (deepagents is optional).
    # We can't subclass at module-load time because LocalShellBackend may not
    # be importable. This factory builds a real subclass per instance.
    _RealCls = None

    def __new__(cls, **kwargs):
        if cls._RealCls is None:
            from deepagents.backends import LocalShellBackend

            class _Sub(LocalShellBackend):
                """Real subclass overriding execute for Windows GBK safety."""

                def execute(self, command: str, *, timeout: int | None = None):  # noqa: ANN201, ARG002
                    import subprocess

                    from deepagents.backends.protocol import ExecuteResponse

                    cwd = getattr(self, "_root_dir", None) or str(Path.cwd())
                    effective_timeout = timeout if timeout is not None else 120
                    try:
                        proc = subprocess.run(
                            command,
                            shell=True,
                            capture_output=True,
                            cwd=str(cwd),
                            timeout=effective_timeout,
                            text=False,  # bytes — decode ourselves (Windows GBK safety)
                        )
                        stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
                        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
                        exit_code = proc.returncode
                    except subprocess.TimeoutExpired:
                        return ExecuteResponse(output=f"Command timed out after {effective_timeout}s", exit_code=124)
                    except Exception as e:  # noqa: BLE001
                        return ExecuteResponse(output=f"Execution error: {e}", exit_code=1)
                    output = stdout
                    if stderr:
                        output += f"\n[stderr]\n{stderr}"
                    return ExecuteResponse(output=output, exit_code=exit_code)

            cls._RealCls = _Sub
        return cls._RealCls(**kwargs)


def _resolve_system_prompt(system_prompt, skill_store, active_skills):
    """Build coderio's system prompt, adapting tool names for deepagents."""
    if system_prompt is not None:
        return system_prompt
    from coderio.agent.prompts import ActiveSkills, build_system_prompt
    from coderio.skills.store import SkillStore

    store = skill_store or SkillStore()
    active = active_skills or ActiveSkills()
    sp = build_system_prompt(store, active)
    return (
        sp.replace("run bash commands", "run shell commands via the `execute` tool")
        .replace("use bash to execute", "use `execute` to run")
        .replace("call bash", "call `execute`")
    )


def _build_extra_tools(tools, skill_store, active_skills):
    """Collect coderio tools not already provided by deepagents."""
    from coderio.tools.base import to_langchain_tool as _adapt

    _SKIP = frozenset({"read_file", "write_file", "edit_file", "glob", "grep", "bash", "todo", "list_dir"})
    extra: list = []
    if tools:
        for t in tools:
            name = getattr(t, "name", "")
            if name in _SKIP:
                continue
            schema = getattr(t, "args_schema", None)
            if schema is not None:
                extra.append(_adapt(t, schema))
    if skill_store is not None and active_skills is not None:
        from coderio.agent.skill_tool import ActivateSkillTool, DeactivateSkillTool

        act = ActivateSkillTool(skill_store, active_skills)
        deact = DeactivateSkillTool(active_skills)
        extra.append(_adapt(act, act.args_schema))
        extra.append(_adapt(deact, deact.args_schema))
    return extra


def _build_research_subagent():
    """Return the research subagent spec (read-only, physically isolated).

    Tool exclusion uses the compat layer (_deepagents_compat) so that a
    deepagents API change degrades gracefully instead of crashing.
    """
    from coderio.agent._deepagents_compat import make_research_subagent_middleware

    return {
        "name": "research",
        "description": (
            "Read-only research and analysis agent. Use for: exploring an "
            "unfamiliar codebase section, finding where a feature is "
            "implemented, summarizing a file's purpose, or gathering "
            "evidence to ground an analysis. This agent can read files "
            "and search but CANNOT write or execute — it returns findings "
            "as text. Use it when you need to read many files without "
            "cluttering your own context."
        ),
        "system_prompt": (
            "You are a research subagent. Your job is to investigate the "
            "codebase and return clear, grounded findings.\n\n"
            "Rules:\n"
            "- Read the relevant files thoroughly before concluding.\n"
            "- Quote specific lines/functions as evidence.\n"
            "- Separate what you verified by reading from what you infer.\n"
            "- Be concise: return only the findings the caller needs, not "
            "a full retelling of every file you read.\n"
            "- If you can't find something, say so explicitly.\n"
            "- The calling agent only sees your final message, not your "
            "intermediate tool calls — make sure your answer is complete."
        ),
        "middleware": make_research_subagent_middleware(),
    }


def _build_inputs(checkpointer, user_input: str, session: Session) -> dict:
    """Build the messages input for the agent stream.

    With a checkpointer: only pass the new user message (deepagents restores
    prior state from sqlite). Without: pass full conversation history.
    """
    if checkpointer is not None:
        return {"messages": [HumanMessage(content=user_input)]}
    return {"messages": _build_history_messages(session.messages)}


def _run_stream(agent, inputs, thread_id, recursion_limit, stream, session, seen_ids, turn_writes):
    """Drive the deepagents graph with three stream modes. Returns final text."""
    config = {
        "recursion_limit": recursion_limit,
        "configurable": {"thread_id": thread_id},
    }
    tc_args: dict[str, tuple] = {}  # tool_call_id → (name, args) for file path tracking
    final_text = ""
    for mode, event in agent.stream(inputs, config=config, stream_mode=["messages", "updates", "custom"]):
        if mode == "messages":
            _handle_messages_mode(event, stream, session)
        elif mode == "updates":
            final_text = _handle_updates_mode(event, stream, session, seen_ids, turn_writes, tc_args) or final_text
        elif mode == "custom":
            _handle_custom_mode(event, stream)
    return final_text


def run_deep_agent(
    user_input: str,
    model,
    session: Session,
    stream=None,
    *,
    gate=None,
    skill_store=None,
    active_skills=None,
    tools: list | None = None,
    system_prompt: str | None = None,
    workdir: str | Path | None = None,
    harness_enabled: bool = True,
    recursion_limit: int = 200,
    command_policy=None,
) -> str:
    """Run a deepagents-backed agent turn (coderio's production engine).

    Builds a create_deep_agent with coderio's middleware stack (harness +
    permission), a Windows-safe shell backend, and coderio's extra tools.
    Streams events to coderio's StreamHandler protocol via three stream modes.
    Returns the final assistant text (also persisted to the session).

    Args:
        user_input: the user's message (str or multimodal content-block list).
        model: a langchain BaseChatModel.
        session: coderio Session (messages persisted here).
        stream: coderio StreamHandler (NullStream if None).
        gate: coderio PermissionGate (wrapped in PermissionMiddleware). None = no gate.
        skill_store: SkillStore (for build_system_prompt). None = empty store.
        active_skills: ActiveSkills (for build_system_prompt). None = empty.
        tools: extra coderio tools beyond deepagents' built-in FS/shell set.
        system_prompt: optional override (defaults to coderio's build_system_prompt).
        workdir: root dir for the shell backend (defaults to CWD).
        harness_enabled: if False, the verification harness is disabled.
        recursion_limit: langgraph recursion limit. Harness force-continues and
            middleware hooks each consume recursion budget; 200 is a safe default
            (harness escalates after 2 force-continues, each round uses ~5-10 recursions).
        command_policy: a CommandPolicy for the command-review middleware (blocks
            destructive shell commands like rm -rf /, mkfs, fork bombs, and
            optionally disables web tools). None = use CommandPolicy.default()
            (built-in blacklist active, network allowed). Pass an explicit policy
            to customize via config.toml [tools].blocked_commands / network_allowed.
    """
    stream = stream or NullStream()
    from deepagents import create_deep_agent

    # Neutralize deepagents' BASE_AGENT_PROMPT via compat layer (graceful
    # degradation if the internal API changes).
    from coderio.agent._deepagents_compat import neutralize_base_prompt

    neutralize_base_prompt()

    session.append(Message.user(user_input))

    sp = _resolve_system_prompt(system_prompt, skill_store, active_skills)
    middleware = [HarnessMiddleware(stream=stream, enabled=harness_enabled)]
    if gate is not None:
        middleware.append(PermissionMiddleware(gate))
    # Command-content review: always active (even in FULL mode). Blocks rm -rf /,
    # mkfs, fork bombs, etc. before they reach subprocess.run(shell=True).
    # This is NOT a real OS sandbox — see command_policy.py for limitations.
    from coderio.agent.command_review import CommandReviewMiddleware
    from coderio.tools.command_policy import CommandPolicy

    policy = command_policy or CommandPolicy.default()
    middleware.append(CommandReviewMiddleware(policy))

    backend = _WinLocalShellBackend(
        root_dir=str(workdir or Path.cwd()),
        virtual_mode=True,
        inherit_env=True,
    )

    extra_lc_tools = _build_extra_tools(tools, skill_store, active_skills)

    build_kwargs: dict[str, Any] = {
        "model": model,
        "middleware": middleware,
        "backend": backend,
        "subagents": [_build_research_subagent()],
    }
    if sp:
        build_kwargs["system_prompt"] = sp
    if extra_lc_tools:
        build_kwargs["tools"] = extra_lc_tools

    # --- Checkpointer: persist graph state across turns (sqlite) ---
    # Without a checkpointer, each run_deep_agent call starts from scratch —
    # SummarizationMiddleware's accumulated state resets, and we'd have to
    # re-pass the full history every turn (expensive + grows linearly).
    # With a checkpointer, deepagents restores prior state from the sqlite DB
    # and we only pass the NEW user message. Falls back to full-history mode
    # if sqlite is unavailable (package missing or DB corrupted).
    thread_id = session.id
    checkpointer, _db_conn = _try_create_checkpointer(session)
    if checkpointer is not None:
        build_kwargs["checkpointer"] = checkpointer

    final_text = ""
    _seen_tool_calls: set[str] = set()
    _turn_writes: list[str] = []

    try:
        agent = create_deep_agent(**build_kwargs)
        inputs = _build_inputs(checkpointer, user_input, session)
        if hasattr(stream, "on_step_start"):
            stream.on_step_start()
        final_text = _run_stream(
            agent, inputs, thread_id, recursion_limit, stream, session, _seen_tool_calls, _turn_writes
        )
    finally:
        if _db_conn is not None:
            try:
                _db_conn.close()
            except Exception:  # noqa: S110
                pass

    if hasattr(stream, "on_finish"):
        stream.on_finish()
    if hasattr(stream, "on_turn_end"):
        stream.on_turn_end(_turn_writes)
    if final_text and not _final_already_persisted(session):
        session.append(Message.assistant(final_text))
    return final_text


def _final_already_persisted(session: Session) -> bool:
    """Check if the last message is already an assistant text (avoid double-append).

    _handle_updates_mode persists AIMessages as they arrive; the final text may
    already be the last session message. We avoid appending a duplicate.
    """
    msgs = session.messages
    if not msgs:
        return False
    last = msgs[-1]
    return last.role == "assistant" and not getattr(last, "tool_calls", None)


def _try_create_checkpointer(session: Session):
    """Create a SqliteSaver for graph state persistence.

    Returns (checkpointer, conn) or (None, None). The caller MUST close the
    conn after use to avoid leaking file handles (especially on Windows where
    open sqlite files can't be deleted).

    Stores checkpoint state alongside the session jsonl (same directory,
    {session_id}.sqlite).
    """
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        db_path = session.path.parent / f"{session.id}.sqlite"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
    except ImportError:
        return None, None
    except Exception:
        return None, None
    # conn is now open. If SqliteSaver() or setup() fails, close it.
    try:
        checkpointer = SqliteSaver(conn)
        checkpointer.setup()
        return checkpointer, conn
    except Exception:
        try:
            conn.close()
        except Exception:  # noqa: S110
            pass
        return None, None


def _build_history_messages(session_messages: list) -> list:
    """Convert session messages to langchain messages for deepagents input.

    Unlike _to_langchain_messages (which prepends a SystemMessage), this only
    returns user/assistant/tool messages — the system prompt is injected
    separately by create_deep_agent's system_prompt parameter.

    Drops phase_timeline system messages (observability metadata). Keeps
    context_summary system messages (they carry compacted history the model
    needs). The current turn's user message is the LAST element (already
    appended to session before this call).
    """
    msgs: list = []
    for m in session_messages:
        if m.role == "user":
            msgs.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            tcs = None
            if m.tool_calls:
                tcs = [{"name": tc.name, "args": tc.args, "id": tc.id, "type": "tool_call"} for tc in m.tool_calls]
            msgs.append(AIMessage(content=m.content, tool_calls=tcs or []))
        elif m.role == "tool":
            msgs.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id or ""))
        elif m.role == "system":
            # Phase timelines are metadata — never shown to the model.
            # Context summaries ARE shown (that's their purpose).
            if getattr(m, "kind", None) != "phase_timeline":
                msgs.append(HumanMessage(content=m.content))
    return msgs


def _handle_messages_mode(event, stream, session) -> None:
    """Process 'messages' mode: token-by-token streaming.

    event is (AIMessageChunk, metadata). We extract text → on_token and
    thinking blocks → on_thinking. We do NOT persist here — complete messages
    are persisted in the 'updates' mode handler.
    """
    if not isinstance(event, tuple) or len(event) != 2:
        return
    chunk, metadata = event
    if not isinstance(chunk, AIMessage):
        return
    # Only stream from the model node (not tool results).
    node = metadata.get("langgraph_node", "") if isinstance(metadata, dict) else ""
    if node and node not in ("model", ""):
        return

    raw = getattr(chunk, "content", "")
    # Thinking blocks (Anthropic).
    thinking = _extract_thinking(raw)
    if thinking and hasattr(stream, "on_thinking"):
        stream.on_thinking(thinking)
    # Text content.
    text = _content_to_text(raw)
    if text and hasattr(stream, "on_token"):
        stream.on_token(text)


def _handle_updates_mode(event, stream, session, seen_ids: set, turn_writes: list, tc_args: dict) -> str:
    """Process 'updates' mode: complete messages (tool calls, tool results, final text).

    Returns the final assistant text if this event carries it (for the caller to
    return). Persists messages to session.
    """
    if not isinstance(event, dict):
        return ""
    final_text = ""
    for _node, payload in event.items():
        if not isinstance(payload, dict):
            continue
        msgs = payload.get("messages", [])
        for m in msgs:
            final_text = _emit_message(m, stream, session, seen_ids, turn_writes, tc_args) or final_text
    return final_text


def _handle_custom_mode(event, stream) -> None:
    """Process 'custom' mode: harness signals (continue / warn).

    event is the dict passed to runtime.stream_writer by HarnessMiddleware.
    """
    if not isinstance(event, dict):
        return
    etype = event.get("type")
    if etype == "harness_continue" and hasattr(stream, "on_harness_continue"):
        stream.on_harness_continue(event.get("reason", ""))
    elif etype == "harness_warn" and hasattr(stream, "on_harness_warn"):
        stream.on_harness_warn(event.get("message", ""))


def _emit_message(m, stream, session, seen_ids: set, turn_writes: list, tc_args: dict | None = None) -> str:
    """Map a complete langchain message to stream callbacks + session persistence.

    Returns the assistant text if this is a final (no tool_calls) AIMessage.
    """
    if isinstance(m, AIMessage):
        text = _content_to_text(getattr(m, "content", ""))
        tool_calls = getattr(m, "tool_calls", None) or []
        # Usage metadata lives on the AIMessage object (not in the updates
        # payload dict). Extract it here so the status bar can show live token
        # consumption. Without this, add_usage is never called (the old code
        # looked for it in payload.get("usage_metadata") which is always empty).
        usage = getattr(m, "usage_metadata", None)
        if usage and hasattr(stream, "add_usage"):
            stream.add_usage(usage)
        if tool_calls:
            from coderio.session import ToolCall

            tcs = []
            for tc in tool_calls:
                name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                args = dict(tc.get("args", {})) if isinstance(tc, dict) else dict(getattr(tc, "args", {}))
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                if tc_id not in seen_ids:
                    if hasattr(stream, "on_tool_start"):
                        stream.on_tool_start(name, args)
                    seen_ids.add(tc_id)
                # Intercept write_todos: deepagents replaces the whole list each
                # call, so args["todos"] is the current full todo list. Push it
                # to the UI for the live todo panel.
                if name == "write_todos" and "todos" in args and hasattr(stream, "on_todos_update"):
                    stream.on_todos_update(args["todos"])
                tcs.append(ToolCall(id=tc_id, name=name, args=args))
                # Remember args by tool_call_id so ToolMessage handler can
                # extract file paths for the turn-end summary.
                if tc_args is not None:
                    tc_args[tc_id] = (name, args)
            session.append(Message.assistant(text, tool_calls=tcs))
            return ""
        elif text:
            session.append(Message.assistant(text))
            return text
    elif isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None):
        name = getattr(m, "name", "tool")
        content = _content_to_text(getattr(m, "content", ""))
        if hasattr(stream, "on_tool_end"):
            stream.on_tool_end(name, content)
        session.append(Message.tool_result(m.tool_call_id, name, content))
        # Track file writes for the turn-end summary. Use tc_args to get the
        # actual file path (ToolMessage only has content, not args).
        if name in ("write_file", "edit_file") and not content.startswith(("Error", "Permission denied")):
            file_path = ""
            if tc_args is not None:
                _stored = tc_args.get(m.tool_call_id)
                if _stored:
                    _sname, _sargs = _stored
                    file_path = str(_sargs.get("file_path", _sargs.get("path", "")))
            turn_writes.append(file_path or name)
    return ""
