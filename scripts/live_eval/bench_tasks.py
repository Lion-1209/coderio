"""Benchmark task set: 20 tasks in 3 difficulty tiers, agent-agnostic.

Tier 1 (easy)  = the 6 agent-agnostic tasks from the live-eval suite
                 (A1-A3, B1, C1-C2) — imported, not duplicated.
Tier 2 (M1-M8) = single-module work with real engineering texture:
                 cross-file refactor, implement-to-tests, boundary bugs,
                 state machines, I/O error handling, config semantics.
Tier 3 (H1-H6) = multi-file features, classic Python pitfalls, a timed
                 performance fix, and an API migration — tasks where
                 multi-step discipline compounds.

Every task is judged ONLY by frozen mechanical criteria (pytest exit code,
concrete output, file-content checks). No prose grading. The judge runs the
workdir's own tests — the same judge for every arm, so the comparison
between agents is fair by construction.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts.live_eval.tasks import JudgeContext, Task


def _pytest_judge(ctx: JudgeContext) -> tuple[bool, str]:
    """The workdir's own test suite decides (frozen before the run)."""
    r = ctx.run([sys.executable, "-m", "pytest", "-q"])
    tail = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else (r.stderr or "")[-200:]
    return r.returncode == 0, f"pytest rc={r.returncode} :: {tail}"


# ------------------------------------------------------------------ tier 2 fixtures
def _fx_m1(wd: Path) -> None:
    (wd / "geometry.py").write_text(
        "import math\n\n\ndef circle_area(r):\n    return math.pi * r * r\n",
        encoding="utf-8",
    )
    (wd / "shapes.py").write_text(
        "from geometry import circle_area\n\n\ndef describe(r):\n    return f'area={circle_area(r):.2f}'\n",
        encoding="utf-8",
    )
    (wd / "test_m1.py").write_text(
        "from geometry import disk_area\nfrom shapes import describe\nimport math\n\n"
        "def test_rename():\n    assert abs(disk_area(1) - math.pi) < 1e-9\n\n"
        "def test_caller_updated():\n    assert describe(1) == 'area=3.14'\n",
        encoding="utf-8",
    )


def _fx_m2(wd: Path) -> None:
    (wd / "string_utils.py").write_text("# 在此实现 slugify\n", encoding="utf-8")
    (wd / "test_m2.py").write_text(
        "from string_utils import slugify\n\n"
        "def test_basic():\n    assert slugify('Hello World') == 'hello-world'\n\n"
        "def test_punct_and_case():\n    assert slugify('Hello, World!') == 'hello-world'\n\n"
        "def test_squeeze_spaces():\n    assert slugify('Multiple   Spaces') == 'multiple-spaces'\n\n"
        "def test_upper():\n    assert slugify('UPPER') == 'upper'\n",
        encoding="utf-8",
    )


def _fx_m3(wd: Path) -> None:
    (wd / "paginator.py").write_text(
        "def page(items, page_num, size):\n"
        "    # page_num is 1-based. Pages past the end are empty.\n"
        "    start = page_num * size\n"
        "    return items[start:start + size]\n",
        encoding="utf-8",
    )
    (wd / "test_m3.py").write_text(
        "from paginator import page\n\n"
        "data = list(range(10))\n\n"
        "def test_first_page():\n    assert page(data, 1, 3) == [0, 1, 2]\n\n"
        "def test_last_partial_page():\n    assert page(data, 4, 3) == [9]\n\n"
        "def test_past_end_is_empty():\n    assert page(data, 5, 3) == []\n",
        encoding="utf-8",
    )


def _fx_m4(wd: Path) -> None:
    (wd / "turnstile.py").write_text("# 在此实现 Turnstile\n", encoding="utf-8")
    (wd / "test_m4.py").write_text(
        "from turnstile import Turnstile\n\n"
        "def test_starts_locked():\n    t = Turnstile()\n    assert t.locked is True\n\n"
        "def test_coin_unlocks():\n    t = Turnstile()\n    t.coin()\n    assert t.locked is False\n\n"
        "def test_push_through_when_unlocked():\n    t = Turnstile()\n    t.coin()\n    t.push()\n    assert t.locked is True\n\n"
        "def test_push_locked_raises_alarm():\n    t = Turnstile()\n    t.push()  # should raise AlarmError and stay locked\n",
        encoding="utf-8",
    )


def _fx_m5(wd: Path) -> None:
    dup = (
        "def _fmt_row(label, value):\n"
        "    return f'{label:<12} | {value:>8}'\n"
    )
    (wd / "report.py").write_text(
        dup
        + "\n\ndef render_summary(stats):\n"
        "    lines = [_fmt_row(k, v) for k, v in stats.items()]\n"
        "    return '\\n'.join(lines)\n\n"
        + dup
        + "\n\ndef render_diff(a, b):\n"
        "    lines = [_fmt_row(k, f'{a[k]}->{b[k]}') for k in a]\n"
        "    return '\\n'.join(lines)\n",
        encoding="utf-8",
    )
    (wd / "test_m5.py").write_text(
        "from report import render_summary, render_diff\n\n"
        "def test_summary():\n    assert 'x' in render_summary({'x': 1})\n\n"
        "def test_diff():\n    assert 'a' in render_diff({'a': 1}, {'a': 2})\n",
        encoding="utf-8",
    )


def _fx_m6(wd: Path) -> None:
    (wd / "scheduler.py").write_text(
        "def order(tasks):\n"
        "    \"\"\"tasks: {name: [deps]}. Return a valid execution order (deps first).\n"
        "    Deterministic: alphabetical among ready tasks.\"\"\"\n"
        "    done, out = set(), []\n"
        "    remaining = dict(tasks)\n"
        "    while remaining:\n"
        "        progressed = False\n"
        "        for name in sorted(remaining):\n"
        "            deps = remaining[name]\n"
        "            # BUG: treats a task as ready when ANY dep is done\n"
        "            if any(d in done for d in deps):\n"
        "                out.append(name)\n"
        "                done.add(name)\n"
        "                del remaining[name]\n"
        "                progressed = True\n"
        "                break\n"
        "        if not progressed:\n"
        "            raise ValueError('cycle')\n"
        "    return out\n",
        encoding="utf-8",
    )
    (wd / "test_m6.py").write_text(
        "from scheduler import order\n\n"
        "def test_two_deps():\n"
        "    assert order({'c': ['a', 'b'], 'a': [], 'b': []}) == ['a', 'b', 'c']\n\n"
        "def test_chain():\n"
        "    assert order({'c': ['b'], 'b': ['a'], 'a': []}) == ['a', 'b', 'c']\n",
        encoding="utf-8",
    )


def _fx_m7(wd: Path) -> None:
    (wd / "reader.py").write_text(
        "def read_scores(path):\n"
        "    # 每行 'name:score'。文件不存在返回空 dict；无法解析的行跳过。\n"
        "    with open(path) as f:\n"
        "        out = {}\n"
        "        for line in f:\n"
        "            name, score = line.strip().split(':')\n"
        "            out[name] = int(score)\n"
        "        return out\n",
        encoding="utf-8",
    )
    (wd / "test_m7.py").write_text(
        "from reader import read_scores\n\n"
        "def test_missing_file(tmp_path):\n    assert read_scores(tmp_path / 'nope.txt') == {}\n\n"
        "def test_malformed_lines_skipped(tmp_path):\n"
        "    p = tmp_path / 's.txt'\n"
        "    p.write_text('a:1\\nbadline\\nb:2\\n', encoding='utf-8')\n"
        "    assert read_scores(p) == {'a': 1, 'b': 2}\n",
        encoding="utf-8",
    )


def _fx_m8(wd: Path) -> None:
    (wd / "conf.py").write_text("# 在此实现 load_config\n", encoding="utf-8")
    (wd / "test_m8.py").write_text(
        "from conf import load_config\n\n"
        "def test_missing_key_uses_default(tmp_path):\n"
        "    p = tmp_path / 'c.txt'\n"
        "    p.write_text('a=1\\n', encoding='utf-8')\n"
        "    cfg = load_config(p, defaults={'a': 0, 'b': 'x'})\n"
        "    assert cfg == {'a': 1, 'b': 'x'}\n\n"
        "def test_int_coercion(tmp_path):\n"
        "    p = tmp_path / 'c.txt'\n"
        "    p.write_text('n=42\\ns=hello\\n', encoding='utf-8')\n"
        "    cfg = load_config(p, defaults={})\n"
        "    assert cfg['n'] == 42 and isinstance(cfg['n'], int)\n"
        "    assert cfg['s'] == 'hello'\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------------ tier 3 fixtures
def _fx_h1(wd: Path) -> None:
    (wd / "app.py").write_text(
        "def compute(op, a, b):\n"
        "    if op == 'add':\n"
        "        return a + b\n"
        "    if op == 'mul':\n"
        "        return a * b\n"
        "    raise ValueError(op)\n",
        encoding="utf-8",
    )
    (wd / "runner.py").write_text(
        "from app import compute\n\n\ndef run(a, b):\n    return compute('add', a, b)\n",
        encoding="utf-8",
    )
    (wd / "test_h1.py").write_text(
        "import logging\n\nimport pytest\n\nfrom app import compute\nfrom runner import run\n\n"
        "@pytest.fixture\ndef cap(caplog):\n    with caplog.at_level(logging.INFO, logger='app'):\n        yield caplog\n\n"
        "def test_app_logs_computations(cap):\n"
        "    compute('add', 1, 2)\n"
        "    assert any('add' in r.message for r in cap.records)\n\n"
        "def test_runner_logs_run(cap):\n"
        "    run(1, 2)\n"
        "    assert any('run' in r.message for r in cap.records)\n",
        encoding="utf-8",
    )


def _fx_h2(wd: Path) -> None:
    (wd / "basket.py").write_text(
        "def add_item(item, items=[]):\n"
        "    items.append(item)\n"
        "    return items\n",
        encoding="utf-8",
    )
    (wd / "test_h2.py").write_text(
        "from basket import add_item\n\n"
        "def test_no_shared_state():\n"
        "    assert add_item('a') == ['a']\n"
        "    assert add_item('b') == ['b']  # classic mutable-default leak\n\n"
        "def test_explicit_list_still_works():\n"
        "    out = []\n"
        "    add_item('c', out)\n"
        "    assert out == ['c']\n",
        encoding="utf-8",
    )


def _fx_h3(wd: Path) -> None:
    (wd / "calc.py").write_text("# 在此实现 evaluate\n", encoding="utf-8")
    (wd / "test_h3.py").write_text(
        "import pytest\n\nfrom calc import evaluate\n\n"
        "def test_arith():\n    assert evaluate('1 + 2 * 3') == 7\n\n"
        "def test_vars():\n"
        "    evaluate('x = 5')\n"
        "    assert evaluate('x * 2') == 10\n\n"
        "def test_parens_with_var():\n"
        "    evaluate('y = 4')\n"
        "    assert evaluate('(y + 2) / 2') == 3\n\n"
        "def test_unknown_var_raises():\n"
        "    with pytest.raises(KeyError):\n        evaluate('zzz + 1')\n",
        encoding="utf-8",
    )


def _fx_h4(wd: Path) -> None:
    (wd / "dedupe.py").write_text(
        "def unique(items):\n"
        "    # 保持首次出现顺序去重\n"
        "    out = []\n"
        "    for x in items:\n"
        "        if x not in out:\n"
        "            out.append(x)\n"
        "    return out\n",
        encoding="utf-8",
    )
    (wd / "test_h4.py").write_text(
        "import time\n\nfrom dedupe import unique\n\n"
        "def test_correct():\n    assert unique([3, 1, 3, 2, 1]) == [3, 1, 2]\n\n"
        "def test_fast_on_large_input():\n"
        "    data = list(range(20000)) * 3\n"
        "    t0 = time.monotonic()\n"
        "    out = unique(data)\n"
        "    elapsed = time.monotonic() - t0\n"
        "    assert out == list(range(20000))\n"
        "    assert elapsed < 1.0, f'too slow: {elapsed:.2f}s'\n",
        encoding="utf-8",
    )


def _fx_h5(wd: Path) -> None:
    (wd / "legacy.py").write_text(
        "# 旧的用户记录格式（dict）——迁移到 User 类后此文件应删除或不再被 api.py 使用\n"
        "USERS = [\n    {'name': 'ann', 'email': 'ann@x.io', 'active': 1},\n"
        "    {'name': 'bob', 'email': 'bob@x.io', 'active': 0},\n]\n",
        encoding="utf-8",
    )
    (wd / "api.py").write_text("# 迁移后这里应从 users 模块构建 User 并提供 find_active\n", encoding="utf-8")
    (wd / "users.py").write_text(
        "from dataclasses import dataclass\n\n\n"
        "@dataclass\n"
        "class User:\n"
        "    name: str\n"
        "    email: str\n"
        "    active: bool = True\n\n"
        "    @property\n"
        "    def is_active(self) -> bool:\n        return self.active\n",
        encoding="utf-8",
    )
    (wd / "test_h5.py").write_text(
        "from api import USERS, find_active\n\n"
        "def test_class_api():\n"
        "    u = USERS[0]\n"
        "    assert u.name == 'ann' and u.is_active is True\n\n"
        "def test_find_active():\n"
        "    assert [u.name for u in find_active(USERS)] == ['ann']\n",
        encoding="utf-8",
    )


def _fx_h6(wd: Path) -> None:
    (wd / "tree.py").write_text(
        "def build(depth):\n"
        "    \"\"\"Return a nested list structure of the given depth without crashing.\"\"\"\n"
        "    def rec(n):\n"
        "        if n == 0:\n"
        "            return []\n"
        "        return [rec(n - 1), rec(n - 1)]\n"
        "    return rec(depth)\n\n"
        "def total_leaves(node, acc=[]):\n"
        "    # 统计叶子（空列表）数量\n"
        "    if not node:\n"
        "        acc.append(1)\n"
        "        return acc\n"
        "    for child in node:\n"
        "        total_leaves(child, acc)\n"
        "    return acc\n",
        encoding="utf-8",
    )
    (wd / "test_h6.py").write_text(
        "from tree import build, total_leaves\n\n"
        "def test_deep_build_and_count():\n"
        "    t = build(12)\n"
        "    assert len(total_leaves(t)) == 4096\n\n"
        "def test_no_state_leak_between_calls():\n"
        "    assert len(total_leaves(build(1))) == 2\n"
        "    assert len(total_leaves(build(1))) == 2\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------------ M5 needs a dedup check
def _judge_m5(ctx: JudgeContext) -> tuple[bool, str]:
    ok, ev = _pytest_judge(ctx)
    if not ok:
        return ok, ev
    src = (ctx.workdir / "report.py").read_text(encoding="utf-8")
    marker = "def _fmt_row(label, value):"
    n = src.count(marker)
    if n > 1:
        return False, f"behavior preserved but the helper is still duplicated x{n} — the refactor did not happen"
    return True, f"{ev}; dedup confirmed (helper defined once)"


def build_bench_tasks() -> list[Task]:
    """Tier 2 + tier 3. Tier 1 comes from the live-eval suite by id."""
    m = [
        ("M1", "把 geometry.py 的 circle_area 重命名为 disk_area，同步更新 shapes.py 的调用。然后运行测试确认全部通过。"),
        ("M2", "在 string_utils.py 实现 slugify(text)：小写、标点替换为连字符、连续空白压成一个连字符、去首尾连字符。运行测试确认通过。"),
        ("M3", "paginator.py 的 page() 有边界 bug（1-based 分页）。阅读测试，修复并运行确认通过。"),
        ("M4", "在 turnstile.py 实现 Turnstile 类：初始 locked=True；coin() 解锁；push() 通过并重新锁上；locked 状态下 push() 抛出 AlarmError。AlarmError 需要在模块里定义。运行测试确认通过。"),
        ("M5", "report.py 里 _fmt_row 定义了两次（复制粘贴）。去重：只保留一个定义，两个公开函数行为不变。运行测试确认通过。"),
        ("M6", "scheduler.py 的 order() 在多依赖时提前放行（ANY 而非 ALL）。阅读测试修复：所有依赖完成才可执行。运行测试确认通过。"),
        ("M7", "按 test_m7.py 的契约修复 reader.read_scores：文件不存在返回空 dict（不抛异常），无法解析的行跳过。运行测试确认通过。"),
        ("M8", "在 conf.py 实现 load_config(path, defaults)：读 key=value 行；defaults 提供缺省；纯数字值转 int。运行测试确认通过。"),
    ]
    h = [
        ("H1", "给 app.py 和 runner.py 加 logging（模块级 logger，名为各自模块名）：compute 记录 op 与结果，run 记录调用。不要用 print。运行测试确认通过（测试用 caplog 断言）。"),
        ("H2", "test_h2.py 暴露了 basket.add_item 的一个经典 Python 陷阱导致的 bug。定位并修复，保持显式传 items 参数的用法可用。运行测试确认通过。"),
        ("H3", "在 calc.py 实现 evaluate(expr)：支持 + - * / 与括号、整数与浮点；'x = 5' 形式的赋值语句返回 None 并记住变量；未知变量抛 KeyError。运算优先级正确。运行测试确认通过。"),
        ("H4", "dedupe.unique 目前是 O(n²)，大输入下太慢（测试里有 1 秒的时限断言）。改成 O(n) 且行为不变（保持首次出现顺序）。运行测试确认通过。"),
        ("H5", "把 legacy.py 的 dict 用户记录迁移到 users.User 类：api.py 应提供 USERS（User 实例列表，含 bob active=False）和 find_active(users)。迁移完成后让测试通过；legacy.py 不再被引用。"),
        ("H6", "test_h6.py 暴露了 tree.py 的两个 bug（一个计数跨调用泄漏，一个深构建崩溃或计数错误）。阅读测试定位并修复。运行测试确认通过。"),
    ]
    fixtures = {
        "M1": _fx_m1, "M2": _fx_m2, "M3": _fx_m3, "M4": _fx_m4, "M5": _fx_m5,
        "M6": _fx_m6, "M7": _fx_m7, "M8": _fx_m8,
        "H1": _fx_h1, "H2": _fx_h2, "H3": _fx_h3, "H4": _fx_h4, "H5": _fx_h5, "H6": _fx_h6,
    }
    out = []
    for tid, prompt in m + h:
        judge = _judge_m5 if tid == "M5" else _pytest_judge
        tier = 2 if tid.startswith("M") else 3
        out.append(Task(id=tid, category=f"T{tier}", prompt=prompt, setup=fixtures[tid], judge=judge,
                        note={"M1": "cross-file rename refactor", "M2": "implement-to-tests (slugify)",
                              "M3": "1-based pagination boundary bug", "M4": "state machine to tests",
                              "M5": "dedup refactor, behavior preserved + source check",
                              "M6": "ANY-vs-ALL dependency bug", "M7": "I/O error-contract fix",
                              "M8": "config loader semantics (defaults + int coercion)",
                              "H1": "multi-file logging feature (caplog-tested)",
                              "H2": "mutable default arg leak", "H3": "expression evaluator with variables",
                              "H4": "O(n²)→O(n) under a timed assertion", "H5": "dict→class API migration",
                              "H6": "shared-state leak + deep recursion"}[tid]))
    return out
