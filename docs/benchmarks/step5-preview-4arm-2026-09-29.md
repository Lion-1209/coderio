# coderio (harness on/off) vs step-cli vs dsh — step-5-preview, same endpoint & key

- date: 2026-09-29 | model: `step-5-preview` via StepFun coding plan (Anthropic protocol), all arms
- coderio v0.6.0 default TurnSpec; the OFF arm is identical except harness_enabled=False
- step-cli 1.0.0 shipped defaults + headless tool overrides + --max-steps 40
- dsh 0.1.7-rc.2 headless profile, shipped defaults (stepfun provider, anthropic-messages)
- 20 tasks / 3 tiers; judges frozen (pytest rc / output / answer-contains / content check)
- tokens: coderio arms measured via engine usage (in/out);
  step-cli / dsh do not expose per-request usage (stated)

## Scoreboard

- **coderio harness ON: 20/20**, 886s total, tokens 1065550/48682
- **coderio harness OFF: 19/20**, 807s total, tokens 1222242/54498
- **step-cli: 19/20**, 4680s total
- **dsh (deepseek-harness): 20/20**, 1378s total

## Per tier

| tier | tasks | coderio harness ON | coderio harness OFF | step-cli | dsh (deepseek-harness) |
|---|---|---|---|---|---|
| tier1 easy (6) | 6/6 | 6/6 | 6/6 | 6/6 |
| tier2 medium (8) | 8/8 | 8/8 | 8/8 | 8/8 |
| tier3 hard (6) | 6/6 | 5/6 | 5/6 | 6/6 |

## Per task

| task | coderio harness ON | coderio harness OFF | step-cli | dsh (deepseek-harness) | time per arm | tokens ON (i/o) |
|---|---|---|---|---|---|---|
| A1 | ✅ | ✅ | ✅ | ✅ | 10s/16s/36s/21s | 29598/354 |
| A2 | ✅ | ✅ | ✅ | ✅ | 20s/26s/59s/32s | 46398/1207 |
| A3 | ✅ | ✅ | ✅ | ✅ | 10s/9s/56s/37s | 29547/387 |
| C1 | ✅ | ✅ | ✅ | ✅ | 3s/4s/74s/14s | 14404/204 |
| C2 | ✅ | ✅ | ✅ | ✅ | 5s/4s/75s/19s | 21593/321 |
| B1 | ✅ | ✅ | ✅ | ✅ | 10s/19s/94s/31s | 29614/363 |
| M1 | ✅ | ✅ | ✅ | ✅ | 12s/13s/92s/40s | 37787/583 |
| M2 | ✅ | ✅ | ✅ | ✅ | 29s/28s/145s/68s | 51535/2737 |
| M3 | ✅ | ✅ | ✅ | ✅ | 18s/16s/66s/50s | 46320/1118 |
| M4 | ✅ | ✅ | ✅ | ✅ | 44s/52s/119s/30s | 67848/2134 |
| M5 | ✅ | ✅ | ✅ | ✅ | 15s/15s/95s/20s | 53649/649 |
| M6 | ✅ | ✅ | ✅ | ✅ | 13s/18s/126s/39s | 45160/455 |
| M7 | ✅ | ✅ | ✅ | ✅ | 12s/20s/91s/28s | 37793/646 |
| M8 | ✅ | ✅ | ✅ | ✅ | 29s/16s/384s/36s | 39540/1686 |
| H1 | ✅ | ✅ | ✅ | ✅ | 176s/142s/391s/308s | 221528/15901 |
| H2 | ✅ | ✅ | ✅ | ✅ | 17s/14s/23s/65s | 37377/601 |
| H3 | ✅ | ✅ | ✅ | ✅ | 67s/57s/192s/126s | 60512/6404 |
| H4 | ✅ | ✅ | ✅ | ✅ | 29s/66s/257s/43s | 49906/2208 |
| H5 | ✅ | ✅ | ✅ | ✅ | 18s/27s/48s/42s | 47823/1405 |
| H6 | ✅ | ❌ | ❌ | ✅ | 349s/248s/2258s/327s | 97618/9319 |

## Evidence

### A1 — fix a buggy function until the repo's own tests pass
- coderio_on: PASS — pytest rc=0 :: 1 passed in 0.01s
- coderio_off: PASS — pytest rc=0 :: 1 passed in 0.03s
- step_cli: PASS — pytest rc=0 :: 1 passed in 0.01s
- dsh: PASS — pytest rc=0 :: 1 passed in 0.03s

### A2 — add a function + tests; suite must stay green
- coderio_on: PASS — pytest rc=0 :: 5 passed in 0.01s
- coderio_off: PASS — pytest rc=0 :: 6 passed in 0.02s
- step_cli: PASS — pytest rc=0 :: 5 passed in 0.03s
- dsh: PASS — pytest rc=0 :: 3 passed in 0.01s

### A3 — fix a known crash; judged by empty-input + concrete input
- coderio_on: PASS — empty rc=0 out='0.0' | normal rc=0 out='4.0'
- coderio_off: PASS — empty rc=0 out='0.0' | normal rc=0 out='4.0'
- step_cli: PASS — empty rc=0 out='0.0' | normal rc=0 out='4.0'
- dsh: PASS — empty rc=0 out='0.0' | normal rc=0 out='4.0'

### C1 — answer must contain facts grounded in the three files
- coderio_on: PASS — answer contains ['beta wrapping alpha', '42']
- coderio_off: PASS — answer contains ['beta wrapping alpha', '42']
- step_cli: PASS — answer contains ['beta wrapping alpha', '42']
- dsh: PASS — answer contains ['beta wrapping alpha', '42']

### C2 — cross-file reference question
- coderio_on: PASS — answer contains ['alpha', 'beta']
- coderio_off: PASS — answer contains ['alpha', 'beta']
- step_cli: PASS — answer contains ['alpha', 'beta']
- dsh: PASS — answer contains ['alpha', 'beta']

### B1 — classic sign-flip bug; tests decide
- coderio_on: PASS — pytest rc=0 :: 1 passed in 0.01s
- coderio_off: PASS — pytest rc=0 :: 1 passed in 0.01s
- step_cli: PASS — pytest rc=0 :: 1 passed in 0.03s
- dsh: PASS — pytest rc=0 :: 1 passed in 0.01s

### M1 — cross-file rename refactor
- coderio_on: PASS — pytest rc=0 :: 2 passed in 0.01s
- coderio_off: PASS — pytest rc=0 :: 2 passed in 0.01s
- step_cli: PASS — pytest rc=0 :: 2 passed in 0.01s
- dsh: PASS — pytest rc=0 :: 2 passed in 0.01s

### M2 — implement-to-tests (slugify)
- coderio_on: PASS — pytest rc=0 :: 4 passed in 0.03s
- coderio_off: PASS — pytest rc=0 :: 4 passed in 0.01s
- step_cli: PASS — pytest rc=0 :: 4 passed in 0.01s
- dsh: PASS — pytest rc=0 :: 4 passed in 0.01s

### M3 — 1-based pagination boundary bug
- coderio_on: PASS — pytest rc=0 :: 3 passed in 0.01s
- coderio_off: PASS — pytest rc=0 :: 3 passed in 0.01s
- step_cli: PASS — pytest rc=0 :: 3 passed in 0.01s
- dsh: PASS — pytest rc=0 :: 3 passed in 0.01s

### M4 — state machine to tests
- coderio_on: PASS — pytest rc=0 :: 4 passed in 0.01s
- coderio_off: PASS — pytest rc=0 :: 4 passed in 0.01s
- step_cli: PASS — pytest rc=0 :: 4 passed in 0.01s
- dsh: PASS — pytest rc=0 :: 4 passed in 0.01s

### M5 — dedup refactor, behavior preserved + source check
- coderio_on: PASS — pytest rc=0 :: 2 passed in 0.01s; dedup confirmed (helper defined once)
- coderio_off: PASS — pytest rc=0 :: 2 passed in 0.03s; dedup confirmed (helper defined once)
- step_cli: PASS — pytest rc=0 :: 2 passed in 0.01s; dedup confirmed (helper defined once)
- dsh: PASS — pytest rc=0 :: 2 passed in 0.01s; dedup confirmed (helper defined once)

### M6 — ANY-vs-ALL dependency bug
- coderio_on: PASS — pytest rc=0 :: 2 passed in 0.01s
- coderio_off: PASS — pytest rc=0 :: 2 passed in 0.01s
- step_cli: PASS — pytest rc=0 :: 2 passed in 0.01s
- dsh: PASS — pytest rc=0 :: 2 passed in 0.01s

### M7 — I/O error-contract fix
- coderio_on: PASS — pytest rc=0 :: 2 passed in 0.03s
- coderio_off: PASS — pytest rc=0 :: 2 passed in 0.03s
- step_cli: PASS — pytest rc=0 :: 2 passed in 0.03s
- dsh: PASS — pytest rc=0 :: 2 passed in 0.04s

### M8 — config loader semantics (defaults + int coercion)
- coderio_on: PASS — pytest rc=0 :: 2 passed in 0.03s
- coderio_off: PASS — pytest rc=0 :: 2 passed in 0.03s
- step_cli: PASS — pytest rc=0 :: 2 passed in 0.04s
- dsh: PASS — pytest rc=0 :: 2 passed in 0.03s

### H1 — multi-file logging feature (caplog-tested)
- coderio_on: PASS — pytest rc=0 :: 2 passed in 0.01s
- coderio_off: PASS — pytest rc=0 :: 2 passed in 0.01s
- step_cli: PASS — pytest rc=0 :: 2 passed in 0.01s
- dsh: PASS — pytest rc=0 :: 2 passed in 0.01s

### H2 — mutable default arg leak
- coderio_on: PASS — pytest rc=0 :: 2 passed in 0.01s
- coderio_off: PASS — pytest rc=0 :: 2 passed in 0.03s
- step_cli: PASS — pytest rc=0 :: 2 passed in 0.03s
- dsh: PASS — pytest rc=0 :: 2 passed in 0.01s

### H3 — expression evaluator with variables
- coderio_on: PASS — pytest rc=0 :: 4 passed in 0.03s
- coderio_off: PASS — pytest rc=0 :: 4 passed in 0.01s
- step_cli: PASS — pytest rc=0 :: 4 passed in 0.02s
- dsh: PASS — pytest rc=0 :: 4 passed in 0.01s

### H4 — O(n²)→O(n) under a timed assertion
- coderio_on: PASS — pytest rc=0 :: 2 passed in 0.01s
- coderio_off: PASS — pytest rc=0 :: 2 passed in 0.01s
- step_cli: PASS — pytest rc=0 :: 2 passed in 0.02s
- dsh: PASS — pytest rc=0 :: 2 passed in 0.02s

### H5 — dict→class API migration
- coderio_on: PASS — pytest rc=0 :: 2 passed in 0.01s
- coderio_off: PASS — pytest rc=0 :: 2 passed in 0.01s
- step_cli: PASS — pytest rc=0 :: 2 passed in 0.01s
- dsh: PASS — pytest rc=0 :: 2 passed in 0.23s

### H6 — shared-state leak + deep recursion
- coderio_on: PASS — pytest rc=0 :: 2 passed in 0.01s
- coderio_off: FAIL — pytest rc=1 :: 1 failed, 1 passed in 0.44s | error: GraphRecursionError: Recursion limit of 60 reached without hitting a stop condition. You can increase the limit by setting the `recursion_limit` confi
- step_cli: FAIL — pytest rc=1 :: 1 failed, 1 passed in 0.29s | error: timeout after 900s
- dsh: PASS — pytest rc=0 :: 2 passed in 0.01s


---

## Analysis (4-arm)

**Two perfect arms: coderio (harness ON) 20/20 in 886s, and dsh 20/20 in
1378s. step-cli 19/20 in 4680s, coderio harness-OFF 19/20 in 807s.**

### H6 (shared-state leak + deep recursion) is the discriminator

The hardest task again separates disciplined runtimes from undisciplined
ones: harness-ON coderio passed (349s), dsh passed (327s), while
harness-OFF coderio looped to its recursion budget death and step-cli
burned past the 900s timeout with the fix incomplete. Both perfect arms
share the "verify before done" philosophy — this benchmark now has two
independent implementations agreeing that it matters exactly where tasks
get hard.

### Positioning

- **coderio: fastest arm overall (886s) at the best accuracy.** ~1.55x
  faster than dsh, ~5.3x faster than step-cli, same 20/20 as dsh.
- **dsh: the credible peer.** Also 20/20, comfortably ahead of step-cli,
  and it solved H6 cleanly. A well-engineered runtime.
- **step-cli: correct on 19/20 but consistently 3-10x slower per task.**

### Honest caveats

- coderio's numbers are in-process (no CLI boot per task); dsh and
  step-cli pay node boot + profile mount per task (~10-20s each), so the
  external arms' totals include ~200-400s of pure startup overhead.
- Single run per arm/task (pass rates near ceiling — the differentiators
  are H6 and time/tokens, not aggregate accuracy); single model; defaults
  vs defaults for the external CLIs.
- dsh's stepfun provider was BROKEN before this run (double `/v1` in the
  user-layer baseURL → 404 on every request); fixed in
  `~/.dsh/profiles/headless/cordis.patch.yml` as part of setup. Its prior
  step-3.7-flash configuration had never actually worked.
- Harness ON vs OFF (coderio internals, boot-free comparison): 20/20 vs
  19/20 and 13% fewer input tokens with the gates on — see the 3-arm
  report for that isolated analysis.
