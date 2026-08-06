"""Performance baseline tests with real model calls.

These tests require a real API key and network access. They are skipped
automatically when no key is configured. Run explicitly:

    ANTHROPIC_API_KEY=<key> .venv/Scripts/python.exe -m pytest tests/agent/test_perf_baseline.py -v

The tests record timing and token metrics for three scenarios:
1. Pure Q&A (greeting, no tools)
2. Code task (write file + verify)
3. Analysis task (read file + summarize)

Results are asserted against baselines to catch performance regressions.
"""

from __future__ import annotations

import os
import time

import pytest

# Skip entire module unless explicitly enabled with CODERIO_PERF_TESTS=1.
# This prevents CI environments (which may have stale/invalid API keys in
# env vars) from accidentally running real model calls and failing.
_PERF_ENABLED = os.environ.get("CODERIO_PERF_TESTS") == "1"
_HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("Z_API_KEY"))
try:
    import deepagents  # noqa: F401

    _HAS_DEEP = True
except ImportError:
    _HAS_DEEP = False

pytestmark = pytest.mark.skipif(
    not (_PERF_ENABLED and _HAS_KEY and _HAS_DEEP),
    reason="requires CODERIO_PERF_TESTS=1 + API key + deepagents",
)


class _PerfStream:
    """Stream handler that records timing and token metrics."""

    def __init__(self):
        self.t_start = 0.0
        self.t_first_token: float | None = None
        self.t_finish: float | None = None
        self.tokens_out = 0
        self.tool_calls: list[str] = []
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def on_step_start(self, step=1):
        self.t_start = time.monotonic()

    def on_token(self, text):
        if self.t_first_token is None:
            self.t_first_token = time.monotonic()
        self.tokens_out += len(text)

    def on_thinking(self, text):
        pass

    def on_tool_start(self, name, args, **kw):
        self.tool_calls.append(name)

    def on_tool_end(self, name, result):
        pass

    def on_finish(self):
        self.t_finish = time.monotonic()

    def on_turn_end(self, writes):
        pass

    def add_usage(self, meta):
        for k in ("input_tokens", "output_tokens"):
            if k in meta:
                self.usage[k] += meta[k]

    @property
    def total_time(self) -> float:
        return (self.t_finish or 0) - self.t_start

    @property
    def first_token_latency(self) -> float:
        if self.t_first_token and self.t_start:
            return self.t_first_token - self.t_start
        return 0.0


def _make_model():
    from coderio.config import load_config
    from coderio.llm import build_chat_model

    cfg = load_config()
    return build_chat_model(cfg)


def _make_session(tmp_path):
    from coderio.session.store import Session

    return Session.create(save_dir=tmp_path, meta={"model": "perf-test"})


# Baselines: generous upper bounds to catch regressions without being flaky.
# These reflect step-3.7-flash performance on 2026-08-06.
_QA_TIME_LIMIT = 15.0  # seconds (observed: 2.44s)
_QA_TOOL_LIMIT = 0  # pure Q&A should use zero tools
_ANALYSIS_TIME_LIMIT = 15.0  # seconds (observed: 2.88s)
_ANALYSIS_INPUT_TOKEN_LIMIT = 50_000  # observed: 16K, allow headroom


def test_perf_qa_no_tools(tmp_path):
    """Pure Q&A: greeting should be fast, use zero tools."""
    from coderio.agent.deep_loop import run_deep_agent

    model = _make_model()
    session = _make_session(tmp_path)
    stream = _PerfStream()

    result = run_deep_agent("你好", model, session, stream=stream, harness_enabled=False, workdir=str(tmp_path))

    assert stream.t_finish is not None, "on_finish was not called"
    assert stream.total_time < _QA_TIME_LIMIT, f"QA took {stream.total_time:.1f}s (limit {_QA_TIME_LIMIT}s)"
    assert len(stream.tool_calls) == _QA_TOOL_LIMIT, f"QA used {stream.tool_calls} (expected zero)"
    assert len(result) > 0, "empty response"


def test_perf_analysis_single_read(tmp_path):
    """Analysis task: read one file + summarize should be fast."""
    from coderio.agent.deep_loop import run_deep_agent

    # Create a small file to analyze.
    (tmp_path / "data.py").write_text("x = 42\n", encoding="utf-8")

    model = _make_model()
    session = _make_session(tmp_path)
    stream = _PerfStream()

    result = run_deep_agent(
        "读取 /data.py 并简短说明它的功能。",
        model,
        session,
        stream=stream,
        harness_enabled=False,
        workdir=str(tmp_path),
    )

    assert stream.t_finish is not None
    assert stream.total_time < _ANALYSIS_TIME_LIMIT, f"Analysis took {stream.total_time:.1f}s"
    assert stream.usage["input_tokens"] < _ANALYSIS_INPUT_TOKEN_LIMIT, (
        f"Analysis used {stream.usage['input_tokens']} input tokens (limit {_ANALYSIS_INPUT_TOKEN_LIMIT})"
    )
    assert "read_file" in stream.tool_calls, f"expected read_file in {stream.tool_calls}"
    assert len(result) > 0
