"""EXPERIMENTAL: Deepagents-backed agent entry point (parallel to agent/loop.run_agent).

This provides run_deep_agent: an alternative engine built on deepagents'
create_deep_agent (which gives us context management, planning tool, and
subagent capability for free) while preserving coderio's harness as a
middleware (the "wrote code but never verified → block done" hard constraint).

Status: experimental. Not wired into the CLI by default (run_agent / ReAct is
the production engine). deepagents is an optional dependency
(`pip install coderio[deepagent]`). Only used by scripts/verify_deepagent_live.py.

Tool-name note: deepagents' FilesystemMiddleware provides its own read_file/
write_file/edit_file/glob/grep/ls/execute/task tools, which collide with
coderio's tool names. So run_deep_agent uses deepagents' built-in toolset
(plus any coderio extras passed via extra_tools) rather than coderio's
build_default_tools. This is by design — the deepagents engine owns its FS.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from coderio.agent.harness_middleware import HarnessMiddleware
from coderio.agent.stream import NullStream
from coderio.session import Message
from coderio.session.store import Session

# deepagents is an optional dependency (heavy: pulls google-genai etc.).
# Import lazily so the package imports even if deepagents isn't installed.


def _content_to_text(content: Any) -> str:
    """Normalize content (str or list of Anthropic blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content) if content else ""


class _WinLocalShellBackend:
    """LocalShellBackend wrapper that decodes subprocess output lossily on Windows.

    deepagents' LocalShellBackend uses subprocess.run(text=True), which decodes
    stdout/stderr as strict UTF-8. On a non-UTF-8 Windows locale (e.g. GBK/CP936),
    shell commands emitting localized bytes (chcp banners, error messages, CJK
    output) raise UnicodeDecodeError and crash the agent. This subclass reads
    bytes and decodes with errors='replace' so the agent keeps running.

    Implemented by composition: we hold a real LocalShellBackend and only override
    execute(). All other methods (read/write/edit/glob/grep/ls) delegate unchanged.
    """

    def __init__(self, **kwargs):
        from deepagents.backends import LocalShellBackend
        self._inner = LocalShellBackend(**kwargs)

    def execute(self, command, **kwargs):
        import subprocess
        inner = self._inner
        timeout = kwargs.get("timeout", 120)
        env = inner.env if hasattr(inner, "env") else None
        import os as _os
        run_env = _os.environ.copy() if inner.inherit_env else {}
        if inner.env:
            run_env.update(inner.env)
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, timeout=timeout, env=run_env,
            )
            out = (result.stdout or b"").decode("utf-8", errors="replace")
            err = (result.stderr or b"").decode("utf-8", errors="replace")
            combined = out
            if err:
                combined += ("\n[stderr]\n" + err) if out else ("[stderr]\n" + err)
            from deepagents.backends.protocol import ExecuteResponse
            return ExecuteResponse(output=combined or "<no output>", exit_code=result.returncode, truncated=False)
        except subprocess.TimeoutExpired:
            from deepagents.backends.protocol import ExecuteResponse
            return ExecuteResponse(output=f"Error: command timed out after {timeout}s", exit_code=-1, truncated=False)
        except Exception as e:
            from deepagents.backends.protocol import ExecuteResponse
            return ExecuteResponse(output=f"Error: {type(e).__name__}: {e}", exit_code=-1, truncated=False)

    def __getattr__(self, name):
        # Delegate all other backend methods (read/write/edit/glob/grep/ls/...) to the inner backend.
        return getattr(self._inner, name)


def run_deep_agent(
    user_input: str,
    model,
    session: Session,
    stream=None,
    *,
    system_prompt: str | None = None,
    workdir: str | Path | None = None,
    extra_tools: list | None = None,
    subagents: list | None = None,
    harness_enabled: bool = True,
    recursion_limit: int = 40,
) -> str:
    """Run a deepagents-backed agent turn.

    Builds a create_deep_agent with: HarnessMiddleware (verification gate),
    LocalShellBackend (real disk at `workdir`), and optional subagents/tools.
    Streams events to coderio's StreamHandler protocol. Returns the final
    assistant text (also persisted to the session).

    Args:
        user_input: the user's message.
        model: a langchain BaseChatModel (e.g. ChatAnthropic for 智谱/阶跃).
        session: coderio Session (messages persisted here).
        stream: coderio StreamHandler (NullStream if None).
        system_prompt: optional override (defaults to deepagents' built-in).
        workdir: root dir for the LocalShellBackend (defaults to CWD).
        extra_tools: additional langchain tools beyond deepagents' built-in FS set.
        subagents: deepagents SubAgent configs for delegation.
        harness_enabled: if False, the verification harness is disabled.
        recursion_limit: langgraph recursion limit (harness interceptions consume these).
    """
    stream = stream or NullStream()
    # Lazy import: deepagents is optional.
    from deepagents import create_deep_agent

    session.append(Message.user(user_input))

    # Default to coderio's system prompt (CODE/QA/ANALYZE intent classification +
    # verification discipline) when none is given, so the deepagents engine gets
    # the same behavioral guidance as the ReAct engine. Adapt tool names: deepagents
    # uses 'execute' for shell (coderio prompt says 'bash').
    if system_prompt is None:
        from coderio.agent.prompts import ActiveSkills, build_system_prompt
        from coderio.skills.store import SkillStore
        sp = build_system_prompt(SkillStore(), ActiveSkills())
        system_prompt = (sp.replace("run bash commands", "run shell commands via the `execute` tool")
                          .replace("use bash to execute", "use `execute` to run")
                          .replace("call bash", "call `execute`"))

    # The harness middleware observes tool calls and intercepts unverified "done".
    middleware = [HarnessMiddleware(stream=stream, enabled=harness_enabled)]

    # Windows-safe backend: decodes subprocess output lossily (deepagents' default
    # strict-UTF-8 decode crashes on GBK/CJK locale shell output).
    backend = _WinLocalShellBackend(
        root_dir=str(workdir or Path.cwd()),
        # virtual_mode=True maps the agent's '/foo' paths (the injected FS prompt
        # tells it to use leading-slash absolute paths) onto root_dir/foo. With
        # virtual_mode=False, '/foo' would resolve to the OS root (C:\foo) and
        # fail with permission errors on Windows. virtual_mode is path-semantics
        # only — it does NOT sandbox shell execution.
        virtual_mode=True,
        inherit_env=True,
    )

    build_kwargs: dict[str, Any] = {
        "model": model,
        "middleware": middleware,
        "backend": backend,
    }
    if system_prompt:
        build_kwargs["system_prompt"] = system_prompt
    if extra_tools:
        build_kwargs["tools"] = extra_tools
    if subagents:
        build_kwargs["subagents"] = subagents

    agent = create_deep_agent(**build_kwargs)

    # Drive the graph, mapping deepagents stream events → coderio StreamHandler.
    final_text = ""
    config = {"recursion_limit": recursion_limit, "configurable": {"thread_id": f"deep-{id(user_input)}"}}
    inputs = {"messages": [HumanMessage(content=user_input)]}

    if hasattr(stream, "on_step_start"):
        stream.on_step_start()

    for event in agent.stream(inputs, config=config, stream_mode="updates"):
        # Each event is {node_name: {messages: [...], ...}}.
        if not isinstance(event, dict):
            continue
        for _node, payload in event.items():
            if not isinstance(payload, dict):
                continue
            # Skip middleware-internal node names (they still carry messages we want).
            msgs = payload.get("messages", [])
            for m in msgs:
                _emit_message(m, stream, session)
                if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
                    text = _content_to_text(getattr(m, "content", ""))
                    if text:
                        final_text = text
            # usage metadata (for /cost)
            usage = payload.get("usage_metadata") or {}
            if usage and hasattr(stream, "add_usage"):
                stream.add_usage(usage)

    stream.on_finish()
    if final_text:
        session.append(Message.assistant(final_text))
    return final_text


def _emit_message(m, stream, session) -> None:
    """Map a langchain message to stream callbacks + session persistence."""
    if isinstance(m, AIMessage):
        text = _content_to_text(getattr(m, "content", ""))
        tool_calls = getattr(m, "tool_calls", None) or []
        if text:
            stream.on_token(text)
        if tool_calls:
            # Emit tool-start for each call.
            from coderio.session import ToolCall
            tcs = []
            for tc in tool_calls:
                name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                args = dict(tc.get("args", {})) if isinstance(tc, dict) else dict(getattr(tc, "args", {}))
                stream.on_tool_start(name, args)
                tcs.append(ToolCall(id=tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", ""),
                                    name=name, args=args))
            session.append(Message.assistant(text, tool_calls=tcs))
        elif text:
            session.append(Message.assistant(text))
    # ToolMessage results (tool end) — content is the result string.
    elif hasattr(m, "tool_call_id") and getattr(m, "tool_call_id", None):
        name = getattr(m, "name", "tool")
        content = _content_to_text(getattr(m, "content", ""))
        stream.on_tool_end(name, content)
        session.append(Message.tool_result(m.tool_call_id, name, content))
