# coderio vs step-cli — step-5-preview, same endpoint & key

- date: 2026-09-29
- model: `step-5-preview` via StepFun coding plan (Anthropic protocol) for BOTH arms
- coderio: v0.6.0, default TurnSpec (four-gate harness ON), no permission gate, recursion_limit 60
- step-cli: 1.0.0 shipped defaults + headless tool overrides + --max-steps 40
- tasks: agent-agnostic subset of the live-eval suite; judges frozen (pytest rc / output / answer-contains)

**coderio 6/6 · step-cli 6/6**

| task | category | coderio | step-cli | coderio time | step-cli time |
|---|---|---|---|---|---|
| A1 | A | ✅ | ✅ | 13.6s | 33.4s |
| A2 | A | ✅ | ✅ | 31.3s | 124.3s |
| A3 | A | ✅ | ✅ | 7.9s | 125.5s |
| C1 | C | ✅ | ✅ | 4.0s | 73.9s |
| C2 | C | ✅ | ✅ | 4.4s | 50.1s |
| B1 | B | ✅ | ✅ | 11.7s | 135.3s |

## Evidence per task

### A1 — fix a buggy function until the repo's own tests pass
- coderio: PASS — pytest rc=0 :: 1 passed in 0.01s
- step-cli: PASS — pytest rc=0 :: 1 passed in 0.01s

### A2 — add a function + tests; suite must stay green
- coderio: PASS — pytest rc=0 :: 5 passed in 0.01s
- step-cli: PASS — pytest rc=0 :: 3 passed in 0.01s

### A3 — fix a known crash; judged by empty-input + concrete input
- coderio: PASS — empty rc=0 out='0.0' | normal rc=0 out='4.0'
- step-cli: PASS — empty rc=0 out='0.0' | normal rc=0 out='4.0'

### C1 — answer must contain facts grounded in the three files
- coderio: PASS — answer contains ['beta wrapping alpha', '42']
- step-cli: PASS — answer contains ['beta wrapping alpha', '42']

### C2 — cross-file reference question
- coderio: PASS — answer contains ['alpha', 'beta']
- step-cli: PASS — answer contains ['alpha', 'beta']

### B1 — classic sign-flip bug; tests decide
- coderio: PASS — pytest rc=0 :: 1 passed in 0.01s
- step-cli: PASS — pytest rc=0 :: 1 passed in 0.01s

---

## Honest analysis

**Accuracy: 6/6 vs 6/6 — no differentiation.** step-5-preview is strong
enough that small self-verifying tasks don't separate agents; this matches
the pre-run expectation ("either a weapon or a map"). To differentiate
accuracy you need longer tasks where discipline compounds (multi-file
refactors, tasks that tempt fake-done) — or the adversarial D-framing,
which is not portable to another agent's internals.

**Speed: coderio 72.9s total vs step-cli 542.5s total (~7.4x).** A real,
reproducible wall-clock gap with identical model/endpoint/key — but the
fair reading includes: step-cli ran with its shipped default
`--anthropic-thinking-budget-tokens 16000` (per tool turn) while coderio
does not set an explicit thinking budget; most of the gap is plausibly
thinking-token volume plus step-cli's larger bundled tool surface, not raw
architecture. Tuning the opponent's knobs down would be unfair the other
way; defaults-vs-defaults is the honest framing. Token accounting per arm
is the follow-up that would settle it.

**Takeaway:** on this slice, step-5-preview makes both agents correct;
coderio makes it ~7x faster to the same correct answer. The claim this
supports is "lean tool surface + harness discipline = same result, far
fewer round-trips" — modest, real, and honest about its limits.
