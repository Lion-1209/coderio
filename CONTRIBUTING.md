# Contributing to coderio

[中文文档](README.md) | **English**

Thanks for your interest in contributing! This guide covers the basics.

## Development setup

```bash
git clone <repo-url> coderio
cd coderio
python -m venv .venv

# Windows (Git Bash)
.venv/Scripts/python.exe -m pip install -e ".[dev]"

# Linux / macOS
.venv/bin/python -m pip install -e ".[dev]"
```

Set your API key:

```bash
export ANTHROPIC_API_KEY="your-key-here"
```

## Running tests

```bash
# All tests
# Windows: .venv/Scripts/python.exe -m pytest -q
# Linux/macOS: .venv/bin/python -m pytest -q
.venv/Scripts/python.exe -m pytest -q

# Just the CLI/TUI tests
.venv/Scripts/python.exe -m pytest tests/cli/ -q

# A single test
.venv/Scripts/python.exe -m pytest tests/cli/test_truncation.py -v
```

Tests mock the model (no API calls needed). E2E tests in `tests/e2e/` run the
CLI as a subprocess.

## Code style

- Python 3.11+. Use `from __future__ import annotations` at the top of every module.
- Type hints everywhere. Use `Protocol` for interfaces, dataclasses for config.
- Functions that can fail should return error strings (not raise) when the caller
  is the agent loop — tool errors become tool results the model can react to.
- Comments: explain WHY, not WHAT. Keep them concise.

## Architecture overview

The codebase is a layered monolith (dependencies flow downward):

```
cli/          Typer app + Textual TUI + slash commands
crew/         6-agent LangGraph orchestration
agent/        ReAct loop, structural harness (4 gates)
llm/          Model factory (ChatAnthropic / ChatOpenAI)
tools/        bash, file ops, search, web, todo, edit
skills/       Skill store (3-layer: bundled + user + project)
session/      Conversation persistence (.jsonl)
config/       TOML config loader
```

Read `docs/coderio-architecture.md` for the full design.

## Pull requests

1. Fork the repo and create a feature branch.
2. Write tests for your change.
3. Make sure all tests pass: `pytest -q`.
4. Keep the PR focused — one feature/fix per PR.
5. Describe what changed and why.

## Reporting bugs

Include:
- Your OS and terminal (e.g. Windows + VSCode integrated terminal).
- The model and provider you're using.
- Steps to reproduce.
- The session file if relevant (`~/.coderio/sessions/*.jsonl`).
