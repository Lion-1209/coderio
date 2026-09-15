import contextlib
import socket
import threading
import time

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
    """Fetch path uses httpx.Client.stream (P1-N3: bounded streamed read), so
    patch Client with a .stream context manager — the old .get patch no
    longer intercepts anything."""

    class _Resp:
        status_code = 200
        is_redirect = False
        headers = {"content-type": "text/html"}

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

        def stream(self, method, url):
            return contextlib.nullcontext(_Resp())

    monkeypatch.setattr(httpx, "Client", _Client)
    _mock_dns(monkeypatch)  # URL validation resolves the host BEFORE the (mocked) fetch
    tool = WebFetchTool()
    out = tool.run(url="http://example.com")
    assert "Hello world" in out


def test_web_fetch_follows_redirect_through_stream_path(monkeypatch):
    """The redirect branch moved INSIDE the stream context (P1-N3 rewrite).
    A redirect hop must be followed (target re-validated, same SSRF rules)
    and the final body still extracted — guards the restructured control
    flow, which no earlier test drove."""

    calls: list[str] = []

    class _Resp:
        def __init__(self, status, headers, body=b""):
            self.status_code = status
            self.is_redirect = status in (301, 302)
            self.headers = headers
            self._body = body

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield self._body

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def stream(self, method, url):
            calls.append(url)
            if len(calls) == 1:
                return contextlib.nullcontext(
                    _Resp(302, {"content-type": "text/html", "location": "http://moved.example/landing"})
                )
            return contextlib.nullcontext(
                _Resp(200, {"content-type": "text/html"}, b"<html><body><article>Landed</article></body></html>")
            )

    monkeypatch.setattr(httpx, "Client", _Client)
    _mock_dns(monkeypatch)  # redirect target validation resolves a public IP
    out = WebFetchTool().run("http://example.com/start")
    assert calls == ["http://example.com/start", "http://moved.example/landing"]
    assert "Landed" in out


def test_web_fetch_rejects_binary_content_type(monkeypatch):
    """Binary sniff must fire on headers inside the stream context, before
    any body byte is consumed."""

    consumed = []

    class _Resp:
        status_code = 200
        is_redirect = False
        headers = {"content-type": "image/png"}

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            consumed.append(True)  # must never run
            yield b"\x89PNG"

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def stream(self, method, url):
            return contextlib.nullcontext(_Resp())

    monkeypatch.setattr(httpx, "Client", _Client)
    _mock_dns(monkeypatch)
    out = WebFetchTool().run("http://example.com/logo.png")
    assert "unsupported content type" in out
    assert consumed == [], "body must not be read once the content type is rejected"


class _StallServer:
    """Real-socket HTTP server that sends more than the 1MB cap, then stalls
    (or completes) WITHOUT ever sending the declared Content-Length remainder.
    Raw sockets rather than http.server so we control the stall precisely."""

    def __init__(self, total_declared: int, send_bytes: int, then_stall: bool):
        self.total_declared = total_declared
        self.send_bytes = send_bytes
        self.then_stall = then_stall
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self._srv.settimeout(5)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _serve(self):
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            try:
                conn.recv(65536)  # request headers
                header = (
                    f"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                    f"Content-Length: {self.total_declared}\r\nConnection: close\r\n\r\n"
                ).encode()
                conn.sendall(header)
                chunk = b"x" * 65536
                sent = 0
                while sent < self.send_bytes:
                    n = min(len(chunk), self.send_bytes - sent)
                    conn.sendall(chunk[:n])
                    sent += n
                if self.then_stall:
                    time.sleep(3)  # hold the connection; never send the rest
            except OSError:
                pass  # client closed early — exactly the abort the fix adds

    def close(self):
        self._srv.close()


def _allow_loopback(monkeypatch):
    """Stub the SSRF host check so the tool may fetch 127.0.0.1. The check
    itself has dedicated tests (test_web_fetch.py); loopback is the only way
    to run a local server without depending on public DNS."""
    monkeypatch.setattr("coderio.tools.web_fetch._validate_url_host", lambda url: None)


def test_web_fetch_size_cap_aborts_before_server_finishes(monkeypatch):
    """P1-N3 regression (2026-09-14 audit): the 1MB cap used to run AFTER
    httpx had read the full body, so a server that sent 1.1MB then stalled
    held the turn until the read timeout and returned an error. The cap must
    abort the transfer instead: truncated content, no error, well under the
    timeout."""
    server = _StallServer(total_declared=1_100_000, send_bytes=1_050_000, then_stall=True).start()
    try:
        _allow_loopback(monkeypatch)
        t0 = time.monotonic()
        out = WebFetchTool().run(f"http://127.0.0.1:{server.port}/", timeout=10)
        elapsed = time.monotonic() - t0
    finally:
        server.close()
    assert not out.startswith("Error fetching"), f"expected truncated content, got: {out[:120]}"
    assert out.startswith("x") and len(out) <= 8000
    assert elapsed < 8, f"fetch took {elapsed:.1f}s — cap did not abort the stalled transfer"


def test_web_fetch_streaming_returns_truncated_body_of_oversized_response(monkeypatch):
    """Same rewrite, non-stalling variant: a complete 1.5MB response still
    yields capped content through the streamed path (guards the rewrite of
    the read loop itself)."""
    server = _StallServer(total_declared=1_500_000, send_bytes=1_500_000, then_stall=False).start()
    try:
        _allow_loopback(monkeypatch)
        out = WebFetchTool().run(f"http://127.0.0.1:{server.port}/", timeout=10)
    finally:
        server.close()
    assert not out.startswith("Error fetching"), f"expected content, got: {out[:120]}"
    assert out.startswith("x") and len(out) <= 8000
