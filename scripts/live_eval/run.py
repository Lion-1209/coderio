"""Live-eval runner: run the task set against a real provider, judge, report.

Usage (from the repo root):

    .venv/Scripts/python.exe -m scripts.live_eval.run                      # all 10 tasks
    .venv/Scripts/python.exe -m scripts.live_eval.run --only D1 D2 D3      # adversarial subset
    .venv/Scripts/python.exe -m scripts.live_eval.run --model glm-5.2      # override model name

The model comes from the USER'S OWN config (~/.coderio/config.toml + active
profile + credentials store) via coderio's own factory — the same resolution
the TUI uses. That is deliberate: the question the eval answers is "does the
harness work in a real user's real setup", not "does it work with a
hand-constructed client".

Cost control: each task is small (one turn, small files). The default is the
full 10-task set ON DEMAND — never scheduled — because the API key is the
maintainer's own money. Results record model name + provider per run.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root for `coderio` imports

from coderio.agent.deep_loop import TurnSpec, run_deep_agent  # noqa: E402
from coderio.config import load_config  # noqa: E402
from coderio.llm.factory import build_chat_model, resolved_model_name, resolved_provider_kind  # noqa: E402
from coderio.session.store import Session  # noqa: E402
from scripts.live_eval.tasks import JudgeContext, build_tasks  # noqa: E402


class RecordingStream:
    """Minimal StreamHandler that records harness signals + tool traffic.

    Implements the same surface as tests' NoOpStream: enough for
    run_deep_agent to stream without error, capturing exactly what the
    judges need (harness_continue / harness_warn / tool events).
    """

    def __init__(self) -> None:
        self.harness_signals: list[dict] = []
        self.tool_calls: list[str] = []
        self.finished = False

    def on_step_start(self, step: int = 1) -> None:  # noqa: D102
        pass

    def on_token(self, text: str) -> None:  # noqa: D102
        pass

    def on_thinking(self, text: str) -> None:  # noqa: D102
        pass

    def on_tool_start(self, name: str, args: dict, **kw) -> None:  # noqa: D102
        self.tool_calls.append(name)

    def on_tool_end(self, name: str, result: str) -> None:  # noqa: D102
        pass

    def on_finish(self) -> None:  # noqa: D102
        self.finished = True

    def on_turn_end(self, writes) -> None:  # noqa: D102
        pass

    def on_harness_continue(self, reason: str) -> None:  # noqa: D102
        self.harness_signals.append({"type": "harness_continue", "reason": reason})

    def on_harness_warn(self, message: str) -> None:  # noqa: D102
        self.harness_signals.append({"type": "harness_warn", "message": message})


class InterruptingStream(RecordingStream):
    """RecordingStream that flips is_interrupted() after N tool results.

    Reproduces the user's Esc exactly where the WhaleDock incident put it:
    mid-turn, right after a tool completes — the engine's abort poll then
    raises InterruptedError, run_deep_agent deletes the thread checkpoint
    (_handle_interrupt), and the NEXT turn is the amnesia-fix's acid test.
    """

    def __init__(self, flip_after_tools: int = 1) -> None:
        super().__init__()
        self._tools_done = 0
        self._flip_after = flip_after_tools
        self._interrupted = False

    def on_tool_end(self, name: str, result: str) -> None:  # noqa: D102
        super().on_tool_end(name, result)
        self._tools_done += 1
        if self._tools_done >= self._flip_after:
            self._interrupted = True

    def is_interrupted(self) -> bool:
        return self._interrupted


def _run_interrupt_resend_task(task, model, model_name: str, recursion_limit: int) -> dict:
    """The R1 flow: turn 1 (interrupted) → turn 2 (knowledge probe).

    Turn 1 embeds a conversation-only fact and is interrupted after its
    first tool result; turn 2 asks for the fact with tools forbidden. Under
    the full-history fallback the model answers from context; under the
    WhaleDock amnesia bug turn 2 never saw turn 1 and no tool can recover
    the fact — the judge's discrimination is airtight.
    """
    workdir = Path(tempfile.mkdtemp(prefix=f"eval-{task.id}-"))
    task.setup(workdir)
    session = Session.create(save_dir=workdir / ".sessions", meta={"model": model_name, "task": task.id})
    spec = TurnSpec(model=model, workdir=str(workdir), recursion_limit=recursion_limit)
    started = time.monotonic()
    error = ""
    interrupted = False

    s1 = InterruptingStream(flip_after_tools=1)
    try:
        run_deep_agent(task.prompt, spec, session, stream=s1)
    except InterruptedError:
        interrupted = True
    except Exception as e:  # noqa: BLE001 — a crash is a result, not a run abort
        error = f"turn1 {type(e).__name__}: {e}"

    msgs_before_t2 = len(session.messages)
    s2 = RecordingStream()
    final_text = ""
    if interrupted:
        try:
            final_text = run_deep_agent(task.prompt2, spec, session, stream=s2)
        except Exception as e:  # noqa: BLE001
            error = (error + " | " if error else "") + f"turn2 {type(e).__name__}: {e}"
    elapsed = round(time.monotonic() - started, 1)

    turn2_tools: list[str] = []
    for m in session.messages[msgs_before_t2:]:
        if getattr(m, "role", "") == "assistant" and getattr(m, "tool_calls", None):
            turn2_tools.extend(tc.name for tc in m.tool_calls)

    ctx = JudgeContext(
        workdir=workdir,
        session=session,
        signals=s2.harness_signals,
        final_text=final_text,
        model_name=model_name,
        interrupted=interrupted,
        turn2_tool_calls=turn2_tools,
    )
    try:
        passed, evidence = task.judge(ctx)
    except Exception as e:  # noqa: BLE001
        passed, evidence = False, f"judge error: {type(e).__name__}: {e}"

    return {
        "id": task.id,
        "category": task.category,
        "note": task.note,
        "passed": bool(passed),
        "evidence": evidence,
        "error": error,
        "elapsed_s": elapsed,
        "workdir": str(workdir),
        "harness_signals": s1.harness_signals + s2.harness_signals,
        "tool_calls": s1.tool_calls + s2.tool_calls,
        "ran_execute": ctx.ran_execute(),
        "final_text_head": final_text[:300],
        "interrupted": interrupted,
    }


def _run_task(task, model, model_name: str, recursion_limit: int) -> dict:
    """One task in a fresh workdir + fresh session. Returns a result record.

    The sandbox lives in the SYSTEM temp dir (never the results directory —
    task workdirs are scratch, reports are the artifact worth keeping).
    """
    if getattr(task, "flow", "") == "interrupt-resend":
        return _run_interrupt_resend_task(task, model, model_name, recursion_limit)
    workdir = Path(tempfile.mkdtemp(prefix=f"eval-{task.id}-"))
    task.setup(workdir)
    session = Session.create(save_dir=workdir / ".sessions", meta={"model": model_name, "task": task.id})
    stream = RecordingStream()
    started = time.monotonic()
    error = ""
    final_text = ""
    try:
        final_text = run_deep_agent(
            task.prompt,
            TurnSpec(model=model, workdir=str(workdir), recursion_limit=recursion_limit),
            session,
            stream=stream,
        )
    except Exception as e:  # noqa: BLE001 — a task crash is a RESULT, not a run abort
        error = f"{type(e).__name__}: {e}"
    elapsed = round(time.monotonic() - started, 1)

    ctx = JudgeContext(
        workdir=workdir,
        session=session,
        signals=stream.harness_signals,
        final_text=final_text,
        model_name=model_name,
    )
    try:
        passed, evidence = task.judge(ctx)
    except Exception as e:  # noqa: BLE001 — judge crash = failed task with evidence
        passed, evidence = False, f"judge error: {type(e).__name__}: {e}"

    return {
        "id": task.id,
        "category": task.category,
        "note": task.note,
        "passed": bool(passed),
        "evidence": evidence,
        "error": error,
        "elapsed_s": elapsed,
        "workdir": str(workdir),
        "harness_signals": stream.harness_signals,
        "tool_calls": stream.tool_calls,
        "ran_execute": ctx.ran_execute(),
        "final_text_head": final_text[:300],
    }


def _report_lines(results: list[dict], model_name: str, provider_kind: str) -> list[str]:
    lines = [
        "# coderio live eval results",
        "",
        f"- date: {_dt.date.today().isoformat()}",
        f"- model: `{model_name}` (provider kind: `{provider_kind}`)",
        f"- tasks: {sum(1 for r in results if r['passed'])}/{len(results)} passed",
        "",
        "| task | category | result | evidence |",
        "|---|---|---|---|",
    ]
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        ev = r["evidence"].replace("|", "\\|")[:120]
        lines.append(f"| {r['id']} | {r['category']} | {mark} | {ev} |")
    lines += [
        "",
        "Behavioral detail (harness signals = the gates actually firing):",
        "",
    ]
    for r in results:
        sigs = ", ".join(f"{s['type']}" for s in r["harness_signals"]) or "none"
        detail = f"tools={len(r['tool_calls'])} ran_execute={r['ran_execute']} {r['elapsed_s']}s"
        lines.append(f"- {r['id']}: signals=[{sigs}] {detail}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="coderio live eval (real provider, auto-judged)")
    ap.add_argument("--only", nargs="*", default=None, help="task ids to run (default: all)")
    ap.add_argument("--out", default=None, help="results directory (default: docs/live-eval)")
    ap.add_argument("--recursion-limit", type=int, default=60)
    args = ap.parse_args()

    cfg = load_config()
    model = build_chat_model(cfg)
    model_name = resolved_model_name(cfg)
    provider_kind = resolved_provider_kind(cfg)
    print(f"[provider] kind={provider_kind} model={model_name}")

    tasks = build_tasks()
    if args.only:
        wanted = {t.upper() for t in args.only}
        tasks = [t for t in tasks if t.id in wanted]
        if not tasks:
            print(f"no tasks matched --only {args.only}; available: {[t.id for t in build_tasks()]}")
            return 2
    print(f"[tasks] {[t.id for t in tasks]}")

    out_dir = Path(args.out) if args.out else Path("docs/live-eval")
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for t in tasks:
        print(f"\n=== {t.id} ({t.category}) {t.note}")
        r = _run_task(t, model, model_name, args.recursion_limit)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"--- {mark}: {r['evidence']}")
        if r["error"]:
            print(f"    error: {r['error']}")

    stamp = _dt.datetime.now().strftime("%Y-%m-%d")
    json_path = out_dir / f"results-{stamp}-{model_name}.json"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = out_dir / f"results-{stamp}-{model_name}.md"
    md_path.write_text("\n".join(_report_lines(results, model_name, provider_kind)) + "\n", encoding="utf-8")

    passed = sum(1 for r in results if r["passed"])
    print(f"\n=== {passed}/{len(results)} passed")
    print(f"report: {md_path}")
    print(f"raw:    {json_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
