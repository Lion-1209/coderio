# coderio

[中文](README.md) | **English**

> The agent claims "done" without running the tests? coderio's harness stops it — system-level enforcement, not prompt politeness.
> A fully open-source local coding agent that runs on **your** endpoint: native Zhipu GLM & StepFun coding-plan support, any Anthropic-protocol or OpenAI-compatible API, four-tier permissions, a layered sandbox, MCP, lifecycle hooks, and an interactive TUI.

![demo](demo.gif)

## Install

```bash
pip install coderio
coderio    # onboarding wizard on first launch (pick provider, paste API key, auto context-window probe)
```

Requires Python 3.11+; Git Bash on Windows. Linux / macOS supported.

Optional: MCP external tools (`.mcp.json`) need an extra — `pip install "coderio[mcp]"`; without it MCP tools stay disabled (a notice appears at startup).

## Why coderio

The shared weakness of coding agents: **the model says "I'm done" and you just have to trust it**. Most agents treat that as a prompting problem — coderio treats it as an enforcement problem. The harness holds the termination decision and checks tool-call ground truth (what actually ran, what actually got read) before a turn is allowed to end.

### The Four Gates: the agent can't talk its way past the harness

| Gate | Behavior |
|---|---|
| **VerifyGate** | Wrote code, never ran it, wants to finish → intercepted, forced to continue. Parses real exit codes — **a failing test run does NOT count as verified**, and `echo app.py` doesn't count as "running it" |
| **CompletionGate** | Declares done with pending todos → intercepted |
| **GroundingGate** | Cites files it never actually read → intercepted |
| **PlanGate** | Writes code without a todo list → soft nudge |

Not prompt-level soft rules — a system-level control based on tool-call ground truth, with escalating enforcement: the agent is force-continued twice, then released with a loud warning. Never infinite, never silent.

![harness intercept: the model claims done, unverified](docs/images/harness-warn.svg)

### Where coderio fits

- **Your endpoint, no middleman.** Bring the API you already pay for: Zhipu **GLM Coding Plan** and StepFun **Step Plan** connect directly over the Anthropic protocol (subscription quota runs a local agent — no proxy layer), and OpenAI / Anthropic / Ollama / any OpenAI-compatible endpoint work the same way, with multi-profile switching.
- **Enforcement, not vibes.** Verified completion, trust-gated repo config, four-tier permissions, a command blacklist for accident prevention, SSRF-protected web_fetch — each layer isolated in its own module.
- **A reference you can actually read.** A layered monolith (~16k lines of Python) where harness, permissions, sandbox and trust are separate, individually tested modules — built as a working reference for people building their own agents.

### Layered security, honestly stated

- Four permission tiers (plan read-only / confirm per-action / auto_edit / full)
- Command blacklist + whitelist (accident prevention); Linux bubblewrap OS sandbox (boundary enforcement)
- First-use repo-config trust confirmation — `config.toml`, `.mcp.json`, skills, **custom commands and custom agents** are all gated (hostile-repo protection); web_fetch SSRF protection
- The blacklist/whitelist are **accident prevention, not adversarial defense** — adversarial protection comes from the sandbox + permissions; use a VM for hostile code

In confirm mode, every write is one keystroke away from allow / deny / custom reply:

![confirm mode's vertical permission menu](docs/images/confirm-menu.svg)

## First task in 3 minutes

After install + onboarding, launch the TUI and just talk:

```bash
coderio
```

Try this:

```
Find the failing test under tests/, fix it, and run the suite to confirm
```

What you'll see:

1. **Thinking streaming live** (Ctrl+O folds/unfolds each round)
2. **Every tool call on its own line** — files read, commands run, output capped to one summary line
3. A live **TODO panel** whenever the agent plans with write_todos:

![TODO panel](docs/images/todo-panel.svg)

4. The final answer in a blue **coderio** panel, plus a one-line "files changed this turn" summary; in confirm mode, every disk write goes through the vertical menu above
5. Skeptical? `/undo` rolls the agent's writes back step by step; `/think` unfolds what it was thinking

No TUI? Headless works the same:

```bash
coderio run "count the Python lines under src/ and summarize" --quiet
```

## Feature highlights

- **Interactive TUI**: streaming output, foldable thinking (Ctrl+O), collapsible TODO panel, vertical permission menu, task interruption (Esc), slash-command autocomplete, session management
- **Custom slash commands**: `.coderio/commands/*.md` (project/user layers) turn prompt templates into `/commands` with `$ARGUMENTS` substitution; built-ins can never be shadowed
- **Custom subagents**: `.coderio/agents/*.md` define personas invokable via `task(subagent_type=...)` — you customize WHO the agent is, its capabilities stay on the read-only stack
- **File rollback**: every structured agent write is auto-checkpointed; `/undo` reverts step by step (a bad edit is one command from gone)
- **Plan artifact**: the task list auto-mirrors to `.coderio/plan.md`; edit it by hand and the agent adopts your version at the next turn (read-only PLAN mode doesn't write it)
- **Headless mode**: `coderio run "task"` one-shot execution (CI / scripts / benchmarks) with graded exit codes
- **MCP support**: connect external tools via `.mcp.json` (Claude Code-compatible format), managed with `coderio mcp`
- **Lifecycle hooks**: `[[hooks]]` run your commands at PreToolUse / PostToolUse / UserPromptSubmit (exit 2 = block) — IO contract compatible with Claude Code
- **Three-layer skills**: bundled + user + project, progressive disclosure saves context
- **Context governance**: deepagents auto-summarization + large-block offload, sqlite checkpoints across turns
- **Subagents**: research (read-only, double-enforced) + general-purpose (inherits the main agent's full security stack)
- **Engineering discipline**: 1280+ tests, coverage gated at 75% in CI, mypy hard gate, uv.lock, 3 OS × 2 Python CI matrix

<details>
<summary><b>Config example</b> (click to expand)</summary>

```toml
# ~/.coderio/config.toml
[model]
provider_id = "bigmodel_coding_plan"   # Zhipu/StepFun coding plan, or openai/anthropic/ollama/custom
default = "glm-5.2"

[tools]
permission_mode = "confirm"            # plan | confirm | auto_edit | full
sandbox_mode = "off"                   # off | job (resource limits) | write (Linux file-write isolation)

# Lifecycle hooks (Claude Code-compatible contract)
[[hooks]]
event = "PreToolUse"
matcher = "write_file|edit_file"
command = "python .hooks/protect.py"   # JSON on stdin; exit 2 = block
```

Every config field — MCP, hooks, the sandbox 4-tuple — is documented in [docs/CONFIG.md](docs/CONFIG.md); architecture design in [docs/coderio-architecture.md](docs/coderio-architecture.md).

</details>

## Common commands

```bash
coderio                                              # interactive TUI
coderio run "fix the failing test" --quiet           # headless one-shot
coderio run "task" --dangerously-skip-permissions    # full access (explicit opt-in)
coderio mcp add github --type http --url ...          # manage MCP
coderio skills install                               # install skill suites
```

Type `/` inside the TUI for all commands (/resume sessions, /mode permissions, /undo file writes, /think unfold reasoning).

## Known limitations

- The Windows write-sandbox currently equals job mode (true isolation awaits the ACL work — documented honestly); sandboxed commands run through `cmd /c`, so quoting/`$VAR` semantics differ from the Git Bash plain path; **macOS has no OS-level sandbox** (bubblewrap is Linux-only) — use a VM for adversarial scenarios
- Where a sandbox can't deliver (Linux without bubblewrap), degradation is explicit: tool output is marked `[sandbox unavailable: …]`, and `auto_allow_if_sandboxed` is disabled — execute prompts per command instead of running with "zero isolation + zero confirmation"
- Blacklist/whitelist are accident-prevention by design (regex can be bypassed by obfuscation); use the sandbox / a VM for adversarial scenarios

## Origin

A spare-time project, open-sourced as a working reference for developers building their own coding agents. The name is **code + rio** (the author's English name is Lion; "codelion" sounded odd). Project direction and maintenance status: [ROADMAP_en.md](ROADMAP_en.md).

## Contributing & License

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). MIT License.
