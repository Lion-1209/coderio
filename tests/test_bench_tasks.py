"""Plumbing tests for the benchmark task set (NO API key, NO model).

Every fix/implement/refactor task's judge must FAIL on the raw fixture —
otherwise the benchmark could pass a turn that changed nothing. Pure-logic
tasks (H3/M2/M4/M8 are implement-to-tests) also fail on the stub by design.
"""

from __future__ import annotations

from pathlib import Path

from scripts.live_eval.bench_tasks import build_bench_tasks
from scripts.live_eval.tasks import JudgeContext


def _ctx(tmp_path: Path) -> JudgeContext:
    return JudgeContext(workdir=tmp_path, session=[], signals=[], final_text="", model_name="test")


def test_bench_set_shape():
    tasks = build_bench_tasks()
    ids = [t.id for t in tasks]
    assert len(tasks) == 14
    assert len(set(ids)) == 14
    assert sum(1 for t in tasks if t.category == "T2") == 8
    assert sum(1 for t in tasks if t.category == "T3") == 6
    for t in tasks:
        assert t.prompt.strip() and callable(t.setup) and callable(t.judge)


def test_every_judge_fails_on_raw_fixture(tmp_path):
    """The discrimination contract: untouched fixtures must NOT pass."""
    for t in build_bench_tasks():
        wd = tmp_path / t.id
        wd.mkdir()
        t.setup(wd)
        passed, evidence = t.judge(_ctx(wd))
        assert passed is False, f"{t.id}: raw fixture must fail the judge ({evidence})"


def test_judges_pass_on_hand_fixed_fixtures(tmp_path):
    """Spot-fix three representative fixtures by hand and confirm the judges
    go green — proving the target state is reachable, not impossible."""
    # M3: fix the off-by-one
    wd = tmp_path / "M3"
    wd.mkdir()
    t = {x.id: x for x in build_bench_tasks()}["M3"]
    t.setup(wd)
    (wd / "paginator.py").write_text(
        "def page(items, page_num, size):\n    start = (page_num - 1) * size\n    return items[start:start + size]\n",
        encoding="utf-8",
    )
    passed, _ = t.judge(_ctx(wd))
    assert passed

    # M6: ANY -> ALL
    wd = tmp_path / "M6"
    wd.mkdir()
    t = {x.id: x for x in build_bench_tasks()}["M6"]
    t.setup(wd)
    (wd / "scheduler.py").write_text(
        (wd / "scheduler.py")
        .read_text(encoding="utf-8")
        .replace("if any(d in done for d in deps):", "if all(d in done for d in deps):"),
        encoding="utf-8",
    )
    passed, _ = t.judge(_ctx(wd))
    assert passed

    # M5: dedup by hand -> both pytest AND the source check must pass
    wd = tmp_path / "M5"
    wd.mkdir()
    t = {x.id: x for x in build_bench_tasks()}["M5"]
    t.setup(wd)
    (wd / "report.py").write_text(
        "def _fmt_row(label, value):\n    return f'{label:<12} | {value:>8}'\n\n\n"
        "def render_summary(stats):\n    return '\\n'.join(_fmt_row(k, v) for k, v in stats.items())\n\n\n"
        "def render_diff(a, b):\n    return '\\n'.join(_fmt_row(k, f'{a[k]}->{b[k]}') for k in a)\n",
        encoding="utf-8",
    )
    passed, ev = t.judge(_ctx(wd))
    assert passed and "dedup confirmed" in ev


def test_m5_source_check_catches_unrefactored_pass():
    """Behavior tests pass on the raw M5 fixture (it is correct, just
    duplicated) — the file-content check is what makes it fail."""
    tasks = {t.id: t for t in build_bench_tasks()}
    assert tasks["M5"].note.startswith("dedup")
