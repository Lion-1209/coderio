"""Live verification of the single-agent harness state control.

Run:
    # GLM (default)
    ANTHROPIC_API_KEY=<key> .venv/Scripts/python.exe scripts/verify_harness_live.py
    # StepFun
    STEP_KEY=<key> CODERIO_PROVIDER=stepfun .venv/Scripts/python.exe scripts/verify_harness_live.py

Proves against a REAL model that the harness machinery (observation of real
tool_calls/results + termination interception + [harness] injection) works
end-to-end. This is the project's recurring failure mode — mock tests pass but a
real provider's streaming/content-blocks/stop-reasons behave differently.

Scenarios:
  TEST 1 (verify gate fires): tell the model to write a file and NOT run it.
        Expected: the model writes, claims done; the harness intercepts, injects
        a [harness] continuation, and the model then runs bash to verify.
  TEST 2 (verify gate passes): tell the model to write AND run the file.
        Expected: clean completion, no [harness] interception.
  TEST 3 (harness disabled): same as TEST 1 but harness_enabled=False.
        Expected: original soft-rule behavior — no interception (regression guard).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from langchain_anthropic import ChatAnthropic

from coderio.agent.loop import run_agent
from coderio.agent.prompts import ActiveSkills
from coderio.agent.stream import StreamHandler
from coderio.session.store import Session
from coderio.skills.store import SkillStore
from coderio.tools import build_default_tools
from coderio.tools.permission import PermissionGate

_PROVIDER = os.environ.get("CODERIO_PROVIDER", "glm").lower()
if _PROVIDER == "stepfun":
    KEY = os.environ.get("STEP_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""
    BASE = os.environ.get("CODERIO_BASE_URL", "https://api.stepfun.com/step_plan")
    MODEL_NAME = os.environ.get("CODERIO_MODEL", "step-3.7-flash")
else:
    KEY = os.environ.get("ANTHROPIC_API_KEY") or "REDACTED_API_KEY"
    BASE = os.environ.get("CODERIO_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
    MODEL_NAME = os.environ.get("CODERIO_MODEL", "GLM-5.2")

MODEL = ChatAnthropic(model=MODEL_NAME, base_url=BASE, api_key=KEY)
print(f"[provider] {_PROVIDER} | model={MODEL_NAME} | base={BASE}")


class PrintStream(StreamHandler):
    """Prints tool events + harness warnings so a human can watch the gate fire."""
    def __init__(self):
        self.warnings = []

    def on_token(self, text): pass

    def on_tool_start(self, name, args):
        preview = str(args)
        if len(preview) > 90:
            preview = preview[:90] + "…"
        print(f"    [tool start] {name}({preview})")

    def on_tool_end(self, name, result):
        preview = result.replace("\n", " ")[:90]
        print(f"    [tool end]   {name} -> {preview}")

    def on_finish(self): pass

    def on_harness_warn(self, message):
        self.warnings.append(message)
        print(f"    ⚠ [HARNESS WARN] {message}")


def section(title):
    print(f"\n{'='*64}\n{title}\n{'='*64}")


def _harness_msgs(session) -> list:
    return [m for m in session.messages if m.role == "user" and "[harness]" in m.content]


def _ran_bash(session) -> bool:
    return any(m.role == "tool" and m.name == "bash" for m in session.messages)


def test_verify_gate_fires(tmp):
    """The natural failure mode: model writes code and forgets to verify.

    We use a NATURAL prompt (no "don't run it" sabotage). The agent, left to
    itself, tends to write-then-summarize. The harness must catch that and force
    a bash run — OR escalate to a visible warning if the model keeps refusing.
    Either way, an unverified write must NEVER pass silently.
    """
    section("TEST 1: verify gate fires (natural write-then-summarize)")
    os.chdir(tmp)
    session = Session.create(save_dir=tmp / "sessions", meta={"model": MODEL_NAME})
    stream = PrintStream()
    final = run_agent(
        "在当前目录创建 hello.py，内容是 print('hello-harness')，写好就告诉我完成了。",
        MODEL, build_default_tools(), PermissionGate("auto"),
        SkillStore(), ActiveSkills(), session, stream, max_rounds=12,
    )
    print(f"\n>>> FINAL: {final[:200]}")
    hmsg = _harness_msgs(session)
    ran = _ran_bash(session)
    print(f"    [harness injections] {len(hmsg)}  [ran bash] {ran}  [warnings] {len(stream.warnings)}")
    assert (tmp / "hello.py").is_file(), "file must have been written"
    assert "hello-harness" in (tmp / "hello.py").read_text(encoding="utf-8")
    # Core invariant: an unverified write is never allowed to finish silently.
    # Two acceptable outcomes: (a) harness forced a bash run, or (b) the gate
    # exhausted retries and released with a LOUD warning (never silent).
    assert ran or stream.warnings, (
        "UNVERIFIED CODE PASSED SILENTLY. Neither bash ran nor a warning was "
        f"emitted. harness_msgs={len(hmsg)} warnings={len(stream.warnings)}")
    if ran:
        print("    PASS: code was verified (bash ran) — the snake-game failure mode is fixed")
    else:
        print("    PASS: gate escalated to a visible warning (model refused, but not silent)")
    return session


def test_verify_gate_passes(tmp):
    """Write + run passes cleanly with no harness interception."""
    section("TEST 2: verify gate passes (write then run)")
    os.chdir(tmp)
    session = Session.create(save_dir=tmp / "sessions", meta={"model": MODEL_NAME})
    stream = PrintStream()
    final = run_agent(
        "在当前目录创建 greet.py，内容是 print('greetings')，然后用 bash 运行它确认输出。",
        MODEL, build_default_tools(), PermissionGate("auto"),
        SkillStore(), ActiveSkills(), session, stream, max_rounds=12,
    )
    print(f"\n>>> FINAL: {final[:200]}")
    assert (tmp / "greet.py").is_file()
    assert _ran_bash(session), "model should have run the file"
    assert _harness_msgs(session) == [], "no interception expected when code is run"
    assert stream.warnings == [], "no warning expected when code is run"
    print("    PASS: clean completion, no harness interception")


def test_harness_disabled(tmp):
    """harness_enabled=False keeps original behavior even on a write-and-skip task."""
    section("TEST 3: harness disabled (regression guard — original behavior)")
    os.chdir(tmp)
    session = Session.create(save_dir=tmp / "sessions", meta={"model": MODEL_NAME})
    stream = PrintStream()
    run_agent(
        "在当前目录创建 skip.py，内容是 print('x')，写好就告诉我完成了。",
        MODEL, build_default_tools(), PermissionGate("auto"),
        SkillStore(), ActiveSkills(), session, stream, max_rounds=12,
        harness_enabled=False,
    )
    assert (tmp / "skip.py").is_file()
    assert _harness_msgs(session) == [], "harness disabled must never inject"
    assert stream.warnings == [], "harness disabled must never warn"
    print("    PASS: harness disabled = no intervention (original soft-rule behavior)")


def test_tool_error_does_not_kill_turn(tmp):
    """A tool call with bad args (the bash(path=...) regression) must NOT crash
    the turn. The error becomes a tool result, the model self-corrects, and the
    turn completes. Only base-model API errors are fatal."""
    section("TEST 4: tool error resilience (bad args don't kill the turn)")
    os.chdir(tmp)
    session = Session.create(save_dir=tmp / "sessions", meta={"model": MODEL_NAME})
    stream = PrintStream()
    # Bait the model into verifying a file. Models frequently invent a `path`
    # kwarg for bash (which only takes `cwd`); that used to crash the whole turn.
    final = run_agent(
        "在当前目录创建 check.py 内容是 print('tool-error-resilience')，"
        "然后用 bash 在 path='.' 下运行它确认输出。",
        MODEL, build_default_tools(), PermissionGate("auto"),
        SkillStore(), ActiveSkills(), session, stream, max_rounds=14,
    )
    print(f"\n>>> FINAL: {final[:200]}")
    assert (tmp / "check.py").is_file()
    # The turn must have COMPLETED (returned a final string), not raised.
    assert isinstance(final, str) and final, "turn must complete, not crash"
    # And there must be NO raw Python traceback leaked into any message.
    for m in session.messages:
        assert "TypeError" not in (m.content or ""), \
            "a raw TypeError leaked into the conversation — tool error not caught"
    # If bash ran at all (even with a bad first arg), the model self-corrected.
    bash_results = [m for m in session.messages if m.role == "tool" and m.name == "bash"]
    if any("rejected" in (r.content or "") for r in bash_results):
        print("    [observed] model tried bash with bad args, got a structured error result")
    assert _ran_bash(session), "model should eventually run bash (self-correcting)"
    print("    PASS: tool error became a result; turn completed; model self-corrected")


def main():
    tmp = Path(tempfile.mkdtemp())
    print(f"[workdir] {tmp}")
    try:
        test_verify_gate_fires(tmp)
        test_verify_gate_passes(tmp)
        test_harness_disabled(tmp)
        test_tool_error_does_not_kill_turn(tmp)
        section("ALL HARNESS LIVE TESTS PASSED")
        return 0
    except AssertionError as e:
        print(f"\n!!! FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
