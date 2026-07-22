from unittest.mock import patch

import httpx

from coderio.tools.web_search import WebSearchTool
from coderio.tools.web_fetch import WebFetchTool


def test_web_search_returns_results(monkeypatch):
    class _Fake:
        def __call__(self, *a, **k):
            return self

        def json(self):
            return {
                "results": [{"title": "T", "url": "http://x", "content": "snippet"}]
            }

        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "get", _Fake())
    tool = WebSearchTool(api_key="test")
    out = tool.run(query="query")
    assert isinstance(out, str)


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
