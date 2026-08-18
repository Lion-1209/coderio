# coderio

[中文](README.md) | **English**

> A skill-driven coding agent — structural harness constraints, a foldable-thinking TUI, and a deepagents engine. Built on langchain + langgraph + deepagents + Lion-Skills. Windows-first, cross-platform.

## Why this project

It started as a way to spend some free tokens and walk the full langchain stack while building an agent. Once the framework was up, a plain REPL + CLI felt uninspiring, so I built a TUI — and that part I'm genuinely happy with.

The framework has not been finely tuned; it's a demo built in spare time. It's open-sourced not as a product, but as a working reference for anyone using the langchain stack: how to wire the deepagents engine, how to build structural harness constraints, how to do streaming rendering in a TUI — the code is all here, it runs, take it and play.

> About the name: **coderio = code + rio** (not coder + io). My English name is Lion; I first tried "codelion" but it felt odd, so coderio it is.

---

**coderio** is a skill-driven coding agent. Its skeleton is the [Lion-Skills](https://github.com/Lion-1209/Lion-Skills) suite (clarify → spec → task → execute → verify → commit workflow); coderio adds tools that actually get work done, a **harness layer** that enforces the workflow, and an interactive Textual TUI. The reference points are Claude Code / Codex / ZCode.

Core philosophy: **skills are the playbook, the harness is the discipline, tools are the hands.** Three layers, none substituting for another.

---

## Features

- **Harness four-gate hard constraints**: when the agent writes code but declares "done" without running it, the harness intercepts termination and force-continues the loop — not a prompt-level soft rule, but a system-level structural control based on tool-call ground truth. VerifyGate parses shell exit codes (both coderio's legacy format and deepagents' native format), so failing tests no longer count as "verified"; permission-denied runs don't either; non-code files (.md/.json/.yaml docs) are intelligently skipped; CompletionGate checks for pending todos.
- **Explicit state machine**: execution phase derived in real time (explore → plan → implement → verify → complete), shown in the status bar; per-turn phase timelines persist to the session for replay/debugging.
- **deepagents engine**: production engine built on [deepagents](https://github.com/langchain-ai/deepagents) with context management (offload + summarization), subagents (task tool, incl. a read-only research subagent), a filesystem backend, and persistent checkpoints (SqliteSaver). deepagents' default prompt is fully replaced — coderio's system prompt is the only one the model sees.
- **Persistent checkpoints**: graph state persists to sqlite across turns; only the new message is sent (no full-history replay), and SummarizationMiddleware's accumulated state is preserved correctly.
- **Automatic context compaction**: deepagents' SummarizationMiddleware triggers near 60% of the context window (configurable via `[context].trigger_ratio`) — old messages are offloaded to files + LLM-summarized.
- **Intent classification**: CODE / QA / ANALYZE intents are auto-detected (bilingual signal words); coding tasks go through the workflow, questions are answered directly.
- **Progressive disclosure**: skill bodies load on demand; the system prompt stays ~2K tokens instead of piling everything in.
- **Interactive TUI**: Textual terminal UI with foldable thinking (Ctrl+O), streaming output, a tool-call status bar (animated spinner + step + task phase + timer + per-turn token count), slash-command autocomplete, a collapsible TODO panel (live ✓/→/○ progress), a vertical permission menu (↑↓ + Enter), session management (`/resume` + Del to delete), visual pickers (`/mode` `/profile`), file-change visualization, task interruption (Esc / ⏹ button), and error recovery.
- **Tool error resilience**: failed tool calls become tool results fed back to the model for self-correction instead of crashing the turn; the bash tool kills the whole process tree on timeout (Windows Job Object).
- **File path isolation**: the deepagents backend's `virtual_mode` confines file tools (write_file/edit_file/read_file/ls/grep/glob) to the workspace root — the agent's `/foo.py` maps to `{workdir}/foo.py`.
- **Command review layer**: shell (execute) commands are not covered by virtual_mode, so an additional `CommandReviewMiddleware` layer applies a built-in blacklist (`rm -rf /`, `mkfs`, fork bombs, `dd of=/dev/`, shutdown — blocked even in FULL mode) plus user-extendable `blocked_commands`. This is not a real OS sandbox (obfuscated commands can bypass regex), but it stops the vast majority of accidental damage. `network_allowed = false` disables the web tools entirely (offline mode).
- **SSRF-protected web_fetch**: scheme allowlist (http/https only), private/loopback/link-local IP blocking (incl. cloud metadata endpoints), per-hop redirect validation, and a 1 MB response cap.
- **Multi-layer OS sandbox** (Linux real isolation): `sandbox_mode = "job" | "write"` — Job Object resource limits on Windows; bubblewrap namespace isolation on Linux (read-only root, read-write workspace, optional network cutoff, Claude-Code-compatible filesystem 4-tuple `allow_write`/`deny_write`/`deny_read`/`allow_read`).
- **MCP support**: connect external MCP servers via `.mcp.json` (Claude Code-compatible format); stdio and HTTP transports; project-level config overrides user-level. `coderio mcp` CLI manages entries; MCP tools are gated by the permission system like built-in tools.
- **Repo-config trust confirmation**: first time a repository's `.coderio/config.toml` or `.mcp.json` is seen, coderio shows what it contains (permission mode, base_url, MCP commands) and requires explicit confirmation — content-hashed, so upstream edits re-trigger the prompt. Cloning a hostile repo no longer means silent arbitrary code execution.
- **Multiple providers + named profiles**: Zhipu GLM / StepFun coding plans (Anthropic protocol) + OpenAI-compatible endpoints; multiple profiles with runtime `/profile` switching.
- **Headless mode**: `coderio run "task"` for one-shot, non-interactive runs (CI, scripting, benchmark harnesses).

---

## Quick start

### Install

**Option 1: pip from PyPI (recommended)**

```bash
pip install coderio
```

**Option 2: from GitHub**

```bash
pip install "coderio @ git+https://github.com/Lion-1209/coderio.git"
```

**Option 3: Release wheel (offline/intranet)**

Download the latest `coderio-*.whl` from the [Releases page](https://github.com/Lion-1209/coderio/releases), then:

```bash
pip install coderio-0.4.0-py3-none-any.whl
```

**Option 4: from source (developers, uv recommended)**

```bash
git clone https://github.com/Lion-1209/coderio.git
cd coderio

# uv (recommended — same as CI, deps locked in uv.lock)
uv sync --extra dev          # creates .venv, installs exactly per uv.lock
uv run pytest -q             # run commands via uv run

# or plain pip (does not read the lockfile; resolves latest compatible versions)
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"    # Windows (Git Bash)
.venv/bin/python -m pip install -e ".[dev]"            # Linux / macOS
```

Requirements: Python 3.11+; on Windows, Git Bash must be installed (the shell tool depends on it).

**Dependency management**: CI installs exactly per [uv.lock](uv.lock) via `uv sync --frozen` (reproducible); Dependabot's weekly upgrade PRs update pyproject.toml and uv.lock together. After changing dependencies, run `uv lock` and commit both files.

**MCP support** (optional): install the MCP extra to connect external MCP servers:

```bash
pip install "coderio[mcp]"            # or: uv sync --extra dev --extra mcp
```

Without the extra, coderio works normally — `.mcp.json` is silently ignored.

### Configuration

The first interactive run triggers an onboarding wizard (pick provider, model, paste API key); config is written to `~/.coderio/config.toml` and `~/.coderio/credentials`. The wizard probes the model's context window and persists it, so compaction thresholds match the real model. Manual configuration:

```bash
# ~/.coderio/config.toml
[model]
provider_id = "bigmodel_coding_plan"   # Zhipu/StepFun/OpenAI/Anthropic/Ollama/custom
default = "glm-5.2"
context_limit = 128000                  # (optional) auto-probed by onboarding; 0 = use the default below
max_output_tokens = 16384               # (optional) max tokens per reply, default 16384

[tools]
permission_mode = "confirm"            # confirm | plan | auto_edit | full
workspace_root = ""                    # shell backend CWD (empty = launch dir); file-path isolation is handled by deepagents virtual_mode
blocked_commands = []                  # extra blacklist regexes, e.g. ["git push --force", "npm publish"]
network_allowed = true                 # false = disable web_fetch/web_search + Linux sandbox network cutoff (--unshare-net)
whitelist_mode = false                 # true = unknown commands degrade to confirm (see "Sandbox" below)
allowed_commands = []                  # extend the built-in whitelist, e.g. ["docker", "kubectl"]
sandbox_mode = "off"                   # off | job | write (see "Sandbox" below)
auto_allow_if_sandboxed = false        # auto-approve execute when sandbox is active (Claude Code "autoAllowBashIfSandboxed")

# Optional: sandbox filesystem isolation (Linux bubblewrap only; Windows ignores it for now)
[tools.sandbox_filesystem]
allow_write = []                       # extra writable paths (workspace is always writable), e.g. ["/tmp/build", "~/.cache"]
deny_write = []                        # forced read-only paths (even inside workspace), e.g. [".git/hooks"]
deny_read = []                         # unreadable paths, e.g. ["~/.ssh", "~/.aws/credentials", "~/.gnupg"]
allow_read = []                        # holes punched inside deny_read, e.g. ["~/.ssh/known_hosts"]

[context]
enabled = true                         # long-session auto-compaction (on by default)
trigger_ratio = 0.6                    # trigger at 60% of the context window
keep_recent = 8                        # keep the most recent N messages verbatim
model_context_limit = 200000           # fallback when the profile has no probed context_limit
```

**MCP configuration** (`.mcp.json`, Claude-Code-compatible):

Place `.mcp.json` in the project root (project scope) or `~/.coderio/mcp.json` (user scope). Configured servers and their tools load at startup:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "github": {
      "type": "http",
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": { "Authorization": "Bearer ghp_xxx" }
    }
  }
}
```

- **stdio servers**: `{command, args, env?, cwd?, timeoutMs?, enabled?}` — spawn a local subprocess (e.g. npx-run server-filesystem)
- **HTTP servers**: `{type: "http", url, headers?, timeoutMs?, enabled?}` — connect to a remote MCP endpoint
- **SSE servers**: `{type: "sse", url, headers?, timeoutMs?, enabled?}`
- **type inference**: omit `type` — a `command` implies stdio, a `url` implies http
- Tool names are prefixed with the server name (e.g. `filesystem_read_file`), never colliding with built-in tools
- Failed servers are skipped with a warning — startup is never blocked
- Requires the MCP extra: `pip install "coderio[mcp]"`

Optional fields (ZCode-compatible):
- `enabled: false` — temporarily disable a server without removing its config
- `cwd` — stdio subprocess working directory (often needed for npx/node on Windows)
- `timeoutMs` — per-request timeout in ms; forwarded to the adapter as `timeout` (seconds)
- Legacy field aliases auto-migrate: `enable`→`enabled`, `environment`→`env`, `http_headers`→`headers`, `type:"remote"`→`http`

**CLI management** (`coderio mcp`):

```bash
# Add a stdio server to the project .mcp.json
coderio mcp add filesystem --command npx --arg -y --arg @modelcontextprotocol/server-filesystem --arg /tmp

# Add an HTTP server to the user config
coderio mcp add github --type http --url https://api.githubcopilot.com/mcp/ --scope user

# List all configured servers (project + user)
coderio mcp list

# Remove a server
coderio mcp remove filesystem
```

**MCP tool permissions**: MCP tool names containing `write`/`create`/`delete`/`execute`/`run`/`fetch`/`request` (and similar keywords) are treated as destructive by the permission system (rejected in PLAN, prompted in CONFIRM, allowed in FULL) — same as built-in tools. Read-only MCP tools (`read`/`get`/`list`/`query`) pass in all modes.

**Sandbox & command security** (defense in depth):

coderio applies multiple security layers to shell (`execute`) commands, lightest to heaviest:

| Layer | Mechanism | Config | Strength |
|---|---|---|---|
| 1. Blacklist | Regex-blocks destructive commands (`rm -rf /`, `mkfs`, fork bombs, ...), even in FULL mode | `[tools].blocked_commands` extends | Accident prevention; bypassable via base64/variables |
| 2. Whitelist | Unknown commands (not in the ~60 built-in dev commands) degrade to confirm | `[tools].whitelist_mode = true` | Stronger (unknown commands prompt); still bypassable |
| 3. OS sandbox | Kernel-level isolation — the process physically lacks the permission to write outside bounds | `[tools].sandbox_mode` | The real security boundary |

`sandbox_mode` levels:

- **`off`** (default): no OS sandbox; blacklist + whitelist only. Existing behavior.
- **`job`**: Windows Job Object / POSIX process group + resource limits (process-count cap prevents fork bombs) + reliable process-tree kill. No file isolation, but stops resource abuse.
- **`write`**: file-write isolation.
  - **Linux**: ✅ **real isolation** — `bubblewrap` (`bwrap`), the same approach as Claude Code on Linux. Read-only root mount, read-write workspace, `--unshare-net` when `network_allowed=false`. Requires `apt install bubblewrap`.
  - **Windows**: ⚠️ **currently equivalent to `job`**. The `CreateRestrictedToken` + `CreateProcessAsUserW` plumbing is in place, but on non-admin accounts the token is a no-op (verified: original and restricted tokens both have Medium integrity — identical write permissions). True isolation needs per-directory ACLs (~500 lines), tracked as future work. **Windows users currently have no OS-level write isolation** — use `job`; `write` provides no extra protection.

```toml
# Recommended: job for daily dev (fork-bomb prevention + reliable cleanup)
# Linux users wanting real isolation: sandbox_mode = "write" (needs bubblewrap)
sandbox_mode = "job"
whitelist_mode = true
allowed_commands = ["docker", "kubectl"]  # commands outside the whitelist trigger confirm

# Advanced: auto-approve shell commands when the sandbox is active (fewer prompts)
# This is Claude Code's "autoAllowBashIfSandboxed" design — the sandbox provides the
# real isolation boundary, so per-command prompts become noise. The blacklist still applies.
auto_allow_if_sandboxed = true
```

**Permission-sandbox interplay** (Claude Code "autoAllowBashIfSandboxed" design):
- Default (`auto_allow_if_sandboxed = false`): even with the sandbox on, every execute still prompts (backward compatible)
- `true`: execute auto-approves when the sandbox is active — the sandbox is the boundary, prompts are noise
- **The blacklist always applies**: `rm -rf /` is blocked even under sandbox + auto_allow (CommandReviewMiddleware is independent of the permission gate)
- **PLAN mode is unaffected**: PLAN is always read-only; the sandbox doesn't change that contract

**Linux filesystem 4-tuple isolation** (`[tools.sandbox_filesystem]`, bubblewrap only):
```toml
[tools.sandbox_filesystem]
# Keep secrets away from sandboxed processes (the real prompt-injection exfiltration threat)
deny_read = ["~/.ssh", "~/.aws/credentials", "~/.gnupg"]
allow_read = ["~/.ssh/known_hosts"]  # punch a hole (SSH connection verification still works)
allow_write = ["/tmp/build", "~/.cache"]  # extra writable paths
deny_write = [".git/hooks"]  # forced read-only even inside the workspace
```
Paths support `~` (home), `./` or bare (workspace-relative), `/abs` (absolute). The `deny_read` tmpfs blackhole **must** mount before the `allow_read` ro-bind (later bwrap mounts override earlier ones) — the code guarantees this order.

**Security model, honestly stated**:
- **The blacklist/whitelist are accident prevention, NOT adversarial defense.** They intercept unintended destructive commands (wrong paths, missed consequences) but are **not a security boundary** — variable expansion (`X=/; rm -rf $X`), base64 encoding, and shell composition (`ls ; curl ... | sh`) all bypass regex matching. Real adversarial protection comes from the OS sandbox (Linux bubblewrap / `write` mode) + permission prompts; use a VM for hostile code. Do not trust untrusted code just because "a blacklist is configured".
- `job` mode: resource limits + process control, not a permission sandbox.
- `write` on Linux: bubblewrap provides full namespace isolation (out of the box) + the filesystem 4-tuple for fine-grained control.
- `write` on Windows: **no actual isolation effect today** (token is a no-op); equivalent to `job`. Real isolation awaits the ACL work.
- Whitelist degradation: in CONFIRM/AUTO_EDIT modes, commands outside the whitelist **run but carry a `[whitelist]` annotation** in the result (so the model/user knows the command is outside the trusted set); PLAN hard-rejects; FULL allows silently.
- For **fully untrusted code**, use a VM (Windows Sandbox / Docker) — do not rely on this sandbox.

**Hooks** (user lifecycle hooks, v1):

Register shell commands in config.toml via `[[hooks]]` array tables, fired at fixed points of the agent lifecycle. The config/IO contract follows the Claude Code / ZCode / Codex interop core — existing hook scripts port over as-is:

```toml
# Protect sensitive files: block writes to .env
[[hooks]]
event = "PreToolUse"                    # SessionStart | UserPromptSubmit | PreToolUse | PostToolUse | Stop
matcher = "write_file|edit_file"       # regex on tool name (tool events only); empty = all
command = "python .hooks/protect.py"   # receives the event JSON on stdin; exit 2 = block
timeout = 30                           # seconds, default 60

# Auto-format after every file write
[[hooks]]
event = "PostToolUse"
matcher = "write_file|edit_file"
command = "jq -r .tool_input.file_path | xargs prettier --write"

# Inject project conventions at session start
[[hooks]]
event = "SessionStart"
command = "cat .hooks/conventions.txt"
```

**IO contract**:
- **stdin**: one JSON object — common fields `session_id`/`cwd`/`permission_mode`/`hook_event_name` + event-specific ones (PreToolUse carries `tool_name`+`tool_input`; UserPromptSubmit carries `prompt`; PostToolUse carries `tool_response`)
- **exit codes**: `0` = pass (UserPromptSubmit/SessionStart stdout injects context, 10k-char cap); `2` = **BLOCK** (stderr becomes the reason fed to the model); anything else = **fail-open** pass + warning
- **environment**: `$CODERIO_PROJECT_DIR` always set; Git Bash preferred on Windows

**Honest positioning**: hooks are an EXTENSIBILITY point, not a security boundary — timeout/crash/non-2 exits all fail-open (a broken hook must never brick the agent loop). Hard policy belongs to the permission gate + command blacklist. Repo-level hooks live in config.toml and ride the existing repo-config trust gate (cloning a hostile repo does not silently run its hooks).

**Execution semantics**:
- **Timeouts**: 60s per hook by default (`timeout` configurable); **30s per-event total budget** — when all matching hooks together exceed it, the rest are skipped (fail-open); one slow hook can't eat the whole turn
- **Serial execution**: multiple hooks run in config order; the first blocker's reason wins, but every hook still runs (side effects preserved)
- **Environment inheritance**: hooks inherit the full environment (including API keys) — only configure hook commands you trust
- **Subagents are covered too**: research / general-purpose subagents both carry HooksMiddleware — `task()` delegation cannot bypass PreToolUse
- **Merge semantics**: user-level `[[hooks]]` + project-level `[[hooks]]` **append** (user first — under first-blocker-wins, the user's protective hook's reason takes priority); a repo's config never drops the user's protections
- Windows grandchild leak on timeout is a known limitation (the turn is no longer hostage; the root fix awaits the CREATE_SUSPENDED approach)

**v1 event set and non-goals**: SessionStart (once per session, resume included) / UserPromptSubmit (reject or inject) / PreToolUse (deny) / PostToolUse (append feedback) / Stop (notification-only — never fights the harness force-continue). Not in v1: SessionEnd (no injection point), Notification/SubagentStop/PreCompact, `updatedInput` rewriting, hook-allow skipping the permission prompt.

Supported providers:
| provider_id | Description | Protocol |
|---|---|---|
| `bigmodel_coding_plan` | Zhipu GLM Coding Plan | Anthropic |
| `stepfun_coding_plan` | StepFun Step Plan | Anthropic |
| `bigmodel_api` / `stepfun_api` | Zhipu/StepFun direct API key | Anthropic / OpenAI |
| `openai` | OpenAI direct | OpenAI |
| `anthropic` | Anthropic Claude direct | Anthropic |
| `ollama` | Local Ollama (no key) | OpenAI |
| `openai_custom` | any OpenAI-compatible endpoint | OpenAI |

API keys live in `~/.coderio/credentials` (POSIX 0600 / Windows icacls protected).

### Running

```bash
# Interactive TUI (Ctrl+O unfolds thinking, scrollable history, / autocomplete)
coderio
# or directly (Windows)
.venv/Scripts/python.exe -m coderio.cli.app
# (Linux / macOS)
.venv/bin/python -m coderio.cli.app

# Specify provider/model
coderio --provider bigmodel_coding_plan --model glm-5.2

# Headless one-shot run (CI / scripting / benchmarks)
coderio run "write a hello-world script and test it"
coderio run "fix the failing test in tests/foo.py" --quiet        # final result only
coderio run "continue" --session-id <id>                           # resume a session

# Manage skills (install pulls from GitHub; needs git on PATH)
coderio skills list
coderio skills install
```

---

## TUI commands

Inside the TUI, type `/` to trigger autocomplete:

| Command | Effect |
|------|------|
| `/help` | list all commands |
| `/exit` `/quit` | quit |
| `/config` | show current config (provider/model/mode) |
| `/mode` | switch permission mode (no arg opens a visual picker: confirm/plan/auto) |
| `/model <name>` | switch model at runtime |
| `/setup` | reconfigure provider/model (onboarding wizard, auto context-window probe) |
| `/profile` | switch saved config profiles (visual picker) |
| `/skills` | list skills (★ = activated) |
| `/cost` | show session token usage |
| `/clear` | reset context (fresh session) |
| `/sessions` | list recent sessions |
| `/resume` | resume a past session (↑↓ select, Enter resume, type to filter) |
| `/think` | unfold the latest turn's thinking |
| `/export [path]` | export the session to markdown |

**Shortcuts**:

| Key | Effect |
|------|------|
| `Ctrl+O` | unfold/fold the latest turn's thinking |
| `Esc` / `⏹ stop` | interrupt the running agent task (TUI stays up) |
| `↑↓` + `Enter` | command menu navigation (opens on `/`) |

Just type natural language to chat or assign coding tasks.

---

## Architecture

Layered monolith, dependencies flow downward:

```
CLI (cli/)              Typer app + Textual TUI + slash commands
  │
Agent (agent/)          deepagents engine + harness/permission middleware + prompt building
  │
Capabilities            tools/ · skills/ · llm/ · session/ · config/
```

### Engine: deepagents + coderio middleware

coderio uses deepagents as the engine (context management, subagents, filesystem backend) and layers middlewares on top:

| Middleware | Role |
|---|---|
| **HarnessMiddleware** | coderio's four-gate hard constraints (verify/completion/grounding/plan) — deepagents itself does not enforce verification |
| **PermissionMiddleware** | four permission tiers (plan/confirm/auto_edit/full) — controls which tools may execute |
| **CommandReviewMiddleware** | content-level review: built-in destructive-command blacklist (active even in FULL mode) + SSRF/network policies |

deepagents' default BASE_AGENT_PROMPT is neutralized — coderio's system prompt stands alone, no conflicts.

**Subagents**: built-in research subagent (read-only, physically isolated — cannot write or execute) + general-purpose (full tools, carrying the same Harness/Permission/CommandReview middleware as the main agent). The main agent delegates via the task tool; contexts are isolated.

The old ReAct engine has been removed — deepagents is the only engine.

### The harness four gates (core)

| Gate | Strength | Mechanism |
|----|------|------|
| **VerifyGate** | hard, escalating | claims "done" after writing code without running it → intercepted, forced continuation; parses shell exit codes — **failing tests (non-zero) do not count as verified**; writing docs/configs (.md/.json/.yaml) doesn't trigger verification; releases after 2 interceptions + a red warning |
| **CompletionGate** | hard | claims "done" with pending todos → intercepted |
| **GroundingGate** | hard (CODE mode only) | after writing code, cites files never read via read_file → intercepted; **ANALYZE mode skips it** — mentioning filenames in an analysis is normal (based on a 105-session audit: 98.2% false-positive rate, never caught a real hallucinated citation) |
| **PlanGate** | soft nudge | writes code without a todo list → appends a nudge to the tool result |

### Context governance

deepagents' SummarizationMiddleware manages context automatically:

| Mechanism | Trigger | Behavior |
|------|------|------|
| **offload** | tool input/output >20K tokens | large blocks saved to disk + pointer left, context preserved |
| **summarize** | tokens reach 60% of the window (configurable `trigger_ratio`) | old messages LLM-summarized + originals offloaded to `/conversation_history/` |
| **checkpoint** | every turn end | graph state persisted to sqlite; next turn sends only the new message |

### Explicit state machine

The agent's execution phase is derived live and shown in the status bar (`step 3 · [implement] thinking · 12.4s`):

```
explore (read_file/grep) → plan (first write, no todos) → implement (write + todos)
  → verify (bash pytest) → complete
```

Each turn's phase timeline persists to the session jsonl (`kind="phase_timeline"`) for replay/debugging, but stays invisible to the model (never pollutes context).

Full architecture: [`docs/coderio-architecture.md`](docs/coderio-architecture.md).

---

## Testing

```bash
# Full unit test suite (~15s)
# Windows (Git Bash):
.venv/Scripts/python.exe -m pytest -q
# Linux / macOS:
.venv/bin/python -m pytest -q

# By module
.venv/Scripts/python.exe -m pytest tests/agent/ -v    # Windows
.venv/bin/python -m pytest tests/agent/ -v            # Linux / macOS

# Live verification (real model endpoint; requires ANTHROPIC_API_KEY)
# harness four gates against a real model:
ANTHROPIC_API_KEY=<key> .venv/Scripts/python.exe scripts/verify_harness_live.py   # Windows
ANTHROPIC_API_KEY=<key> .venv/bin/python scripts/verify_harness_live.py           # Linux / macOS
# deepagents engine integration:
ANTHROPIC_API_KEY=<key> .venv/Scripts/python.exe scripts/verify_deepagent_live.py # Windows
```

Three test layers: unit tests (logic) + live verification (real integration) + manual experience testing.

---

## Tech stack

| Dependency | Purpose |
|------|------|
| langchain >=0.3 | agent foundation |
| langgraph >=0.2 | state-graph orchestration |
| langchain-anthropic >=0.2 | Zhipu/StepFun endpoints (Anthropic protocol) |
| textual >=0.40 | interactive TUI |
| rich >=13 | terminal rendering |
| typer >=0.12 | CLI framework |
| deepagents >=0.6 | production engine (context management, subagents, filesystem backend) |

---

## Project structure

```
src/coderio/
├── agent/          # deepagents engine, harness/permission middleware, prompts, stream protocol
├── cli/            # Typer app, Textual TUI, slash commands, credentials/onboarding, headless run
├── tools/          # tool set + permission gate + langchain adapters + sandbox
├── skills/         # SkillStore 3-layer loading + Lion-Skills (bundled)
├── config/         # 3-layer TOML config merge + repo trust
├── session/        # jsonl session storage + resume
└── llm/            # model factory (provider registry)
```

Lion-Skills ship as bundled skills (`src/coderio/skills/lion-skills/`) — no separate install needed.

---

## Known limitations

- **Windows encoding**: shell output under GBK locales has a built-in mitigation (`_WinLocalShellBackend` decodes bytes with errors='replace')
- **Windows write sandbox**: `sandbox_mode = "write"` is currently equivalent to `job` on Windows (documented above under Sandbox)

---

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT (see [LICENSE](LICENSE)). Bundled Lion-Skills are also MIT (see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)).
