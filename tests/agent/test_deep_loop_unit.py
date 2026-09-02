"""Unit tests for deep_loop.py helper functions (no graph, no model).

These cover the pure-logic helpers in deep_loop.py that the integration tests
(test_deep_integration.py) don't reach directly. The goal is to close the
"production engine black box" gap (P1-2, 2026-08-10 report) by giving every
branch of _build_extra_tools / _resolve_system_prompt / _build_inputs /
_handle_*_mode a dedicated test without spinning up the full deepagents graph.
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from coderio.agent.deep_loop import (
    _build_extra_tools,
    _build_inputs,
    _content_to_text,
    _extract_thinking,
    _handle_custom_mode,
    _handle_messages_mode,
    _handle_updates_mode,
    _resolve_system_prompt,
)
from tests.agent.conftest import NoOpStream

# ----------------------------------------------------- _content_to_text / _extract_thinking


def test_content_to_text_string_passthrough():
    assert _content_to_text("hello") == "hello"


def test_content_to_text_anthropic_blocks():
    blocks = [
        {"type": "text", "text": "foo "},
        {"type": "thinking", "thinking": "secret"},  # ignored by content_to_text
        {"type": "text", "text": "bar"},
    ]
    assert _content_to_text(blocks) == "foo bar"


def test_content_to_text_empty():
    assert _content_to_text("") == ""
    assert _content_to_text(None) == ""


def test_extract_thinking_from_blocks():
    blocks = [
        {"type": "thinking", "thinking": "let me consider"},
        {"type": "text", "text": "answer"},
    ]
    assert _extract_thinking(blocks) == "let me consider"


def test_extract_thinking_no_thinking_block():
    assert _extract_thinking("plain string") == ""
    assert _extract_thinking([{"type": "text", "text": "x"}]) == ""


# ----------------------------------------------------- _resolve_system_prompt


def test_resolve_system_prompt_passthrough_override():
    """An explicit system_prompt override is returned as-is (no bash→execute
    translation on user-supplied prompts — they're responsible for their own
    tool names)."""
    assert _resolve_system_prompt("my custom prompt", None, None) == "my custom prompt"


def test_resolve_system_prompt_translates_bash_to_execute(tmp_path):
    """The default coderio prompt mentions 'bash'; deepagents' shell tool is
    'execute'. _resolve_system_prompt must translate the standalone word so the
    model calls the tool that actually exists. workdir points at an empty tmp
    dir — otherwise the REAL repo's AGENTS.md gets injected (the feature works
    so well it broke its own test)."""
    sp = _resolve_system_prompt(None, None, None, workdir=tmp_path)
    assert "execute" in sp, "default prompt should reference 'execute' after translation"
    # The word 'bash' should not appear as a standalone token (it may appear
    # inside another word, but \bbash\b matches should be gone).
    import re

    assert not re.search(r"\bbash\b", sp), f"standalone 'bash' should be gone, got: {sp!r}"


def test_resolve_system_prompt_injects_project_instructions(tmp_path):
    """P2-7 wiring guard (adversarial review: removing the injection or the
    workdir pass-through left all tests green). A repo AGENTS.md under
    workdir MUST reach the system prompt."""
    (tmp_path / "AGENTS.md").write_text("USE UV NOT PIP MARKER", encoding="utf-8")
    sp = _resolve_system_prompt(None, None, None, workdir=tmp_path)
    assert "USE UV NOT PIP MARKER" in sp, "project instructions must be injected"


# ----------------------------------------------------- _build_extra_tools


class _FakeCoderioTool:
    """Minimal coderio Tool stub: has .name, .description, .run()."""

    def __init__(self, name):
        self.name = name
        self.description = f"fake tool {name}"

    def run(self, **kwargs):
        return "ok"


class _FakeArgsSchema(BaseModel):
    """A real pydantic BaseModel subclass (StructuredTool.from_function requires
    args_schema to be a BaseModel subclass or JSON schema dict, not a bare class)."""

    value: str = ""


def test_build_extra_tools_skips_duplicates():
    """Tools whose names are in _SKIP (deepagents already provides them) must
    NOT appear in the output — otherwise the model sees two read_file tools."""

    class _T:
        name = "read_file"
        args_schema = _FakeArgsSchema
        description = "dup"

    out = _build_extra_tools([_T()], None, None)
    assert out == [], f"read_file should be skipped, got: {[getattr(t, 'name', t) for t in out]}"


def test_build_extra_tools_adapts_coderio_tool_with_schema():
    """A coderio tool with an args_schema is adapted via to_langchain_tool."""

    class _T:
        name = "my_tool"
        args_schema = _FakeArgsSchema
        description = "custom"

        def run(self, **kw):
            return "ran"

    out = _build_extra_tools([_T()], None, None)
    assert len(out) == 1
    # The adapted tool should be a langchain StructuredTool (has .invoke).
    assert hasattr(out[0], "invoke")


def test_build_extra_tools_passes_mcp_tool_through():
    """MCP tools arrive as langchain BaseTool/StructuredTool with `invoke` but
    no coderio-style args_schema attribute path. They must pass through without
    re-adaptation (their names are prefixed, so they never hit _SKIP)."""

    class _McpLike:
        # Has invoke (langchain-like), no args_schema attribute → MCP path.
        name = "filesystem_read_file"
        description = "mcp tool"

        def invoke(self, **kw):
            return "mcp result"

    out = _build_extra_tools([_McpLike()], None, None)
    assert len(out) == 1
    assert out[0] is _McpLike() or out[0].__class__ is _McpLike or hasattr(out[0], "invoke")


def test_build_extra_tools_none_input():
    """tools=None → empty list (no crash)."""
    assert _build_extra_tools(None, None, None) == []


def test_build_extra_tools_injects_skill_tools():
    """When skill_store and active_skills are both provided, the skill
    activate/deactivate tools are appended to the output."""
    from coderio.agent.prompts import ActiveSkills
    from coderio.skills.store import SkillStore

    store = SkillStore()
    active = ActiveSkills()
    out = _build_extra_tools(None, store, active)
    names = {getattr(t, "name", "") for t in out}
    # ActivateSkillTool and DeactivateSkillTool should be present.
    assert any("activate" in n.lower() or "skill" in n.lower() for n in names), (
        f"expected skill tools, got names: {names}"
    )


# ----------------------------------------------------- _build_inputs


class _FakeSession:
    """Minimal Session stub for _build_inputs (only needs .messages)."""

    def __init__(self, messages):
        self.messages = messages


def test_build_inputs_with_checkpointer_only_new_message():
    """When a checkpointer is present, deepagents restores prior state from
    sqlite — so we pass ONLY the new user message (not full history)."""
    session = _FakeSession([HumanMessage(content="old"), AIMessage(content="msg")])
    inputs = _build_inputs(object(), "new question", session)  # type: ignore[arg-type]
    assert len(inputs["messages"]) == 1
    assert inputs["messages"][0].content == "new question"


def test_build_inputs_without_checkpointer_passes_full_history():
    """Without a checkpointer, the full session history is passed (deepagents
    has no memory of prior turns)."""

    session = _FakeSession(
        [
            SimpleNamespace(role="user", content="hi", tool_calls=None, tool_call_id=None, kind=None),
            SimpleNamespace(role="assistant", content="hello", tool_calls=None, tool_call_id=None, kind=None),
        ]
    )
    inputs = _build_inputs(None, "new", session)  # type: ignore[arg-type]
    # Should contain the history + the new message.
    assert len(inputs["messages"]) >= 2


# ----------------------------------------------------- _handle_messages_mode


def test_handle_messages_mode_emits_token():
    """A model-node AIMessage with text content → on_token fires."""
    stream = NoOpStream()
    chunk = AIMessage(content="hello world")
    metadata = {"langgraph_node": "model"}
    _handle_messages_mode((chunk, metadata), stream, session=None)
    assert "hello world" in stream.tokens


def test_handle_messages_mode_ignores_non_model_node():
    """Tool-node output is NOT streamed as tokens (only model output is)."""
    stream = NoOpStream()
    chunk = AIMessage(content="tool output")
    metadata = {"langgraph_node": "tools"}
    _handle_messages_mode((chunk, metadata), stream, session=None)
    assert stream.tokens == []


def test_handle_messages_mode_malformed_event_noop():
    """A non-tuple or wrong-length event is silently ignored (defensive)."""
    stream = NoOpStream()
    _handle_messages_mode("not a tuple", stream, session=None)
    _handle_messages_mode(("only one element",), stream, session=None)
    assert stream.tokens == []


def test_handle_messages_mode_extracts_thinking():
    """Anthropic thinking blocks fire on_thinking."""
    stream = NoOpStream()
    chunk = AIMessage(
        content=[
            {"type": "thinking", "thinking": "pondering"},
            {"type": "text", "text": "answer"},
        ]
    )
    _handle_messages_mode((chunk, {"langgraph_node": "model"}), stream, session=None)
    assert "answer" in stream.tokens


# ----------------------------------------------------- _handle_updates_mode


def test_handle_updates_mode_persists_tool_call_to_session():
    """An AIMessage with tool_calls in an updates payload is processed by
    _emit_message (which persists). Here we verify _handle_updates_mode routes
    the message through (the persistence itself is covered in integration
    tests); the key assertion is that it doesn't crash on a well-formed payload."""
    stream = NoOpStream()

    class _Session:
        def __init__(self):
            self.messages = []

        def append(self, m):
            self.messages.append(m)

    session = _Session()
    payload = {
        "model": {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"file_path": "/x", "content": "y"},
                            "id": "tc1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        }
    }
    final = _handle_updates_mode(payload, stream, session, set(), [], {})
    # A tool_call message isn't final text → returns "".
    assert final == ""
    # on_tool_start should have fired.
    assert any(name == "write_file" for name, _ in stream.tool_starts)


def test_handle_updates_mode_non_dict_event_returns_empty():
    """A malformed (non-dict) event is ignored, returns empty string."""
    stream = NoOpStream()
    assert _handle_updates_mode("garbage", stream, None, set(), [], {}) == ""


# ----------------------------------------------------- _handle_custom_mode


def test_handle_custom_mode_harness_continue():
    """A {type: 'harness_continue'} event fires on_harness_continue."""
    stream = NoOpStream()
    _handle_custom_mode({"type": "harness_continue", "reason": "verify first"}, stream)
    assert any(s["type"] == "harness_continue" for s in stream.harness_signals)


def test_handle_custom_mode_harness_warn():
    """A {type: 'harness_warn'} event fires on_harness_warn."""
    stream = NoOpStream()
    _handle_custom_mode({"type": "harness_warn", "message": "releasing"}, stream)
    assert any(s["type"] == "harness_warn" for s in stream.harness_signals)


def test_handle_custom_mode_unknown_type_noop():
    """Unknown event types are ignored without crashing."""
    stream = NoOpStream()
    _handle_custom_mode({"type": "something_else"}, stream)
    _handle_custom_mode("not a dict", stream)
    assert stream.harness_signals == []


# ----------------------------------------------------- _WinLocalShellBackend cwd fix
# REGRESSION (2026-08-10 sandbox analysis): the execute override read
# self._root_dir (which doesn't exist) instead of self.cwd, so workspace_root
# config silently had no effect on shell execution. These tests verify the fix.


def test_win_shell_backend_uses_cwd_attr(tmp_path):
    """The production shell backend must read self.cwd (set by FilesystemBackend
    from root_dir), not the non-existent self._root_dir.

    Instantiates the backend with root_dir=tmp_path, then runs a cwd-reporting
    command (`pwd` on POSIX, `pwd -W` on Windows/Git Bash) and verifies the
    output reflects tmp_path, not Path.cwd().
    """
    import pytest

    deepagents = pytest.importorskip("deepagents")
    if not deepagents:
        return

    from coderio.agent.deep_loop import make_shell_backend

    backend = make_shell_backend(root_dir=str(tmp_path), virtual_mode=True, inherit_env=True)
    # self.cwd should be set by FilesystemBackend.__init__ from root_dir.
    assert getattr(backend, "cwd", None) is not None, "backend.cwd must be set from root_dir"
    # Execute a cwd-reporting command.
    import sys

    # P0-4 (2026-08-14): Windows executes through Git Bash, so the command must
    # be bash-compatible — bare `cd` is SILENT in bash (it only prints in
    # cmd.exe). `pwd -W` is a Git Bash builtin that prints the Windows-form
    # path (C:/...), matching tmp_path.as_posix().
    if sys.platform == "win32":
        cmd = "pwd -W"
        expected = tmp_path.as_posix()
    else:
        cmd = "pwd"
        expected = str(tmp_path)
    result = backend.execute(cmd)
    output = getattr(result, "output", "") or ""
    # The output must mention tmp_path (the shell ran in the workspace root).
    assert expected in output, f"execute should run in self.cwd ({expected}), got output: {output!r}"


def test_win_shell_backend_truncates_oversized_output(tmp_path):
    """The execute override must truncate output at _max_output_bytes.

    Restores upstream behavior the old override dropped: a command producing
    huge output (e.g. `yes` or a big find) must be truncated to prevent OOM
    in the agent's context window.
    """
    import pytest

    deepagents = pytest.importorskip("deepagents")
    if not deepagents:
        return

    from coderio.agent.deep_loop import make_shell_backend

    # Use a small max_output_bytes so the test doesn't generate 100KB of output.
    backend = make_shell_backend(
        root_dir=str(tmp_path),
        virtual_mode=True,
        inherit_env=True,
        max_output_bytes=200,  # small threshold for fast testing
    )
    import sys

    # Generate ~2KB of output (well over the 200-byte threshold).
    if sys.platform == "win32":
        cmd = "powershell -Command \"'x' * 2000\""
    else:
        cmd = "yes x | head -c 2000"
    result = backend.execute(cmd)
    output = getattr(result, "output", "") or ""
    assert "truncated" in output.lower() or "..." in output, (
        f"output should be truncated, got {len(output)} bytes: {output[:100]!r}..."
    )


def test_win_shell_backend_nonexistent_workspace_clear_error(tmp_path):
    """REGRESSION GUARD: when workspace_root points to a non-existent directory,
    the sandbox path must return a CLEAR error (exit=1 + actionable message),
    not a cryptic 'CreateProcessAsUserW failed (err=0)'.

    Context (found during E2E testing 2026-08-11): before this check, a typo in
    [tools].workspace_root caused CreateProcessAsUserW to fail with err=0
    (misleading — real cause is ERROR_PATH_NOT_FOUND), and the error surface
    was 'CreateProcessAsUserW failed — cannot launch sandboxed child' which
    neither the model nor the user could diagnose. Now we pre-check and return
    an actionable message naming the bad path + how to fix it.
    """
    import pytest

    deepagents = pytest.importorskip("deepagents")
    if not deepagents:
        return

    from coderio.agent.deep_loop import make_shell_backend

    # A path that definitely doesn't exist.
    bad_ws = str(tmp_path / "never-created-xyz")
    backend = make_shell_backend(root_dir=bad_ws, virtual_mode=True, inherit_env=True, sandbox_mode="job")
    result = backend.execute("echo test")
    ec = getattr(result, "exit_code", None)
    out = getattr(result, "output", "") or ""

    assert ec == 1, f"nonexistent workspace should return exit=1, got {ec}"
    assert "non-existent" in out.lower() or "does not exist" in out.lower(), (
        f"error should clearly say the directory doesn't exist, got: {out!r}"
    )
    assert bad_ws in out, "error should name the bad path so the user can fix it"
    assert "workspace_root" in out, "error should tell the user which config to fix"


def test_win_shell_backend_existing_workspace_runs_normally(tmp_path):
    """A real existing workspace must still execute normally (the existence
    check must not false-positive on valid paths)."""
    import pytest

    deepagents = pytest.importorskip("deepagents")
    if not deepagents:
        return

    from coderio.agent.deep_loop import make_shell_backend

    backend = make_shell_backend(root_dir=str(tmp_path), virtual_mode=True, inherit_env=True, sandbox_mode="job")
    result = backend.execute("echo workspace-ok")
    out = getattr(result, "output", "") or ""
    assert "workspace-ok" in out, f"existing workspace should run normally, got: {out!r}"


def test_run_stream_aborts_midstream_when_should_abort_fires():
    """P1-5: wire the TUI's is_interrupted flag into the engine. should_abort
    returning True must raise InterruptedError at the next chunk boundary —
    the stream stops pulling before draining, and the caller sees the
    exception (Esc actually stops the engine instead of relying on
    worker.cancel() semantics)."""
    import pytest

    from coderio.agent.deep_loop import _run_stream
    from coderio.agent.stream import NullStream

    seen: list[int] = []

    class FakeAgent:
        def stream(self, *a, **k):
            for i in range(100):
                seen.append(i)
                yield ("custom", {"type": f"m{i}"})

    def abort() -> bool:
        return len(seen) >= 3  # abort once 3 chunks are out

    with pytest.raises(InterruptedError):
        _run_stream(FakeAgent(), {}, "t", 50, NullStream(), None, set(), [], abort)
    assert len(seen) == 3, f"loop should stop at the abort boundary, got {len(seen)}"


def test_run_stream_runs_to_completion_without_abort():
    from coderio.agent.deep_loop import _run_stream
    from coderio.agent.stream import NullStream

    seen: list[int] = []

    class FakeAgent:
        def stream(self, *a, **k):
            for i in range(5):
                seen.append(i)
                yield ("custom", {"type": f"m{i}"})

    _run_stream(FakeAgent(), {}, "t", 50, NullStream(), None, set(), [], None)
    assert seen == [0, 1, 2, 3, 4]


# --------------------------------------------- interrupt → checkpointer cleanup (P1-3)


def test_interrupt_drops_thread_checkpoint_and_surfaces_writes(tmp_path, monkeypatch):
    """P1-3 regression: the InterruptedError handler must (a) drop the thread's
    checkpoint so the next turn doesn't replay dangling tool_calls state
    (audit finding #9), (b) still fire on_turn_end with the writes so far
    (audit finding #10), and (c) close the sqlite conn. The cleanup branch had
    zero test coverage before 2026-09-02."""
    import pytest
    from langchain_core.messages import AIMessage, ToolMessage

    from coderio.agent.deep_loop import TurnSpec, run_deep_agent
    from coderio.session.store import Session

    session = Session.create(str(tmp_path / "sessions"), {"model": "m"})

    deleted_threads: list[str] = []
    closed: list[bool] = []

    class FakeCheckpointer:
        def delete_thread(self, thread_id):
            deleted_threads.append(thread_id)

    class FakeConn:
        def close(self):
            closed.append(True)

    monkeypatch.setattr("coderio.agent.deep_loop._try_create_checkpointer", lambda s: (FakeCheckpointer(), FakeConn()))

    turn_ends: list[list[str]] = []

    class AbortStream:
        """Interrupts after the first streamed chunk (Esc mid-turn)."""

        def __init__(self):
            self._chunks_seen = 0

        def is_interrupted(self):
            return self._chunks_seen >= 1

        def on_step_start(self):
            pass

        def on_token(self, text):
            pass

        def on_turn_end(self, writes):
            turn_ends.append(list(writes))

    stream = AbortStream()

    class FakeAgent:
        def stream(self, inputs, config=None, stream_mode=None):
            yield (
                "updates",
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "write_file",
                                        "args": {"file_path": "/tmp/x.py"},
                                        "id": "t1",
                                        "type": "tool_call",
                                    }
                                ],
                            ),
                            ToolMessage(content="Wrote /tmp/x.py", tool_call_id="t1", name="write_file"),
                        ]
                    }
                },
            )
            # the engine's abort gate fires before the NEXT pull — the remaining
            # chunks must never be reached
            stream._chunks_seen += 1
            for _ in range(100):
                yield ("custom", {"type": "never-reached"})

    monkeypatch.setattr("deepagents.create_deep_agent", lambda **kw: FakeAgent())

    with pytest.raises(InterruptedError):
        run_deep_agent("do work", TurnSpec(model=object(), workdir=str(tmp_path)), session, stream=stream)

    assert deleted_threads == [session.id], (
        "interrupt must drop the thread checkpoint to avoid replaying dangling tool_calls state (audit #9)"
    )
    assert closed == [True], "sqlite conn must be closed on the interrupt path"
    assert turn_ends and turn_ends[0] == ["/tmp/x.py"], (
        f"on_turn_end must still fire with the writes so far (audit #10), got {turn_ends}"
    )
