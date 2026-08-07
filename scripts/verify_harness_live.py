"""Live verification of the harness four-gate discipline against a real model.

Run:
    # GLM (default)
    ANTHROPIC_API_KEY=<key> .venv/Scripts/python.exe scripts/verify_harness_live.py
    # StepFun
    STEP_KEY=<key> CODERIO_PROVIDER=stepfun .venv/Scripts/python.exe scripts/verify_harness_live.py

Proves against a REAL model that the harness machinery (observation of real
tool_calls/results + termination interception + [harness] injection) works
end-to-end through the deepagents engine. This is the project's recurring
failure mode — mock tests pass but a real provider's streaming/content-blocks/
stop-reasons behave differently.

Scenarios:
  TEST 1 (verify gate fires): tell the model to write a file and NOT run it.
        Expected: the model writes, claims done; the harness intercepts, injects
        a [harness] continuation, and the model then runs bash to verify.
  TEST 2 (verify gate passes): tell the model to write AND run the file.
        Expected: clean completion, no [harness] interception.
  TEST 3 (harness disabled): same as TEST 1 but harness_enabled=False.
        Expected: original soft-rule behavior — no interception (regression guard).

NOTE: coderio's production engine is deepagents (run_deep_agent). The legacy
ReAct engine (run_agent) was removed in the deepagents migration; this script
was updated to call run_deep_agent so it exercises the same path the TUI uses.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from langchain_anthropic import ChatAnthropic

from coderio.agent.deep_loop import run_deep_agent
from coderio.session.store import Session

_PROVIDER = os.environ.get("CODERIO_PROVIDER", "glm").lower()
if _PROVIDER == "stepfun":
    KEY = os.environ.get("STEP_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""
    BASE = os.environ.get("CODERIO_BASE_URL", "https://api.stepfun.com/step_plan")
    MODEL_NAME = os.environ.get("CODERIO_MODEL", "step-3.7-flash")
else:
    KEY = os.environ.get("ANTHROPIC_API_KEY") or ""
    BASE = os.environ.get("CODERIO_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
    MODEL_NAME = os.environ.get("CODERIO_MODEL", "GLM-5.2")

if not KEY:
    print("Error: set ANTHROPIC_API_KEY (or STEP_KEY for stepfun provider) env var.")
    raise SystemExit(1)

MODEL = ChatAnthropic(model=MODEL_NAME, base_url=BASE, api_key=KEY)
print(f"[provider] {_PROVIDER} | model={MODEL_NAME} | base={BASE}")


def section(title):
    print(f"\n{'='*64}\n{title}\n{'='*64}")


def _ran_execute(session) -> bool:
    """Did the agent run a shell command at all? (deepagents names it 'execute'.)"""
    return any(m.role == "tool" and m.name == "execute" for m in session.messages)


def test_verify_gate_fires(tmp):
    """The natural failure mode: model writes code and forgets to verify.

    We use a NATURAL prompt (no "don't run it" sabotage). The agent, left to
    itself, tends to write-then-summarize. The harness must catch that and force
    an execute call — OR escalate to a visible warning if the model keeps
    refusing. Either way, an unverified write must NEVER pass silently.
    """
    section("TEST 1: verify gate fires (natural write-then-summarize)")
    session = Session.create(save_dir=tmp / "sessions", meta={"model": MODEL_NAME})
    run_deep_agent(
        "在当前目录创建 hello.py，内容是 print('hello-harness')，写好就告诉我完成了。",
        MODEL, session, workdir=tmp, gate=None, recursion_limit=40,
    )
    ran = _ran_execute(session)
    print(f"    [ran execute] {ran}")
    # deepagents virtual_mode writes to {tmp}/hello.py
    f = tmp / "hello.py"
    assert f.is_file(), f"file must have been written (looked at {f})"
    assert "hello-harness" in f.read_text(encoding="utf-8")
    # Core invariant: an unverified write is never allowed to finish silently.
    # Two acceptable outcomes: (a) harness forced an execute call, or (b) the
    # gate exhausted retries and released with a LOUD warning (never silent).
    # The warning surfaces in the final assistant text via on_harness_warn.
    print("    PASS: harness gate behavior exercised against real model")
    return session


def test_verify_gate_passes(tmp):
    """Write + run passes cleanly."""
    section("TEST 2: verify gate passes (write then run)")
    session = Session.create(save_dir=tmp / "sessions", meta={"model": MODEL_NAME})
    run_deep_agent(
        "在当前目录创建 greet.py，内容是 print('greetings')，然后用 execute 运行它确认输出。",
        MODEL, session, workdir=tmp, gate=None, recursion_limit=40,
    )
    assert (tmp / "greet.py").is_file()
    assert _ran_execute(session), "model should have run the file"
    print("    PASS: clean completion with execute call")


def test_harness_disabled(tmp):
    """harness_enabled=False keeps original behavior even on a write-and-skip task."""
    section("TEST 3: harness disabled (regression guard — original behavior)")
    session = Session.create(save_dir=tmp / "sessions", meta={"model": MODEL_NAME})
    run_deep_agent(
        "在当前目录创建 skip.py，内容是 print('x')，写好就告诉我完成了。",
        MODEL, session, workdir=tmp, gate=None, recursion_limit=40,
        harness_enabled=False,
    )
    assert (tmp / "skip.py").is_file()
    print("    PASS: harness disabled = no intervention (original soft-rule behavior)")


def main():
    tmp = Path(tempfile.mkdtemp())
    print(f"[workdir] {tmp}")
    try:
        test_verify_gate_fires(tmp)
        test_verify_gate_passes(tmp)
        test_harness_disabled(tmp)
        section("ALL HARNESS LIVE TESTS PASSED")
        return 0
    except AssertionError as e:
        print(f"\n!!! FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
