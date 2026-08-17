# Changelog

All notable changes to coderio are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions adhere to
[Semantic Versioning](https://semver.org/). Version source of truth is
`pyproject.toml`'s `[project].version` — `coderio.__version__` reads it via
`importlib.metadata`.

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
