"""Live eval for coderio: real-provider tasks with auto-judged outcomes.

WHY THIS EXISTS (change plan Phase 2): the repo's 1100+ tests mock the model
layer. They prove the code does what its author expected; they prove nothing
about real providers (streaming chunk boundaries, tool-call shapes, thinking
blocks, provider 400s) — and nothing about whether the four harness gates
actually fire on a real model. This package turns the four-gate claim into
measured, re-runnable, publishable evidence.

Run (from the repo root, with the user's own ~/.coderio config + keys):

    .venv/Scripts/python.exe -m scripts.live_eval.run --provider stepfun
    .venv/Scripts/python.exe -m scripts.live_eval.run --only D1 D2 D3

Design rules (learned from the 2026-09-16 analysis and ZCode's
prompt-trajectory tool):
  - every task carries an AUTO-JUDGEABLE success criterion (a test that
    passes, an output that matches, a gate signal that must fire) — no
    prose grading, ever;
  - behavioral assertions only: tool calls made, exit codes, harness
    signals. Model prose is never asserted (it varies run to run);
  - the adversarial category (D*) is the point of the whole suite: tasks
    designed so the model is TEMPTED to claim done without verifying —
    that is exactly what the gates exist to catch;
  - results record the model NAME and provider per run — model IDs drift
    (water18-0910 → ...), so evidence must be traceable to the exact model.
"""

from __future__ import annotations
