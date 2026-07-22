"""Tests: provider context-window discovery (probe_context_limit).

The probe is best-effort: any failure returns 0, never raises. These tests
cover the happy path (extracting context_length from various provider response
shapes) and the failure paths (network errors, missing fields, weird types).
"""

from coderio.llm.probe import probe_context_limit, _extract_context_limit


def test_extract_from_openai_compatible_top_level():
    """StepFun/DeepSeek/Together often put context_length at top level."""
    payload = {"id": "step-3.7-flash", "object": "model", "context_length": 256000}
    assert _extract_context_limit(payload) == 256000


def test_extract_from_anthropic_nested_data():
    """Anthropic shape: fields nested under 'data'."""
    payload = {"data": {"id": "claude-sonnet-4", "context_window": 200000}}
    assert _extract_context_limit(payload) == 200000


def test_extract_from_openai_list_shape():
    """OpenAI /v1/models returns {'data': [{...}, ...]}."""
    payload = {
        "data": [{"id": "gpt-4o", "context_length": 128000}, {"id": "gpt-4o-mini"}]
    }
    assert _extract_context_limit(payload) == 128000


def test_extract_picks_first_known_field():
    """If multiple aliases present, the first in _CONTEXT_FIELDS wins."""
    payload = {"context_length": 100000, "max_context_length": 200000}
    assert _extract_context_limit(payload) == 100000


def test_extract_returns_zero_on_missing():
    assert _extract_context_limit({"id": "x"}) == 0
    assert _extract_context_limit({}) == 0


def test_extract_returns_zero_on_non_dict():
    assert _extract_context_limit(None) == 0
    assert _extract_context_limit("string") == 0
    assert _extract_context_limit(42) == 0


def test_extract_returns_zero_on_non_positive():
    """Zero or negative context_length is treated as 'not reported'."""
    assert _extract_context_limit({"context_length": 0}) == 0
    assert _extract_context_limit({"context_length": -1}) == 0


def test_extract_accepts_numeric_string():
    """Some providers serialize ints as strings in JSON."""
    assert _extract_context_limit({"context_length": "128000"}) == 128000


def test_extract_rejects_garbage_string():
    assert _extract_context_limit({"context_length": "lots"}) == 0


def test_probe_returns_zero_on_empty_inputs():
    assert probe_context_limit("openai_compatible", "", "key", "model") == 0
    assert probe_context_limit("openai_compatible", "https://x", "key", "") == 0


def test_probe_returns_zero_on_network_failure(monkeypatch):
    """Network errors must NOT raise — return 0 so onboarding proceeds."""
    import urllib.error

    def _raise(*a, **kw):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("coderio.llm.probe.urllib.request.urlopen", _raise)
    n = probe_context_limit(
        "openai_compatible", "https://api.example.com/v1", "key", "model-x"
    )
    assert n == 0


def test_probe_returns_zero_on_http_error(monkeypatch):
    """404/401 must return 0, not raise."""
    import urllib.error

    class _Resp404:
        status = 404

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def _404(*a, **kw):
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr("coderio.llm.probe.urllib.request.urlopen", _404)
    n = probe_context_limit("anthropic", "https://api.anthropic.com", "key", "x")
    assert n == 0


def test_probe_happy_path_openai_compatible(monkeypatch):
    """Successful probe returns the context_length field."""
    import json

    class _Resp:
        status = 200

        def __init__(self):
            self._payload = json.dumps(
                {"id": "step-3.7-flash", "context_length": 256000}
            ).encode()

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def _ok(req, timeout=None):
        # Verify the URL was constructed for OpenAI-compatible path
        assert "/v1/models/step-3.7-flash" in req.full_url, req.full_url
        return _Resp()

    monkeypatch.setattr("coderio.llm.probe.urllib.request.urlopen", _ok)
    n = probe_context_limit(
        "openai_compatible", "https://api.stepfun.com/v1", "sk-test", "step-3.7-flash"
    )
    assert n == 256000


def test_probe_happy_path_anthropic(monkeypatch):
    """Anthropic path uses x-api-key header and /models/{id} URL."""
    import json

    class _Resp:
        status = 200

        def read(self):
            return json.dumps({"data": {"context_window": 200000}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    captured = {}

    def _ok(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = req.headers
        return _Resp()

    monkeypatch.setattr("coderio.llm.probe.urllib.request.urlopen", _ok)
    n = probe_context_limit(
        "anthropic", "https://api.anthropic.com", "sk-ant", "claude-x"
    )
    assert n == 200000
    # Anthropic uses x-api-key header (urllib normalizes header name casing)
    headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
    assert "x-api-key" in headers_lower
    assert headers_lower["x-api-key"] == "sk-ant"
