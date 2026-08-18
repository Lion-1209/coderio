"""Tests for the user hooks system (agent/hooks.py + config wiring).

Unit tests exercise the real subprocess path (actual shell commands with
stdin JSON) — the whole point of hooks is crossing the process boundary, so
mocking it would test nothing. All commands are fast/dependency-free (echo,
exit, python -c reading stdin).
"""

from __future__ import annotations

import pytest

from coderio.agent.hooks import HookRunner, HooksMiddleware, HookSpec


def _runner(tmp_path, *specs, session_id="s1"):
    return HookRunner(list(specs), project_dir=str(tmp_path), session_id=session_id, permission_mode="full")


# ----------------------------------------------------- exit-code semantics


def test_exit_2_blocks_with_stderr_reason(tmp_path):
    r = _runner(tmp_path, HookSpec(event="PreToolUse", command="echo protected >&2; exit 2"))
    out = r.fire("PreToolUse", {"tool_name": "write_file", "tool_input": {}})
    assert out.blocked is True
    assert "protected" in out.reason


def test_exit_0_passes_and_no_context_for_tool_events(tmp_path):
    """stdout on exit 0 only injects context for UserPromptSubmit/SessionStart —
    a PreToolUse's stdout is ignored (a linter printing to stdout must not
    pollute anything)."""
    r = _runner(tmp_path, HookSpec(event="PreToolUse", command="echo loud-output"))
    out = r.fire("PreToolUse", {"tool_name": "x", "tool_input": {}})
    assert out.blocked is False
    assert out.context == ""
    assert out.error == ""


def test_exit_0_stdout_injects_context_for_prompt_event(tmp_path):
    r = _runner(tmp_path, HookSpec(event="UserPromptSubmit", command="echo use-bun-not-npm"))
    out = r.fire("UserPromptSubmit", {"prompt": "hi"})
    assert out.blocked is False
    assert "use-bun-not-npm" in out.context


def test_nonzero_exit_fails_open(tmp_path):
    """exit 1 = non-blocking error: surfaced in .error but NEVER blocks. This is
    the documented fail-open contract (hooks are glue, not a security gate)."""
    r = _runner(tmp_path, HookSpec(event="PreToolUse", command="echo oops >&2; exit 1"))
    out = r.fire("PreToolUse", {"tool_name": "x", "tool_input": {}})
    assert out.blocked is False
    assert "exit 1" in out.error or "exited 1" in out.error


def test_timeout_fails_open(tmp_path):
    r = _runner(tmp_path, HookSpec(event="PreToolUse", command="sleep 10", timeout=1))
    out = r.fire("PreToolUse", {"tool_name": "x", "tool_input": {}})
    assert out.blocked is False
    assert "timed out" in out.error


def test_missing_command_fails_open(tmp_path):
    r = _runner(tmp_path, HookSpec(event="PreToolUse", command="definitely-not-a-command-xyz"))
    out = r.fire("PreToolUse", {"tool_name": "x", "tool_input": {}})
    assert out.blocked is False
    assert "fail-open" in out.error


# ----------------------------------------------------- stdin contract


def test_hook_receives_json_on_stdin(tmp_path):
    """The full stdin contract: common fields + event-specific payload."""
    cmd = (
        'python -c "import sys,json; d=json.load(sys.stdin); '
        "assert d['session_id']=='s1'; assert d['hook_event_name']=='PreToolUse'; "
        "assert d['tool_name']=='write_file'; assert d['permission_mode']=='full'; "
        "print('ok')\""
    )
    r = _runner(tmp_path, HookSpec(event="PreToolUse", command=cmd))
    out = r.fire("PreToolUse", {"tool_name": "write_file", "tool_input": {"path": "/x"}})
    assert out.error == "", f"stdin contract violated: {out.error}"


def test_project_dir_env_var(tmp_path):
    r = _runner(tmp_path, HookSpec(event="PreToolUse", command='test "$CODERIO_PROJECT_DIR"'))
    out = r.fire("PreToolUse", {"tool_name": "x", "tool_input": {}})
    assert out.error == ""


# ----------------------------------------------------- matcher


def test_matcher_regex_filters_tools(tmp_path):
    r = _runner(tmp_path, HookSpec(event="PreToolUse", command="exit 2", matcher="write_file|edit_file"))
    assert r.fire("PreToolUse", {"tool_name": "write_file"}).blocked is True
    assert r.fire("PreToolUse", {"tool_name": "edit_file"}).blocked is True
    assert r.fire("PreToolUse", {"tool_name": "execute"}).blocked is False


def test_empty_matcher_matches_all(tmp_path):
    r = _runner(tmp_path, HookSpec(event="PreToolUse", command="exit 2"))
    for tool in ("write_file", "execute", "anything"):
        assert r.fire("PreToolUse", {"tool_name": tool}).blocked is True


def test_invalid_matcher_never_matches_never_crashes(tmp_path):
    r = _runner(tmp_path, HookSpec(event="PreToolUse", command="exit 2", matcher="[unclosed"))
    assert r.fire("PreToolUse", {"tool_name": "write_file"}).blocked is False


# ----------------------------------------------------- multiple hooks


def test_multiple_hooks_first_blocker_wins_but_all_run(tmp_path):
    """Serial execution: every hook runs (side effects are the point — logging
    hooks must fire even when the event is already denied); first exit-2 reason
    wins."""
    marker = tmp_path / "marker.txt"
    marker_posix = marker.as_posix()  # Git Bash treats backslashes as escapes
    r = _runner(
        tmp_path,
        HookSpec(event="PreToolUse", command=f"touch {marker_posix}"),  # side effect
        HookSpec(event="PreToolUse", command="echo first-deny >&2; exit 2"),
        HookSpec(event="PreToolUse", command="echo second-deny >&2; exit 2"),
    )
    out = r.fire("PreToolUse", {"tool_name": "x", "tool_input": {}})
    assert out.blocked is True
    assert "first-deny" in out.reason
    assert marker.is_file(), "later-blocked hooks must still run (side effects)"


# ----------------------------------------------------- HooksMiddleware


def _req(name, args, tc_id="tc1"):
    class _R:
        tool_call = {"name": name, "args": args, "id": tc_id}

    return _R()


def test_middleware_pretooluse_deny_short_circuits(tmp_path):
    r = _runner(
        tmp_path, HookSpec(event="PreToolUse", command="echo denied-by-policy >&2; exit 2", matcher="write_file")
    )
    mw = HooksMiddleware(r)
    called = []
    result = mw.wrap_tool_call(_req("write_file", {"path": "x"}), lambda req: called.append(1) or "WROTE")
    assert called == [], "denied call must NOT reach the tool"
    assert "Blocked by hook" in result.content
    assert "denied-by-policy" in result.content


def test_middleware_pretooluse_pass_through(tmp_path):
    r = _runner(tmp_path, HookSpec(event="PreToolUse", command="exit 0"))
    mw = HooksMiddleware(r)
    result = mw.wrap_tool_call(_req("write_file", {"path": "x"}), lambda req: "WROTE")
    assert result == "WROTE"


def test_middleware_posttooluse_exit2_appends_feedback(tmp_path):
    r = _runner(tmp_path, HookSpec(event="PostToolUse", command="echo run-the-linter >&2; exit 2"))
    mw = HooksMiddleware(r)
    result = mw.wrap_tool_call(_req("write_file", {"path": "x"}), lambda req: "WROTE")

    class _Msg:
        content = "WROTE"

    msg = _Msg()
    out = mw.wrap_tool_call(_req("write_file", {}), lambda req: msg)
    assert "[hook] run-the-linter" in out.content
    assert out.content.startswith("WROTE"), "original result preserved, feedback appended"


# ----------------------------------------------------- deep_loop integration


@pytest.mark.skipif(
    not __import__("importlib.util", fromlist=["util"]).find_spec("deepagents"), reason="deepagents not installed"
)
def test_user_prompt_submit_blocked_rejects_turn(tmp_path):
    """A blocking UserPromptSubmit hook rejects the prompt: no agent loop runs,
    the rejection lands in the session, and the return value explains it."""
    import sys as _sys

    _sys.path.insert(0, "tests")
    from agent.conftest import make_model, make_session  # noqa: PLC0415
    from langchain_core.messages import AIMessage

    from coderio.agent.deep_loop import run_deep_agent

    specs = [HookSpec(event="UserPromptSubmit", command="echo no-deploy-talk-allowed >&2; exit 2")]
    model = make_model(AIMessage(content="should never run"))
    session = make_session(tmp_path)

    result = run_deep_agent(
        "deploy to prod",
        model,
        session,
        harness_enabled=False,
        workdir=str(tmp_path),
        hooks=specs,
    )
    assert "rejected by hook" in result
    assert "no-deploy-talk-allowed" in result
    # The model never ran — the fake model's message must NOT be in the session.
    assert "should never run" not in " ".join(m.content for m in session.messages)


@pytest.mark.skipif(
    not __import__("importlib.util", fromlist=["util"]).find_spec("deepagents"), reason="deepagents not installed"
)
def test_user_prompt_submit_context_injected(tmp_path):
    """A pass hook's stdout rides into the turn as [hook context]."""
    import sys as _sys

    _sys.path.insert(0, "tests")
    from agent.conftest import make_model, make_session  # noqa: PLC0415
    from langchain_core.messages import AIMessage

    from coderio.agent.deep_loop import run_deep_agent

    specs = [HookSpec(event="UserPromptSubmit", command="echo always-use-bun")]
    model = make_model(AIMessage(content="ok"))
    session = make_session(tmp_path)

    run_deep_agent("do stuff", model, session, harness_enabled=False, workdir=str(tmp_path), hooks=specs)
    user_msgs = [m.content for m in session.messages if m.role == "user"]
    assert any("always-use-bun" in c for c in user_msgs), "hook context must land in the persisted user message"


@pytest.mark.skipif(
    not __import__("importlib.util", fromlist=["util"]).find_spec("deepagents"), reason="deepagents not installed"
)
def test_session_start_fires_once_per_session(tmp_path):
    """SessionStart injects on the first turn of a session only."""
    import sys as _sys

    _sys.path.insert(0, "tests")
    from agent.conftest import make_model, make_session  # noqa: PLC0415
    from langchain_core.messages import AIMessage

    from coderio.agent.deep_loop import run_deep_agent

    specs = [HookSpec(event="SessionStart", command="echo sprint-is-auth")]
    session = make_session(tmp_path)

    run_deep_agent(
        "t1", make_model(AIMessage(content="a")), session, harness_enabled=False, workdir=str(tmp_path), hooks=specs
    )
    first = [m.content for m in session.messages if m.role == "user"]
    run_deep_agent(
        "t2", make_model(AIMessage(content="b")), session, harness_enabled=False, workdir=str(tmp_path), hooks=specs
    )
    second = [m.content for m in session.messages if m.role == "user" and m.content != first[0]]

    assert any("sprint-is-auth" in c for c in first), "first turn gets SessionStart context"
    assert not any("sprint-is-auth" in c for c in second), "second turn must NOT re-inject"


# ----------------------------------------------------- config parsing


def test_config_hooks_parsed_and_survives_apply_env(tmp_path):

    from coderio.config import load_config

    proj = tmp_path / "proj" / ".coderio"
    proj.mkdir(parents=True)
    (proj / "config.toml").write_text(
        "[[hooks]]\nevent = 'PreToolUse'\nmatcher = 'write_file'\ncommand = 'exit 2'\ntimeout = 5\n"
        "\n[[hooks]]\nbad = 'no event'\n",
        encoding="utf-8",
    )
    cfg = load_config(search_from=tmp_path / "proj", user_dir=tmp_path / "user")
    assert len(cfg.hooks) == 1, "malformed entry skipped, valid one kept"
    assert cfg.hooks[0].event == "PreToolUse"
    assert cfg.hooks[0].matcher == "write_file"
    assert cfg.hooks[0].timeout == 5


def test_hook_runner_filters_unknown_events(tmp_path):
    """Specs with events outside HOOK_EVENTS are dropped at runner init —
    forward compatibility (a future event name in an old config is inert)."""
    r = _runner(tmp_path, HookSpec(event="SomeFutureEvent", command="exit 2"))
    assert r.specs == []


# ----------------------------------------------------- SEAM test (v3 audit P0)
# REGRESSION (2026-08-14 v3 report): TWO classes named HookSpec existed —
# config/models.py's (no .matches()) and agent/hooks.py's (with it). The
# loader produced the former; HookRunner.fire called .matches() on it → every
# PreToolUse/PostToolUse hook from a REAL config.toml crashed with
# AttributeError, while all 20 in-module tests stayed green because they
# imported agent.hooks.HookSpec directly. The seam — load_config's output
# feeding HookRunner — had ZERO coverage. This test crosses that seam: a
# config.toml loaded through the real parser must fire a tool event cleanly.


def test_seam_config_to_runner_tool_event_fires(tmp_path):
    """config.toml → load_config → HookRunner.fire("PreToolUse") → blocked.

    This is the exact path production takes (tui.py/run_cmd.py pass cfg.hooks
    straight into run_deep_agent). If a duplicate HookSpec ever reappears,
    this fails with the same AttributeError production hit.
    """
    from coderio.config import load_config

    proj = tmp_path / "proj" / ".coderio"
    proj.mkdir(parents=True)
    (proj / "config.toml").write_text(
        '[[hooks]]\nevent = "PreToolUse"\nmatcher = "write_file"\ncommand = "echo seam-reason >&2; exit 2"\n',
        encoding="utf-8",
    )
    cfg = load_config(search_from=tmp_path / "proj", user_dir=tmp_path / "user")

    # The loaded spec must BE the runtime class (single source of truth).
    from coderio.agent.hooks import HookSpec as RuntimeHookSpec

    assert isinstance(cfg.hooks[0], RuntimeHookSpec), (
        f"loader produced {type(cfg.hooks[0])} — HookRunner expects agent.hooks.HookSpec "
        "(duplicate-class regression, 2026-08-14 v3 P0)"
    )

    runner = HookRunner(cfg.hooks, project_dir=str(tmp_path), session_id="s1")
    out = runner.fire("PreToolUse", {"tool_name": "write_file", "tool_input": {}})
    assert out.blocked is True
    assert "seam-reason" in out.reason


def test_seam_config_to_runner_post_tool_event_fires(tmp_path):
    """PostToolUse from real config: matcher runs, exit 2 recorded (not crashed)."""
    from coderio.config import load_config

    proj = tmp_path / "proj" / ".coderio"
    proj.mkdir(parents=True)
    (proj / "config.toml").write_text(
        '[[hooks]]\nevent = "PostToolUse"\nmatcher = "edit_file"\ncommand = "run-the-linter >&2; exit 2"\n',
        encoding="utf-8",
    )
    cfg = load_config(search_from=tmp_path / "proj", user_dir=tmp_path / "user")
    runner = HookRunner(cfg.hooks, project_dir=str(tmp_path))
    out = runner.fire("PostToolUse", {"tool_name": "edit_file", "tool_input": {}})
    assert out.blocked is True
    assert "run-the-linter" in out.reason
    # Non-matching tool: matcher executes without crashing either.
    out2 = runner.fire("PostToolUse", {"tool_name": "execute", "tool_input": {}})
    assert out2.blocked is False


# ----------------------------------------------------- timeout latency guard (v3 P1)


def test_timeout_returns_promptly(tmp_path):
    """REGRESSION GUARD (v3 audit: a timeout=2 hook took 12s wall-clock). The
    old code drained pipes for 10s after the kill — waiting for an EOF that
    never comes when a pre-kill Windows grandchild holds the write end. The
    drain is now a 1s grace; total latency must stay near the timeout itself."""
    import time

    r = _runner(tmp_path, HookSpec(event="PreToolUse", command="sleep 10", timeout=2))
    start = time.time()
    out = r.fire("PreToolUse", {"tool_name": "x", "tool_input": {}})
    elapsed = time.time() - start
    assert out.blocked is False and "timed out" in out.error
    assert elapsed < 4, f"timeout=2 hook took {elapsed:.1f}s — the slow-drain regression is back (v3 measured 12s)"


# ----------------------------------------------------- per-event budget (v3 P1)


def test_event_budget_skips_remaining_hooks(tmp_path):
    """The per-event budget caps ALL matching hooks: when the first hook
    consumes the budget, the rest are skipped with an error note (fail-open)."""
    r = HookRunner(
        [
            HookSpec(event="PreToolUse", command="sleep 5", timeout=30),
            HookSpec(event="PreToolUse", command="exit 2"),
        ],
        project_dir=str(tmp_path),
        event_budget=2,
    )
    out = r.fire("PreToolUse", {"tool_name": "x", "tool_input": {}})
    assert "budget" in out.error, "budget exhaustion must be surfaced"
    # fail-open: budget exhaustion never blocks
    assert out.blocked is False


def test_budget_tightens_single_hook_timeout(tmp_path):
    """A hook's own timeout is min(spec.timeout, budget-remaining) — one slow
    hook can't outlive the event budget even with a generous per-hook timeout."""
    import time

    r = HookRunner(
        [HookSpec(event="PreToolUse", command="sleep 30", timeout=60)],
        project_dir=str(tmp_path),
        event_budget=2,
    )
    start = time.time()
    out = r.fire("PreToolUse", {"tool_name": "x", "tool_input": {}})
    elapsed = time.time() - start
    assert "timed out after 2s" in out.error, f"budget should tighten timeout to 2s: {out.error}"
    assert elapsed < 5


# ----------------------------------------------------- subagent hooks (v3 #12)


def test_general_purpose_subagent_carries_hooks_middleware(tmp_path):
    """task()-delegation must not bypass user hooks: the general-purpose
    subagent carries HooksMiddleware OUTERMOST (same order as the main agent)."""
    from coderio.agent.deep_loop import _build_general_purpose_subagent

    runner = _runner(tmp_path, HookSpec(event="PreToolUse", command="exit 0"))
    spec = _build_general_purpose_subagent(None, None, hook_runner=runner)
    mw = [type(m).__name__ for m in spec["middleware"]]
    assert "HooksMiddleware" in mw, f"subagent must carry hooks: {mw}"
    assert mw[0] == "HooksMiddleware", "hooks must be OUTERMOST (deny before permission prompts)"


def test_research_subagent_carries_hooks_middleware(tmp_path):
    """The read-only research subagent also carries HooksMiddleware (inserted
    before the tool whitelist)."""
    from coderio.agent.deep_loop import _build_research_subagent

    runner = _runner(tmp_path, HookSpec(event="PreToolUse", command="exit 0"))
    spec = _build_research_subagent(hook_runner=runner)
    mw = [type(m).__name__ for m in spec["middleware"]]
    assert "HooksMiddleware" in mw
    assert mw[0] == "HooksMiddleware"


def test_subagents_skip_hooks_middleware_when_no_specs(tmp_path):
    """No configured hooks → no middleware overhead on subagents."""
    from coderio.agent.deep_loop import _build_general_purpose_subagent

    empty = _runner(tmp_path)  # no specs
    spec = _build_general_purpose_subagent(None, None, hook_runner=empty)
    mw = [type(m).__name__ for m in spec["middleware"]]
    assert "HooksMiddleware" not in mw


# ----------------------------------------------------- user+project hooks merge (v3 P2)


def test_user_and_project_hooks_append_user_first(tmp_path):
    """REGRESSION GUARD (v3 P2): _merge replaces lists, so a repo's [[hooks]]
    silently DROPPED the user's protective hooks. Hooks append instead — user
    hooks FIRST (first-blocker-wins: the user's deny reason is what the model
    sees when both block)."""
    from coderio.config import load_config

    user = tmp_path / "user" / "config.toml"
    user.parent.mkdir(parents=True)
    user.write_text("[[hooks]]\nevent = 'UserPromptSubmit'\ncommand = 'echo user-hook'\n", encoding="utf-8")
    proj = tmp_path / "proj" / ".coderio" / "config.toml"
    proj.parent.mkdir(parents=True)
    proj.write_text("[[hooks]]\nevent = 'UserPromptSubmit'\ncommand = 'echo repo-hook'\n", encoding="utf-8")

    cfg = load_config(search_from=tmp_path / "proj", user_dir=tmp_path / "user")
    commands = [h.command for h in cfg.hooks]
    assert commands == ["echo user-hook", "echo repo-hook"], (
        f"both layers must survive, user first (first-blocker-wins priority); got {commands}"
    )
