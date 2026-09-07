import httpx

from coderio.tools.web_fetch import WebFetchTool
from coderio.tools.web_search import WebSearchTool


def _mock_dns(monkeypatch, ip: str = "93.184.216.34"):
    """Force every hostname to resolve to a public IP — web tests must not
    depend on real DNS (external review 2026-09-05: fake-IP proxies make
    example.com resolve into the blocked 198.18/15 range, which correctly
    trips the SSRF guard and would fail these tests anywhere)."""
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


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
    _mock_dns(monkeypatch)  # URL validation resolves the host BEFORE the (mocked) fetch
    tool = WebFetchTool()
    out = tool.run(url="http://example.com")
    assert "Hello world" in out
