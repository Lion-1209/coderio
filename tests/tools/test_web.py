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
    """Fetch path now uses httpx.Client (SSRF fix: manual redirect hops), so
    patch Client — the old httpx.get patch no longer intercepts anything."""

    class _Resp:
        status_code = 200
        is_redirect = False
        headers = {"content-type": "text/html"}
        text = "<html><body><article>Hello world</article></body></html>"

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b"<html><body><article>Hello world</article></body></html>"

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, url):
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    tool = WebFetchTool()
    out = tool.run(url="http://example.com")
    assert "Hello world" in out
