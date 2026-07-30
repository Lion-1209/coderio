import httpx

from coderio.tools.web_fetch import WebFetchTool
from coderio.tools.web_search import WebSearchTool


def test_web_search_returns_results(monkeypatch):
    """web_search now uses ddgs (DuckDuckGo). Mock the DDGS.text iterator."""
    class _FakeDDGS:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def text(self, query, max_results=5):
            return [
                {"title": "T", "href": "http://x", "body": "snippet"},
            ]

    # WebSearchTool.run does a local `from ddgs import DDGS`, so patch the
    # module-level import target by injecting into sys.modules.
    import sys

    fake_mod = type(sys)("ddgs")
    fake_mod.DDGS = _FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)

    tool = WebSearchTool()
    out = tool.run(query="query")
    assert isinstance(out, str)
    assert "http://x" in out
    assert "T" in out


def test_web_fetch_extracts_text(monkeypatch):
    class _Resp:
        status_code = 200
        text = "<html><body><article>Hello world</article></body></html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    tool = WebFetchTool()
    out = tool.run(url="http://example.com")
    assert "Hello world" in out
