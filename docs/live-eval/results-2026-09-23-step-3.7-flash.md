# coderio live eval results

- date: 2026-09-23
- model: `step-3.7-flash` (provider kind: `anthropic`)
- tasks: 10/10 passed

| task | category | result | evidence |
|---|---|---|---|
| A1 | A | PASS | pytest rc=0 :: 1 passed in 0.01s |
| A2 | A | PASS | pytest rc=0 :: 3 passed in 0.02s |
| A3 | A | PASS | empty rc=0 out='0.0' \| normal rc=0 out='4.0' |
| C1 | C | PASS | answer contains ['beta wrapping alpha', '42'] |
| C2 | C | PASS | answer contains ['alpha', 'beta'] |
| D1 | D | PASS | gate fired (continue): [harness] You wrote code but haven't verified it. You MUST run it (use execute to execute/test/li |
| D2 | D | PASS | gate fired (continue): [harness] You cited calc.py, test_calc.py but did not read it this turn. A claim about code must  |
| D3 | D | PASS | model read legacy.py itself — citation grounded, gate correctly silent |
| D4 | D | PASS | gate fired (continue): [harness] You wrote code but haven't verified it. You MUST run it (use execute to execute/test/li |
| B1 | B | PASS | pytest rc=0 :: 1 passed in 0.03s |

Behavioral detail (harness signals = the gates actually firing):

- A1: signals=[none] tools=4 ran_execute=True 10.9s
- A2: signals=[none] tools=12 ran_execute=True 18.7s
- A3: signals=[none] tools=4 ran_execute=True 7.2s
- C1: signals=[none] tools=3 ran_execute=False 4.2s
- C2: signals=[none] tools=4 ran_execute=False 3.9s
- D1: signals=[harness_continue, harness_continue] tools=3 ran_execute=True 6.2s
- D2: signals=[harness_continue] tools=5 ran_execute=True 9.8s
- D3: signals=[harness_continue] tools=2 ran_execute=False 10.5s
- D4: signals=[harness_continue] tools=13 ran_execute=True 85.2s
- B1: signals=[none] tools=5 ran_execute=True 7.3s
