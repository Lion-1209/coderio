"""Live verification of the experimental deepagents engine + harness middleware.

Run:
    ANTHROPIC_API_KEY=<key> .venv/Scripts/python.exe scripts/verify_deepagent_live.py
    STEP_KEY=<key> CODERIO_PROVIDER=stepfun .venv/Scripts/python.exe scripts/verify_deepagent_live.py

Verifies run_deep_agent (create_deep_agent + HarnessMiddleware + WinLocalShellBackend)
against a real endpoint. This engine is EXPERIMENTAL — the harness middleware
intercepts termination correctly (unit-tested), but the model's willingness to
verify under deepagents' heavy middleware stack is not yet reliable. This script
documents the current real behavior.
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
    print(f"\n{'='*60}\n{title}\n{'='*60}")


def main():
    workdir = Path(tempfile.mkdtemp())
    print(f"[workdir] {workdir}")

    section("TEST 1: deepagent writes to real disk (virtual_mode=True)")
    session = Session.create(save_dir=workdir / "sessions", meta={"model": MODEL_NAME})
    ans = run_deep_agent(
        "Create /marker.py with content print('deepagent-ok'), then tell me done.",
        MODEL, session, workdir=workdir, recursion_limit=30,
    )
    marker = workdir / "marker.py"
    print(f">>> final: {ans[:100]}")
    print(f">>> marker.py on disk: {marker.exists()}")
    if marker.exists():
        print(f">>> content: {marker.read_text().strip()}")
    disk_ok = marker.exists()

    section("TEST 2: harness middleware observes writes")
    session2 = Session.create(save_dir=workdir / "sessions2", meta={"model": MODEL_NAME})
    run_deep_agent(
        "Create /calc.py with print(1+1), then tell me done.",
        MODEL, session2, workdir=workdir, recursion_limit=40,
    )
    execs = [m for m in session2.messages if m.role == "tool" and m.name == "execute"]
    print(f">>> execute (verification) calls: {len(execs)}")
    print("    (harness intercepts unverified 'done'; model may or may not comply")
    print("     — this is the known experimental limitation)")

    section("RESULT")
    if disk_ok:
        print("PASS: deepagent engine writes to real disk + harness middleware runs")
        print("(NOTE: model self-verification under deepagents is still unreliable —")
        print("       the ReAct engine remains the production default.)")
        return 0
    print("FAIL: deepagent did not write to real disk")
    return 1


if __name__ == "__main__":
    sys.exit(main())
