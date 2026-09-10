# StepFun live rerun evidence

Read section 7 of the root audit report. These results include failures.

- Root `verify*.log`: original scripts fail on the removed workdir keyword.
- `after/`: TurnSpec repair; harness passes, deepagent encounters 429.
- `paced-12/`: approximately one model request per 10 seconds; harness passes,
  deepagent reaches its unchanged 30-step budget.
- `production-budget-12/`: explicit diagnostic override to the production
  200-step budget; both deepagent scenarios finish. Not a stock-script pass.
- `perf-final.log`: real stepfun_api factory; QA passes, analysis fails the
  reported input-token threshold. `usage_probe.json` shows cumulative SSE
  usage being added repeatedly by the installed adapter.
- `perf.log`, `perf-after.log`: retained failures of the audit driver itself
  (missing isatty, then a placeholder credential precedence mistake).
- `rerun-before*` / `rerun-after*`: full standard gates on both Python versions.

Logs are scrubbed for the supplied key, user path and hostname; trailing
whitespace is normalized. Preflight JSON omits model reasoning and request IDs.
No API key is stored in these drivers. They prompt using getpass, construct the
model, and remove the key from the environment before tools execute.

To reproduce, copy a driver into a scratch directory, activate the repository's
venv (or use uv run), and execute it while the current directory is the repo.
The driver writes sibling evidence files. Use only a standard StepFun China API
key; these scripts deliberately override the Anthropic base to
https://api.stepfun.com, not the repository's default Coding Plan endpoint.
Do not run all drivers simultaneously: this account returned a 10-RPM limit.

The performance driver injects an actual build_chat_model factory configured
for stepfun_api. It preserves the original tests and thresholds, with waits
between test setups excluded from their own timing. The usage probe forwards
real response bytes unchanged and records only usage fields.
