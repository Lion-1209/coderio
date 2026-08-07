# Changelog

All notable changes to coderio are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions adhere to
[Semantic Versioning](https://semver.org/). Version source of truth is
`pyproject.toml`'s `[project].version` — `coderio.__version__` reads it via
`importlib.metadata`.

## [Unreleased]

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

[Unreleased]: https://github.com/Lion-1209/coderio/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Lion-1209/coderio/releases/tag/v0.2.0
[0.1.0]: https://github.com/Lion-1209/coderio/releases/tag/v0.1.0
