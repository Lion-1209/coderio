"""Live verification of the crew (LangGraph) pipeline against a real model.

Run:
    ANTHROPIC_API_KEY=<key> .venv/Scripts/python.exe scripts/verify_crew_live.py
    STEP_KEY=<key> CODERIO_PROVIDER=stepfun .venv/Scripts/python.exe scripts/verify_crew_live.py

Verifies the 6-agent crew (clarify→spec→task→execute→verify→commit) runs
end-to-end via the LangGraph StateGraph against a real endpoint. This is the
critical test for the orchestrator refactor — mock tests can't catch graph
routing / interrupt / fix-loop issues that only surface with real model output.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from langchain_anthropic import ChatAnthropic

from coderio.crew.orchestrator import CrewOrchestrator
from coderio.skills.store import load_skill_store
from coderio.tools.permission import AutoPermissionGate
from coderio.agent.stream import NullStream

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


class PrintStream(NullStream):
    def on_tool_end(self, name, result):
        preview = result.replace("\n", " ")[:80]
        print(f"  [{name}] {preview}")


def main():
    workdir = Path(tempfile.mkdtemp())
    os.chdir(workdir)
    store = load_skill_store(Path(__file__).resolve().parent.parent / "src" / "coderio" / "skills", None, None)
    print(f"[workdir] {workdir}")

    section("TEST: crew 6-agent pipeline end-to-end (auto mode)")
    orch = CrewOrchestrator(
        model=MODEL, store=store, gate=AutoPermissionGate(),
        stream=PrintStream(), auto_mode=True, max_rounds=4,
    )
    state = orch.run("在当前目录创建 hi.py 内容是 print(42)，这是测试 crew 流水线")

    print(f"\n>>> clarification: {state.clarification[:80]}")
    print(f">>> spec: {state.spec[:80]}")
    print(f">>> implementation: {state.implementation[:80]}")
    print(f">>> verification: {state.verification[:80]}")
    print(f">>> commit_message: {state.commit_message[:80]}")
    print(f">>> final stage: {state.current_stage}")

    ok = True
    if not state.clarification:
        print("FAIL: clarification empty"); ok = False
    if not state.commit_message:
        print("FAIL: commit_message empty"); ok = False
    if state.current_stage != "commit":
        print(f"FAIL: did not reach commit stage (at {state.current_stage})"); ok = False

    if ok:
        section("PASS: crew pipeline ran all 6 stages end-to-end")
        return 0
    section("FAIL: crew pipeline incomplete")
    return 1


if __name__ == "__main__":
    sys.exit(main())
