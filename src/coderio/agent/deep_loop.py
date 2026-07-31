"""deepagents-backed production agent engine for coderio.

Replaces the hand-rolled ReAct loop (agent/loop.py run_agent) as the default
engine. deepagents provides context management (offload + summarization), a
filesystem/shell backend, subagents, and task planning — capabilities coderio
previously reimplemented (compact.py) or lacked (subagents) entirely.

What coderio ADDS on top via middleware:
  - HarnessMiddleware: the "wrote code but never verified → block done" hard
    constraint (4 gates). deepagents trusts the agent; coderio does not.
  - PermissionMiddleware: 4-tier access system (plan/confirm/auto_edit/full) +
    workspace boundary. deepagents' FilesystemPermission is coarser.

The old ReAct engine (loop.py) is kept as a fallback.
_execute_turn directly). This module is the production path invoked by the TUI.

Streaming: uses THREE stream modes in parallel —
  - 'messages': AIMessageChunk → on_token / on_thinking (token-by-token stream)
  - 'updates':  complete messages → on_tool_start / on_tool_end / usage
  - 'custom':   harness signals → on_harness_continue / on_harness_warn
Using only 'updates' (as the old experimental version did) degrades on_token to
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
    """
    stream = stream or NullStream()
    # Lazy import: deepagents is a heavy dependency.
    from deepagents import create_deep_agent

    session.append(Message.user(user_input))

    # Build coderio's system prompt (behavioral guidance) unless overridden.
    # Adapt tool names: deepagents uses 'execute' for shell (coderio says 'bash').
    if system_prompt is None:
        from coderio.agent.prompts import ActiveSkills, build_system_prompt
        from coderio.skills.store import SkillStore

        store = skill_store or SkillStore()
        active = active_skills or ActiveSkills()
        sp = build_system_prompt(store, active)
        system_prompt = (
            sp.replace("run bash commands", "run shell commands via the `execute` tool")
            .replace("use bash to execute", "use `execute` to run")
            .replace("call bash", "call `execute`")
        )

    # --- Middleware stack: harness (verification) + permission (access control) ---
    middleware = [HarnessMiddleware(stream=stream, enabled=harness_enabled)]
    if gate is not None:
        middleware.append(PermissionMiddleware(gate))

    # --- Windows-safe shell backend ---
    backend = _WinLocalShellBackend(
        root_dir=str(workdir or Path.cwd()),
        virtual_mode=True,
        inherit_env=True,
    )

    # --- Extra tools: coderio's web_search/web_fetch/note/skill tools ---
    # deepagents provides read_file/write_file/edit_file/glob/grep/execute/write_todos.
    # coderio's tools complement these (web search, notes, skill activation).
    extra_lc_tools: list = []
    if tools:
        from coderio.tools.base import to_langchain_tool as _adapt

        for t in tools:
            name = getattr(t, "name", "")
            # Skip tools that deepagents already provides (avoid name collisions).
            if name in ("read_file", "write_file", "edit_file", "glob", "grep", "bash", "todo", "list_dir"):
                continue
            schema = getattr(t, "args_schema", None)
            if schema is not None:
                extra_lc_tools.append(_adapt(t, schema))

    # Skill activation tools — coderio's skill system. These must be registered
    # explicitly (they're not in build_default_tools). Without them the model
    # gets "activate_skill is not a valid tool" errors.
    if skill_store is not None and active_skills is not None:
        from coderio.agent.skill_tool import ActivateSkillTool, DeactivateSkillTool
        from coderio.tools.base import to_langchain_tool as _adapt

        act = ActivateSkillTool(skill_store, active_skills)
        deact = DeactivateSkillTool(active_skills)
        extra_lc_tools.append(_adapt(act, act.args_schema))
        extra_lc_tools.append(_adapt(deact, deact.args_schema))

    build_kwargs: dict[str, Any] = {
        "model": model,
        "middleware": middleware,
        "backend": backend,
    }
    if system_prompt:
        build_kwargs["system_prompt"] = system_prompt
    if extra_lc_tools:
        build_kwargs["tools"] = extra_lc_tools

    agent = create_deep_agent(**build_kwargs)

    # --- Drive the graph with three stream modes in parallel ---
    final_text = ""
    # Stable thread_id so SummarizationMiddleware's offload path is consistent
    # across turns (uses session file stem, not a per-input hash).
    thread_id = getattr(session, "stem", None) or f"session-{id(session):x}"
    config = {
        "recursion_limit": recursion_limit,
        "configurable": {"thread_id": thread_id},
    }
    inputs = {"messages": [HumanMessage(content=user_input)]}

    if hasattr(stream, "on_step_start"):
        stream.on_step_start()

    # Track which AIMessage tool_calls we've already announced (dedup across modes).
    _seen_tool_calls: set[str] = set()
    _turn_writes: list[str] = []

    for mode, event in agent.stream(inputs, config=config, stream_mode=["messages", "updates", "custom"]):
        if mode == "messages":
            _handle_messages_mode(event, stream, session)
        elif mode == "updates":
            final_text = _handle_updates_mode(event, stream, session, _seen_tool_calls, _turn_writes) or final_text
        elif mode == "custom":
            _handle_custom_mode(event, stream)

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


def _handle_updates_mode(event, stream, session, seen_ids: set, turn_writes: list) -> str:
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
            final_text = _emit_message(m, stream, session, seen_ids, turn_writes) or final_text
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


def _emit_message(m, stream, session, seen_ids: set, turn_writes: list) -> str:
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
                tcs.append(ToolCall(id=tc_id, name=name, args=args))
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
        # Track file writes for the turn-end summary.
        if name in ("write_file", "edit_file") and not content.startswith(("Error", "Permission denied")):
            # Args aren't on ToolMessage; best-effort from content.
            turn_writes.append(f"{name}")
    return ""
