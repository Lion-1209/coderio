"""Three-arm benchmark: coderio(harness ON) vs coderio(harness OFF) vs step-cli.

Same model (step-5-preview), same endpoint (StepFun coding plan, Anthropic
protocol), same key, same fixtures, same frozen judges. The arms isolate the
two questions the 6-task first round could not answer:

  1. harness ON vs OFF  — what does coderio's four-gate harness itself
     contribute (pass rate / speed / tokens) on identical tasks?
  2. coderio vs step-cli — how does the whole runtime compare against the
     provider's own agent, defaults against defaults?

Task set: 3 difficulty tiers, 20 tasks, all mechanically judged (pytest rc /
output match / answer-contains / frozen file-content checks). Tier 1 = the
agent-agnostic live-eval tasks; tiers 2-3 = scripts/live_eval/bench_tasks.py.

Metrics per task per arm: pass, wall time, tokens (coderio arms only —
step-cli's session store does not expose per-request usage; stated in the
report). Run (maintainer's own key):

    .venv/Scripts/python.exe -m scripts.live_eval.compare_step_cli
    .venv/Scripts/python.exe -m scripts.live_eval.compare_step_cli --only M3 H4
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coderio.config import load_config  # noqa: E402
from coderio.llm.factory import build_chat_model  # noqa: E402
from scripts.live_eval.bench_tasks import build_bench_tasks  # noqa: E402
from scripts.live_eval.tasks import JudgeContext, build_tasks  # noqa: E402

PROFILE = "阶跃 StepFun - Step 5 Preview"
STEP_CLI = Path.home() / ".step-cli" / "bin" / "step.cmd"
TIER1_IDS = ["A1", "A2", "A3", "B1", "C1", "C2"]
STEP_FLAGS = [
    "--provider",
    "anthropic",
    "--base-url",
    "https://api.stepfun.com/step_plan",
    "-m",
    "step-5-preview",
    "--tool-override",
    "write_file=allow",
    "--tool-override",
    "edit_file=allow",
    "--tool-override",
    "apply_patch=allow",
    "--tool-override",
    "run_command=allow",
    "--max-steps",
    "40",
]


def _plan_key() -> str:
    creds: dict[str, str] = {}
    cur = ""
    for line in (Path.home() / ".coderio" / "credentials").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            cur = line[1:-1]
        elif "=" in line and cur:
            k, v = line.split("=", 1)
            creds[f"{cur}.{k.strip()}"] = v.strip().strip('"')
    return creds["stepfun_coding_plan.key"]


def _run_coderio(task, model, harness: bool) -> dict:
    from scripts.live_eval.run import _run_task

    started = time.monotonic()
    r = _run_task(task, model, "step-5-preview", recursion_limit=60, harness_enabled=harness)
    r["elapsed_s"] = round(time.monotonic() - started, 1)
    return r


def _run_headless_cli(cmd: list[str], cwd: Path, timeout: float = 900) -> tuple[str, str]:
    """Run an external CLI capturing stdout, killing the whole TREE on timeout.

    subprocess.run(timeout=...) kills only the direct child on Windows — a
    node grandchild holding the stdout pipe then deadlocks the parent's
    final read (observed in the 2026-09-29 step-cli H6 run: recorded hang
    2258s for a 900s timeout). Popen + taskkill /T /F closes that hole.
    Returns (stdout, error); error is "" on a clean run.
    """
    import subprocess as sp

    proc = sp.Popen(cmd, cwd=str(cwd), stdout=sp.PIPE, stderr=sp.PIPE, text=True)  # noqa: S603 — benchmark arms by design
    try:
        out, err = proc.communicate(timeout=timeout)
        error = "" if proc.returncode == 0 else f"exit {proc.returncode}: {err[-300:]}"
        return out, error
    except sp.TimeoutExpired:
        # Tree-kill (Windows taskkill /T, POSIX killpg) BEFORE re-reading the
        # pipes, so no orphan can hold them.
        try:
            if sys.platform == "win32":
                sp.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)  # noqa: S603, S607 — tree-kill by design
            else:
                import os
                import signal

                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001 — best-effort; the hard read below still completes
            proc.kill()
        out, err = proc.communicate()
        return out or "", f"timeout after {timeout:.0f}s"


def _run_step_cli(task, key: str) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix=f"bench-stepcli-{task.id}-"))
    task.setup(workdir)
    started = time.monotonic()
    final_text, error = _run_headless_cli(
        [str(STEP_CLI), *STEP_FLAGS, "--api-key", key, "-w", str(workdir), task.prompt], workdir
    )
    elapsed = round(time.monotonic() - started, 1)

    ctx = JudgeContext(workdir=workdir, session=[], signals=[], final_text=final_text, model_name="step-5-preview")
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
        "final_text_head": final_text[:300],
        "usage": None,  # step-cli does not expose per-request usage in its session store
    }


def _run_dsh(task) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix=f"bench-dsh-{task.id}-"))
    task.setup(workdir)
    started = time.monotonic()
    # dsh headless works in the CWD; model comes from the profile's default
    # (stepfun / step-5-preview, configured in the user layer). shutil.which
    # resolves the npm .CMD shim — Popen cannot find bare "dsh" on Windows.
    import shutil

    dsh_bin = shutil.which("dsh") or "dsh"
    final_text, error = _run_headless_cli([dsh_bin, "--profile", "headless", task.prompt], workdir)
    elapsed = round(time.monotonic() - started, 1)

    ctx = JudgeContext(workdir=workdir, session=[], signals=[], final_text=final_text, model_name="step-5-preview")
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
        "final_text_head": final_text[:300],
        "usage": None,  # dsh does not expose per-request usage to the headless caller
    }


def main() -> int:
    only = []
    argv = sys.argv[1:]
    if "--only" in argv:
        only = [a for a in argv[argv.index("--only") + 1 :] if not a.startswith("--")]
    arms_sel = None
    if "--arms" in argv:
        arms_sel = [a for a in argv[argv.index("--arms") + 1 :] if not a.startswith("--")]
    merge_from = None
    if "--merge-from" in argv:
        merge_from = argv[argv.index("--merge-from") + 1]

    cfg = load_config()
    cfg.active_profile = PROFILE
    model = build_chat_model(cfg)
    key = _plan_key()

    tier1 = [t for t in build_tasks() if t.id in TIER1_IDS]
    bench = build_bench_tasks()
    tasks = tier1 + bench
    if only:
        wanted = {o.upper() for o in only}
        tasks = [t for t in tasks if t.id in wanted]
        if not tasks:
            print(f"no tasks matched --only {only}")
            return 2
    all_arms = ("coderio_on", "coderio_off", "step_cli", "dsh")
    run_arms = tuple(a for a in all_arms if arms_sel is None or a in arms_sel)
    print(f"[bench] model=step-5-preview | tasks={len(tasks)} | arms={','.join(run_arms)}")

    results = {a: [] for a in all_arms}
    # Merge prior results for arms NOT being run (e.g. run only the dsh arm
    # and combine with the saved 3-arm json).
    if merge_from and Path(merge_from).is_file():
        prior = json.loads(Path(merge_from).read_text(encoding="utf-8"))
        wanted_ids = {t.id for t in tasks}
        for a in all_arms:
            if a not in run_arms and a in prior:
                results[a] = [r for r in prior[a] if r["id"] in wanted_ids]
        print(f"[bench] merged prior arms: {[a for a in all_arms if a not in run_arms and results[a]]}")

    for t in tasks:
        print(f"\n=== {t.id} ({t.category}) {t.note}")
        for arm in run_arms:
            fn = {
                "coderio_on": lambda t=t: _run_coderio(t, model, harness=True),
                "coderio_off": lambda t=t: _run_coderio(t, model, harness=False),
                "step_cli": lambda t=t: _run_step_cli(t, key),
                "dsh": lambda t=t: _run_dsh(t),
            }[arm]
            r = fn()
            results[arm].append(r)
            tok = "-"
            if r.get("usage"):
                tok = f"{r['usage']['input_tokens']}i/{r['usage']['output_tokens']}o"
            print(
                f"  {arm:12s}: {'PASS' if r['passed'] else 'FAIL'} {r['elapsed_s']:>6}s {tok:>16s} {r['evidence'][:70]}"
            )
            if r["error"]:
                print(f"    error: {r['error'][:130]}")

    # ---- report
    out_dir = Path("docs/benchmarks")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.date.today().isoformat()
    by_id = {arm: {r["id"]: r for r in rs} for arm, rs in results.items() if rs}
    arms = [a for a in all_arms if by_id.get(a)]
    labels = {
        "coderio_on": "coderio harness ON",
        "coderio_off": "coderio harness OFF",
        "step_cli": "step-cli",
        "dsh": "dsh (deepseek-harness)",
    }

    def _tok(arm, tid):
        u = by_id[arm][tid].get("usage")
        return f"{u['input_tokens']}/{u['output_tokens']}" if u else "-"

    lines = [
        "# coderio (harness on/off) vs step-cli vs dsh — step-5-preview, same endpoint & key",
        "",
        f"- date: {stamp} | model: `step-5-preview` via StepFun coding plan (Anthropic protocol), all arms",
        "- coderio v0.6.0 default TurnSpec; the OFF arm is identical except harness_enabled=False",
        "- step-cli 1.0.0 shipped defaults + headless tool overrides + --max-steps 40",
        "- dsh 0.1.7-rc.2 headless profile, shipped defaults (stepfun provider, anthropic-messages)",
        f"- {len(tasks)} tasks / 3 tiers; judges frozen (pytest rc / output / answer-contains / content check)",
        "- tokens: coderio arms measured via engine usage (in/out);",
        "  step-cli / dsh do not expose per-request usage (stated)",
        "",
        "## Scoreboard",
        "",
    ]
    n = len(tasks)
    for arm in arms:
        p = sum(1 for r in results[arm] if r["passed"])
        tt = sum(r["elapsed_s"] for r in results[arm])
        ti = sum((r.get("usage") or {}).get("input_tokens", 0) for r in results[arm])
        to = sum((r.get("usage") or {}).get("output_tokens", 0) for r in results[arm])
        tok = f", tokens {ti}/{to}" if ti or to else ""
        lines.append(f"- **{labels[arm]}: {p}/{n}**, {tt:.0f}s total{tok}")
    lines += [
        "",
        "## Per tier",
        "",
        "| tier | tasks | " + " | ".join(labels[a] for a in arms) + " |",
        "|---|---|" + "---|" * len(arms),
    ]
    for tier, label in (("T1", "tier1 easy"), ("T2", "tier2 medium"), ("T3", "tier3 hard")):
        ids = [t.id for t in tasks if t.category == tier or (tier == "T1" and t.id in TIER1_IDS)]
        if not ids:
            continue
        row = [label + f" ({len(ids)})"]
        for arm in arms:
            p = sum(1 for i in ids if i in by_id[arm] and by_id[arm][i]["passed"])
            row.append(f"{p}/{len(ids)}")
        lines.append("| " + " | ".join(row) + " |")
    lines += [
        "",
        "## Per task",
        "",
        "| task | " + " | ".join(labels[a] for a in arms) + " | time per arm | tokens ON (i/o) |",
        "|---|" + "---|" * (len(arms) + 2),
    ]
    for t in tasks:
        c = ["✅" if by_id[a][t.id]["passed"] else "❌" for a in arms]
        times = "/".join(f"{by_id[a][t.id]['elapsed_s']:.0f}s" for a in arms)
        u = by_id["coderio_on"][t.id].get("usage") if "coderio_on" in by_id else None
        tok = f"{u['input_tokens']}/{u['output_tokens']}" if u else "-"
        lines.append(f"| {t.id} | " + " | ".join(c) + f" | {times} | {tok} |")
    lines += ["", "## Evidence", ""]
    for t in tasks:
        lines.append(f"### {t.id} — {t.note}")
        for arm in arms:
            if t.id not in by_id[arm]:
                continue
            r = by_id[arm][t.id]
            lines.append(
                f"- {arm}: {'PASS' if r['passed'] else 'FAIL'} — {r['evidence']}"
                + (f" | error: {r['error'][:150]}" if r["error"] else "")
            )
        lines.append("")

    md = out_dir / f"step5-preview-4arm-{stamp}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / f"step5-preview-4arm-{stamp}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nreport: {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
