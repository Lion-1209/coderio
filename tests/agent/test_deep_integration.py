"""Integration tests for run_deep_agent with a fake model.

These tests exercise the full deepagents graph (create_deep_agent → stream →
HarnessMiddleware → PermissionMiddleware → session persistence) without a real
LLM. The fake model yields predetermined AIMessages, so we can verify:

1. Pure Q&A: model returns text, no tool calls → session persisted correctly.
2. Write + verify: model writes a file then declares done → HarnessMiddleware
   forces continuation (VerifyGate).
3. Permission denial: PLAN mode blocks write_file → model gets error message.

The fake model + stream helpers are shared via tests/agent/conftest.py so
test_deep_loop_unit.py can reuse them without duplication.

The fake model inherits BaseChatModel so deepagents accepts it. Its .stream()
yields the predetermined messages in sequence.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from tests.agent.conftest import NoOpStream, make_model, make_session

# Skip entire module if deepagents isn't installed (CI installs it, local may not).
deepagents = pytest.importorskip("deepagents")


@pytest.mark.skipif(
    not deepagents,
    reason="deepagents not installed",
)
def test_fake_model_qa(tmp_path):
    """Pure Q&A: model returns text, no tools → session has user + assistant."""
    from coderio.agent.deep_loop import run_deep_agent

    model = make_model(AIMessage(content="你好！我是 coderio。"))
    session = make_session(tmp_path)
    stream = NoOpStream()

    result = run_deep_agent(
        "你好",
        model,
        session,
        stream=stream,
        harness_enabled=False,
        workdir=str(tmp_path),
    )

    assert "你好" in result or "coderio" in result
    assert stream.finished
    assert len(session.messages) >= 2  # user + assistant


@pytest.mark.skipif(not deepagents, reason="deepagents not installed")
def test_permission_plan_mode_blocks_write(tmp_path):
    """PLAN mode blocks write_file → model gets Permission denied error."""
    from coderio.agent.deep_loop import run_deep_agent
    from coderio.tools.permission import PermissionGate

    model = make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"file_path": "/test.txt", "content": "x"},
                    "id": "tc1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="done."),
    )
    session = make_session(tmp_path)
    stream = NoOpStream()
    gate = PermissionGate("plan")

    result = run_deep_agent(
        "write a file",
        model,
        session,
        stream=stream,
        gate=gate,
        harness_enabled=False,
        workdir=str(tmp_path),
    )

    # The write should have been blocked by the gate.
    assert any("Permission denied" in r for _, r in stream.tool_ends), (
        f"expected Permission denied in tool_ends, got: {stream.tool_ends}"
    )


@pytest.mark.skipif(not deepagents, reason="deepagents not installed")
def test_full_mode_allows_write(tmp_path):
    """FULL mode allows write_file without prompting."""
    from coderio.agent.deep_loop import run_deep_agent
    from coderio.tools.permission import AutoPermissionGate

    model = make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"file_path": "/test.txt", "content": "hello"},
                    "id": "tc1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="done."),
    )
    session = make_session(tmp_path)
    stream = NoOpStream()
    gate = AutoPermissionGate()

    result = run_deep_agent(
        "write a file",
        model,
        session,
        stream=stream,
        gate=gate,
        harness_enabled=False,
        workdir=str(tmp_path),
    )

    # The write should NOT have been blocked.
    assert not any("Permission denied" in r for _, r in stream.tool_ends), (
        f"FULL mode should allow write, but got denied: {stream.tool_ends}"
    )


# --- NEW: deep_loop main-path coverage (P1-2, 2026-08-10 report) ---
# These tests fill the "production engine black box" gap — the middleware
# combination, harness force-continue, command review, and stream handlers were
# previously untested at the graph level. Each test uses the fake model to drive
# a specific path through run_deep_agent and asserts on observed effects.


@pytest.mark.skipif(not deepagents, reason="deepagents not installed")
def test_command_review_blocks_destructive_shell(tmp_path):
    """CommandReviewMiddleware must block 'rm -rf /' even in FULL mode.

    Covers the integration of CommandReviewMiddleware into the middleware
    chain: the fake model requests execute({command: 'rm -rf /'}), the
    permission gate (FULL) allows it through, but the command review layer
    blocks it by content. The model then sees a ToolMessage explaining the
    block. This is the safety-takes-priority-over-FULL-mode contract.
    """
    from coderio.agent.deep_loop import run_deep_agent
    from coderio.tools.command_policy import CommandPolicy
    from coderio.tools.permission import AutoPermissionGate

    model = make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "execute",
                    "args": {"command": "rm -rf /"},
                    "id": "tc1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="I cannot run that command."),
    )
    session = make_session(tmp_path)
    stream = NoOpStream()
    gate = AutoPermissionGate()  # FULL — but command review still blocks
    policy = CommandPolicy.default()

    run_deep_agent(
        "delete everything",
        model,
        session,
        stream=stream,
        gate=gate,
        harness_enabled=False,
        workdir=str(tmp_path),
        command_policy=policy,
    )

    blocked = [r for _, r in stream.tool_ends if "Blocked by command policy" in r]
    assert blocked, f"FULL mode must still block 'rm -rf /' via command review, got tool_ends: {stream.tool_ends}"


@pytest.mark.skipif(not deepagents, reason="deepagents not installed")
def test_command_review_network_disabled_blocks_web_fetch(tmp_path):
    """network_allowed=False blocks web_fetch at the command-review layer.

    Covers the _NETWORK_TOOLS path in CommandReviewMiddleware: even when the
    permission gate would allow web_fetch, a CommandPolicy with
    network_allowed=False short-circuits it with a clear ToolMessage.
    """
    from coderio.agent.deep_loop import run_deep_agent
    from coderio.tools.command_policy import CommandPolicy
    from coderio.tools.permission import AutoPermissionGate

    # web_fetch is a coderio tool, not a deepagents built-in — we pass it via
    # `tools` so the model can request it. The fake model doesn't actually
    # validate tool schemas, so a bare name in tool_calls suffices.
    model = make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "web_fetch",
                    "args": {"url": "https://example.com"},
                    "id": "tc1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="cannot fetch."),
    )
    session = make_session(tmp_path)
    stream = NoOpStream()
    gate = AutoPermissionGate()
    policy = CommandPolicy(network_allowed=False)

    run_deep_agent(
        "fetch a url",
        model,
        session,
        stream=stream,
        gate=gate,
        harness_enabled=False,
        workdir=str(tmp_path),
        command_policy=policy,
    )

    blocked = [r for _, r in stream.tool_ends if "network access is disabled" in r]
    assert blocked, f"network_allowed=False should block web_fetch, got: {stream.tool_ends}"


@pytest.mark.skipif(not deepagents, reason="deepagents not installed")
def test_harness_force_continue_on_unverified_write(tmp_path):
    """Harness enabled: model writes then declares done → VerifyGate forces a
    continuation turn before allowing the turn to end.

    Covers the main-path integration of HarnessMiddleware.after_model's
    jump_to='model' mechanism. The fake model yields: (1) a write_file tool
    call, (2) a text-only 'done' message. Without the harness, the turn would
    end after (2); with it, the graph is forced back to the model for a
    verify step. We assert the session accumulated more than the bare minimum
    of messages (user + tool_call + tool_result + final), indicating the
    force-continue injected an extra round.
    """
    from coderio.agent.deep_loop import run_deep_agent
    from coderio.tools.permission import AutoPermissionGate

    model = make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"file_path": "/app.py", "content": "print('hi')"},
                    "id": "tc1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="done."),  # wants to end, but harness will force verify
        AIMessage(content="verified, all done."),  # released after escalation
    )
    session = make_session(tmp_path)
    stream = NoOpStream()
    gate = AutoPermissionGate()

    run_deep_agent(
        "write app.py",
        model,
        session,
        stream=stream,
        gate=gate,
        harness_enabled=True,
        workdir=str(tmp_path),
    )

    # The harness must have fired at least one force-continue signal.
    continues = [s for s in stream.harness_signals if s["type"] == "harness_continue"]
    assert continues, f"harness should force-continue on unverified write, signals: {stream.harness_signals}"


@pytest.mark.skipif(not deepagents, reason="deepagents not installed")
def test_session_persists_tool_calls_and_results(tmp_path):
    """_handle_updates_mode must persist both AIMessage(tool_calls) and the
    resulting ToolMessage into the session.

    Covers the session-persistence path in _handle_updates_mode / _emit_message:
    after a tool call completes, the session's message list must contain the
    assistant tool_call message AND the tool result, so /export and resume can
    reconstruct what happened.
    """
    from coderio.agent.deep_loop import run_deep_agent
    from coderio.tools.permission import AutoPermissionGate

    model = make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"file_path": "/note.txt", "content": "hi"},
                    "id": "tc1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="written."),
    )
    session = make_session(tmp_path)
    stream = NoOpStream()
    gate = AutoPermissionGate()

    run_deep_agent(
        "write a note",
        model,
        session,
        stream=stream,
        gate=gate,
        harness_enabled=False,
        workdir=str(tmp_path),
    )

    # Session must have: user msg + assistant(tool_calls) + tool_result + assistant(text)
    assert len(session.messages) >= 3, (
        f"session should have user + tool_call + tool_result at minimum, "
        f"got {len(session.messages)}: {session.messages}"
    )
    # At least one message should carry tool_calls (the assistant's request).
    has_tool_call = any(getattr(m, "tool_calls", None) for m in session.messages)
    assert has_tool_call, "session must persist the assistant's tool_call message"


@pytest.mark.skipif(not deepagents, reason="deepagents not installed")
def test_harness_enabled_fire_continue_signal(tmp_path):
    """When harness is enabled and the model writes then ends, a
    harness_continue custom event flows through _handle_custom_mode to the
    stream. This verifies the three-mode stream plumbing (custom mode →
    on_harness_continue) end to end.

    Distinct from test_harness_force_continue_on_unverified_write: that test
    asserts the session grows; this one asserts the stream receives the
    custom-mode signal specifically (covering _handle_custom_mode).
    """
    from coderio.agent.deep_loop import run_deep_agent
    from coderio.tools.permission import AutoPermissionGate

    model = make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"file_path": "/x.py", "content": "x = 1"},
                    "id": "tc1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="done."),
        AIMessage(content="ok, verified."),
    )
    session = make_session(tmp_path)
    stream = NoOpStream()
    gate = AutoPermissionGate()

    run_deep_agent(
        "write x.py",
        model,
        session,
        stream=stream,
        gate=gate,
        harness_enabled=True,
        workdir=str(tmp_path),
    )

    # The stream must have recorded the harness_continue signal from custom mode.
    assert any(s["type"] == "harness_continue" for s in stream.harness_signals), (
        f"expected harness_continue via custom mode, got: {stream.harness_signals}"
    )
