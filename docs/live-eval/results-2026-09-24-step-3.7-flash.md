# coderio live eval results

- date: 2026-09-24
- model: `step-3.7-flash` (provider kind: `anthropic`)
- tasks: 1/1 passed

| task | category | result | evidence |
|---|---|---|---|
| R1 | R | PASS | conversation-only fact retained across the interrupt |

Behavioral detail (harness signals = the gates actually firing):

- R1: signals=[none] tools=1 ran_execute=False 5.3s

---

## What R1 proves

R1 is the live regression probe for the 2026-09-23 WhaleDock post-interrupt
amnesia bug (the incident's disaster replay): a fact that exists ONLY in
turn 1's user message must survive an Esc-interrupt. Turn 1 (read a file +
memorize the session password) is interrupted after its first tool result —
exactly where the incident's interrupt landed; turn 2 asks for the password
with tools forbidden. Under the old `_build_inputs` the post-interrupt turn
received ONLY the new message into an empty graph — the password was
unrecoverable and the model cold-started (the incident's "你好！" replay).
With the full-history fallback, the model answers from context.

This run (step-3.7-flash, anthropic protocol, 2026-09-24): **PASS** —
"conversation-only fact retained across the interrupt".
