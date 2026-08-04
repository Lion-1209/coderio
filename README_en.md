# coderio

[中文](README.md) | **English**

> A skill-driven coding agent with a structural harness, foldable-thinking TUI, and deepagents engine. Built on langchain + langgraph + deepagents + Lion-Skills. Windows-first, cross-platform.

## Why this project

It started as a way to burn through some free tokens from StepFun, and to go through the full langchain tech stack to build an agent. Once the framework was up, a plain REPL + CLI didn't feel cool enough — so the TUI journey began. I'm quite happy with how the TUI turned out.

That said, the overall framework hasn't been finely tuned. This is a demo built in my spare time, not a product. The goal of open-sourcing is simply to offer a small reference for anyone using the langchain stack: how to integrate the deepagents engine, how to do structural harness constraints, how to do streaming TUI rendering — the code is all here, it runs, feel free to play with it.

> About the name: **coderio = code + rio** (not coder + io). My English name is Lion — I wanted "codelion" but it sounded weird, so coderio it is.

---

**coderio** is a skill-driven coding agent. Its "backbone" is the [Lion-Skills](https://github.com/Lion-1209/Lion-Skills) suite (clarify→spec→task→execute→verify→commit workflow). coderio pairs it with real working tools, a **harness state-control layer** that enforces the workflow, and an interactive Textual TUI. Reference targets: claude code / codex / zcode.

Core philosophy: **skills are the playbook, the harness is discipline, tools are the hands.** Three layers, none substituting for another.

---

## Features

- **Harness four-gate hard constraint**: when the agent writes code but tries to declare "done" without running it, the harness intercepts and force-continues — not a soft prompt rule, but system-level structural control (based on tool-call ground truth)
- **Explicit state machine**: real-time phase derivation (explore→plan→implement→verify→complete), status bar shows task phase + model activity dual-axis; per-turn phase timeline persisted to session for replay/debugging
- **deepagents engine**: production engine built on [deepagents](https://github.com/langchain-ai/deepagents), with built-in context management (offload + summarization), subagents (task tool, including a read-only research subagent), filesystem backend, and persistent checkpoints (SqliteSaver); **fully replaces deepagents' default prompt** — coderio's system prompt is the only one, no conflicts
- **Persistent checkpoints**: graph state persists across turns to sqlite; only the new user message is passed (not full history); SummarizationMiddleware's accumulated state persists correctly
- **Automatic context compaction**: deepagents' SummarizationMiddleware triggers at 85% of the context window — old messages are offloaded to files + LLM-summarized, preserving recent context
- **Intent classification**: automatically distinguishes CODE / QA / ANALYZE intents — coding tasks follow the workflow, questions get direct answers (bilingual CN/EN signal words)
- **Progressive disclosure**: skill bodies load on-demand, system prompt ~2K tokens instead of dumping everything
- **Interactive TUI**: Textual terminal UI with foldable thinking (Ctrl+O), streaming output, tool-call status bar (animated spinner + step + task phase + timer + **turn token count**), slash-command autocomplete, **collapsible TODO panel** (live progress ✓/→/○), **vertical permission menu** (↑↓ + Enter, zcode/codex style), **session management** (`/resume` + Del to delete), **visual pickers** (`/mode` `/profile`), **file change visualization**, **task interrupt** (Esc / ⏹ button), **error recovery**
- **deepagents engine**: production engine built on [deepagents](https://github.com/langchain-ai/deepagents), with built-in context management (offload + summarization), subagents (task tool), and filesystem backend; coderio's harness four gates + four-tier permissions retained as middleware
- **Tool error resilience**: tool failures become tool results fed back to the model for self-correction, never crash the turn
- **Multi-provider + named profiles**: Zhipu GLM / StepFun coding plans (Anthropic protocol) + OpenAI + Anthropic + Ollama + custom; supports multiple config profiles with `/profile` runtime switching

---

## Quick Start

### Install

```bash
git clone https://github.com/Lion-1209/coderio.git coderio
cd coderio
python -m venv .venv

# Windows (Git Bash)
.venv/Scripts/python.exe -m pip install -e ".[dev]"

# Linux / macOS
.venv/bin/python -m pip install -e ".[dev]"
```

Requires: Python 3.11+. On Windows, install Git Bash (the bash tool depends on it).

### Configuration

First run triggers the onboarding wizard (pick provider, choose model, enter API key). Configuration is auto-written to `~/.coderio/config.toml` and `~/.coderio/credentials`. You can also configure manually:

```bash
# ~/.coderio/config.toml
[model]
provider_id = "bigmodel_coding_plan"   # Zhipu/StepFun/OpenAI/Anthropic/Ollama/custom
default = "glm-5.2"

[tools]
permission_mode = "auto"                # confirm | plan | auto

[context]
enabled = true                          # auto-compaction for long sessions (default on)
trigger_ratio = 0.75                    # compact at 75% of the context window
keep_recent = 8                         # preserve the most recent N messages verbatim
model_context_limit = 128000            # model context window size (tokens)
```

Supported providers:
| provider_id | Description | Protocol |
|---|---|---|
| `bigmodel_coding_plan` | Zhipu GLM Coding Plan | Anthropic |
| `stepfun_coding_plan` | StepFun Step Plan | Anthropic |
| `bigmodel_api` / `stepfun_api` | Zhipu/StepFun API Key direct | Anthropic / OpenAI |
| `openai` | OpenAI direct | OpenAI |
| `anthropic` | Anthropic Claude direct | Anthropic |
| `ollama` | Local Ollama (no key needed) | OpenAI |
| `openai_custom` | Any OpenAI-compatible endpoint | OpenAI |

API keys are stored in `~/.coderio/credentials` (POSIX 0600 / Windows icacls protected).

### Run

```bash
# Interactive TUI (Ctrl+O to expand thinking, scrollable history, / command autocomplete)
coderio
# Or directly (Windows)
.venv/Scripts/python.exe -m coderio.cli.app
# (Linux / macOS)
.venv/bin/python -m coderio.cli.app

# Specify provider/model
coderio --provider bigmodel_coding_plan --model glm-5.2

# Manage skills
coderio skills list
coderio skills install
```

---

## TUI Commands

Inside the TUI, type `/` to trigger command autocomplete:

| Command | Action |
|---------|--------|
| `/help` | Show all commands |
| `/exit` `/quit` | Exit |
| `/config` | View current config (provider/model/mode) |
| `/mode <confirm\|plan\|auto>` | Switch permission mode |
| `/model <name>` | Switch model at runtime |
| `/skills` | List skills (★ = active) |
| `/cost` | View token usage for this session |
| `/clear` | Reset context (new session) |
| `/sessions` | List recent sessions |
| `/resume` | Resume a past session (↑↓ to select, Enter to resume, type to filter) |

Type natural language directly to chat or assign coding tasks.

---

## Architecture

Layered monolith, dependencies flow downward:

```
CLI layer (cli/)          Typer app + Textual TUI + slash commands
  │
Agent layer (agent/)      deepagents engine + harness/permission middleware + prompt building
  │
Capability layer          tools/ · skills/ · llm/ · session/ · config/
```

### Engine: deepagents + coderio middleware

coderio uses deepagents as its primary engine (context management, subagents, filesystem backend), with two middleware layers on top:

| Middleware | Purpose |
|---|---|
| **HarnessMiddleware** | coderio's four-gate hard constraint (verify/completion/grounding/plan) — deepagents itself doesn't enforce verification |
| **PermissionMiddleware** | four-tier permissions (plan/confirm/auto_edit/full) + workspace path boundary |

The old ReAct engine has been removed — deepagents is the sole engine.

### Harness four gates (core)

| Gate | Strength | Mechanism |
|------|----------|-----------|
| **VerifyGate** | Hard, progressive escalation | Wrote code but didn't run bash before declaring "done" → intercept, inject forced continuation; released after 2 attempts + red warning |
| **CompletionGate** | Hard | Non-trivial todos remain when declaring "done" → intercept |
| **GroundingGate** | Hard (CODE mode only) | After writing code, cites files never read_file'd when declaring "done" → intercept; **ANALYZE mode (pure reads) skips** — mentioning filenames in analysis is normal (based on 105-session audit: 98.2% false positive rate, never caught a real hallucinated citation) |
| **PlanGate** | Soft nudge | Writes code without any todos → append nudge to tool result |

### Context management

deepagents' SummarizationMiddleware manages context automatically:

| Mechanism | Trigger | Behavior |
|-----------|---------|----------|
| **Offload** | tool input/output >20k tokens | Large content stored to file + pointer left, doesn't consume context |
| **Summarize** | token count reaches 85% of window | Old messages LLM-summarized + original offloaded to `/conversation_history/` |
| **Checkpoint** | each turn ends | graph state persisted to sqlite, next turn only passes the new message |

### Explicit state machine

Agent execution phases are derived in real time and shown in the status bar (`Step 3 · [implement] thinking · 12.4s`):

```
explore (read_file/grep) → plan (first write, no todo) → implement (write + todo)
  → verify (bash pytest) → complete
```

Each turn's phase timeline is persisted to the session jsonl (`kind="phase_timeline"`), available for replay/debugging, but invisible to the model (doesn't pollute context).

Full architecture design: [`docs/coderio-architecture.md`](docs/coderio-architecture.md) (Chinese).

---

## Testing

```bash
# Full unit tests (~15s)
# Windows (Git Bash):
.venv/Scripts/python.exe -m pytest -q
# Linux / macOS:
.venv/bin/python -m pytest -q

# By module
.venv/Scripts/python.exe -m pytest tests/agent/ -v    # Windows
.venv/bin/python -m pytest tests/agent/ -v            # Linux / macOS

# Live verification (connects to real model endpoint, requires ANTHROPIC_API_KEY)
ANTHROPIC_API_KEY=<key> .venv/Scripts/python.exe scripts/verify_harness_live.py   # Windows
ANTHROPIC_API_KEY=<key> .venv/bin/python scripts/verify_harness_live.py           # Linux / macOS
```

Three-layer test design: unit tests (logic) + live verification (real integration) + manual experience testing.

---

## Tech Stack

| Dependency | Purpose |
|------------|---------|
| langchain >=0.3 | Agent foundation |
| langgraph >=0.2 | State graph orchestration |
| langchain-anthropic >=0.2 | Zhipu/StepFun endpoint access (Anthropic protocol) |
| textual >=0.40 | Interactive TUI |
| rich >=13 | Terminal rendering |
| typer >=0.12 | CLI framework |
| deepagents >=0.6 | Production engine (context management, subagents, filesystem backend) |

---

## Project Structure

```
src/coderio/
├── agent/          # deepagents engine, harness/permission middleware, prompts, streaming protocol
├── cli/            # Typer app, Textual TUI, slash commands, credentials/onboarding
├── tools/          # Tool set + permission gate + langchain adapter
├── skills/         # SkillStore 3-layer loading + Lion-Skills 0.3.0 (bundled)
├── config/         # 3-layer TOML config merge
├── session/        # jsonl session storage + resume
└── llm/            # Model factory (provider registry)
```

Lion-Skills is distributed as a bundled skill (`src/coderio/skills/lion-skills/`), no separate install needed.

---

## Known Limitations

- **Windows encoding**: shell output has a built-in compatibility solution for GBK locale (`_WinLocalShellBackend` decodes bytes with errors='replace')

---

## Contributing

Contributions welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT (see [LICENSE](LICENSE)). Bundled Lion-Skills is also MIT (see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)).
