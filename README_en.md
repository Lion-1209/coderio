# coderio

[中文](README.md) | **English**

> A skill-driven coding agent with a structural harness, foldable-thinking TUI, and crew orchestration. Built on langchain + langgraph + Lion-Skills. Windows-first, cross-platform.

**coderio** is a skill-driven coding agent. Its "backbone" is the [Lion-Skills](https://github.com/Lion-1209/Lion-Skills) suite (clarify→spec→task→execute→verify→commit workflow). coderio pairs it with real working tools, a **harness state-control layer** that enforces the workflow, and an interactive Textual TUI. Reference targets: claude code / codex / zcode.

Core philosophy: **skills are the playbook, the harness is discipline, tools are the hands.** Three layers, none substituting for another.

---

## Features

- **Harness four-gate hard constraint**: when the agent writes code but tries to declare "done" without running it, the harness intercepts and force-continues — not a soft prompt rule, but system-level structural control (based on tool-call ground truth)
- **Intent classification**: automatically distinguishes CODE / QA / ANALYZE intents — coding tasks follow the workflow, questions get direct answers
- **Progressive disclosure**: skill bodies load on-demand, system prompt ~2K tokens instead of dumping everything
- **Interactive TUI**: Textual terminal UI with foldable thinking (Ctrl+O), streaming output, tool-call status bar (animated spinner + step + timer), slash-command autocomplete, session resume picker
- **Two modes**: interactive single-agent (daily use) + 6-agent crew pipeline (large tasks, LangGraph orchestration)
- **Tool error resilience**: tool failures become tool results fed back to the model for self-correction, never crash the turn
- **Multi-provider**: Zhipu GLM / StepFun StepFun coding plans (Anthropic protocol) + OpenAI + Anthropic + Ollama + custom OpenAI-compatible endpoints

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

# 6-agent crew pipeline (large tasks)
coderio crew "implement a todo list CLI tool" --auto

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
Agent layer (agent/)      ReAct loop + harness state control + prompt building
  │
Orchestration (crew/)     6-agent LangGraph StateGraph (optional advanced mode)
  │
Capability layer          tools/ · skills/ · llm/ · session/ · config/
```

### Two run modes

| | Single-agent (TUI) | Crew (pipeline) |
|---|---|---|
| Use case | Daily interaction, Q&A, coding tasks | Large tasks, full feature development |
| Agents | 1 (all tools) | 6 (tools physically isolated per stage) |
| Harness | Hard constraint active | Inactive (crew has its own verify→fix loop) |
| Orchestration | ReAct loop | LangGraph StateGraph + interrupt |

### Harness four gates (core)

| Gate | Strength | Mechanism |
|------|----------|-----------|
| **VerifyGate** | Hard, progressive escalation | Wrote code but didn't run bash before declaring "done" → intercept, inject forced continuation; released after 2 attempts + red warning |
| **CompletionGate** | Hard | Non-trivial todos remain when declaring "done" → intercept |
| **GroundingGate** | Hard | Cites code locations never actually read when declaring "done" → intercept (prevents analysis built on assumptions) |
| **PlanGate** | Soft nudge | Writes code without any todos → append nudge to tool result |

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
| langchain >=0.3 | ReAct agent foundation |
| langgraph >=0.2 | Crew pipeline state graph orchestration |
| langchain-anthropic >=0.2 | Zhipu/StepFun endpoint access (Anthropic protocol) |
| textual >=0.40 | Interactive TUI |
| rich >=13 | Terminal rendering |
| typer >=0.12 | CLI framework |
| deepagents >=0.6 | Experimental engine (optional: `pip install -e ".[deepagent]"`) |

---

## Project Structure

```
src/coderio/
├── agent/          # ReAct loop, harness, prompts, streaming protocol
├── cli/            # Typer app, Textual TUI, slash commands, credentials/onboarding
├── crew/           # 6-agent LangGraph pipeline (orchestrator/agents/state)
├── tools/          # 12 tools + permission gate + langchain adapter
├── skills/         # SkillStore 3-layer loading + Lion-Skills 0.3.0 (bundled)
├── config/         # 3-layer TOML config merge
├── session/        # jsonl session storage + resume
└── llm/            # Model factory (provider registry)
```

Lion-Skills is distributed as a bundled skill (`src/coderio/skills/lion-skills/`), no separate install needed.

---

## Known Limitations

- **deepagents engine is experimental**: harness as middleware works, but the default is still the ReAct engine. deepagents is now an optional dependency
- **Windows encoding**: shell output has a built-in compatibility solution for GBK locale
- **Crew persistence**: currently uses in-memory MemorySaver (per-session), sqlite persistence is future work

---

## Contributing

Contributions welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT (see [LICENSE](LICENSE)). Bundled Lion-Skills is also MIT (see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)).
