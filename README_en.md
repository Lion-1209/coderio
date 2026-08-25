# coderio

[中文](README.md) | **English**

> The agent claims "done" without running the tests? coderio's harness stops it.
> A local coding agent with **native Zhipu GLM & StepFun Step coding-plan support** — four-tier permissions, layered sandbox, MCP, lifecycle hooks, and an interactive TUI.

![demo](demo.gif)

## Install

```bash
pip install coderio
coderio    # onboarding wizard on first launch (pick provider, paste API key, auto context-window probe)
```

Requires Python 3.11+; Git Bash on Windows. Linux / macOS supported.

## Why coderio

The shared weakness of coding agents: **the model says "I'm done" and you just have to trust it**. coderio turns that sentence into a structural constraint—

### The Four Gates: the agent can't lie to you

| Gate | Behavior |
|---|---|
| **VerifyGate** | Wrote code, never ran it, wants to finish → intercepted, forced to continue. Parses real exit codes — **a failing test run does NOT count as verified** |
| **CompletionGate** | Declares done with pending todos → intercepted |
| **GroundingGate** | Cites files it never read → intercepted |
| **PlanGate** | Writes code without a todo list → soft nudge |

Not a prompt-level soft rule — a system-level control based on tool-call ground truth. Claude Code and Codex don't have this.

### Native Chinese coding-plan support

Zhipu **GLM Coding Plan** and StepFun **Step Plan** work out of the box (direct Anthropic-protocol connection) — your subscription quota runs a local agent, no proxies, no middle layer. Also supports OpenAI / Anthropic / Ollama / any OpenAI-compatible endpoint, with multi-profile switching.

### Layered security, honestly stated

- Four permission tiers (plan read-only / confirm per-action / auto_edit / full)
- Command blacklist + whitelist (accident prevention); Linux bubblewrap OS sandbox (boundary enforcement)
- First-use repo-config trust confirmation (hostile-repo protection); web_fetch SSRF protection
- The blacklist/whitelist are **accident prevention, not adversarial defense** — adversarial protection comes from the sandbox + permissions; use a VM for hostile code

## Feature highlights

- **Interactive TUI**: streaming output, foldable thinking (Ctrl+O), collapsible TODO panel, vertical permission menu, task interruption (Esc), slash-command autocomplete, session management
- **Custom slash commands**: `.coderio/commands/*.md` (project/user layers) turn prompt templates into `/commands` with `$ARGUMENTS` substitution; built-ins can never be shadowed
- **Custom subagents**: `.coderio/agents/*.md` define personas invokable via `task(subagent_type=...)` — you customize WHO the agent is, its capabilities stay on the read-only stack
- **File rollback**: every structured agent write is auto-checkpointed; `/undo` reverts step by step (a bad edit is one command from gone)
- **Plan artifact**: the task list auto-mirrors to `.coderio/plan.md`; edit it by hand and the agent adopts your version at the next turn
- **Headless mode**: `coderio run "task"` one-shot execution (CI / scripts / benchmarks) with graded exit codes
- **MCP support**: connect external tools via `.mcp.json` (Claude Code-compatible format), managed with `coderio mcp`
- **Lifecycle hooks**: `[[hooks]]` run your commands at PreToolUse / PostToolUse / UserPromptSubmit (exit 2 = block) — IO contract compatible with Claude Code
- **Three-layer skills**: bundled + user + project, progressive disclosure saves context
- **Context governance**: auto-compaction (60% window trigger), large-block offload, sqlite checkpoints across turns
- **Subagents**: research (read-only, double-enforced) + general-purpose (inherits the main agent's full security stack)
- **Engineering discipline**: 950+ tests, 82% coverage (CI floor 75%), mypy hard gate, uv.lock, 3 OS × 2 Python CI matrix

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

MCP, the sandbox 4-tuple, and more: [docs/coderio-architecture.md](docs/coderio-architecture.md).

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

- The Windows write-sandbox currently equals job mode (true isolation awaits the ACL work — documented honestly)
- Blacklist/whitelist are accident-prevention by design (regex can be bypassed by obfuscation); use the sandbox / a VM for adversarial scenarios

## Origin

A spare-time project, open-sourced as a working reference for developers building their own coding agents. The name is **code + rio** (the author's English name is Lion; "codelion" sounded odd).

## Contributing & License

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). MIT License.
