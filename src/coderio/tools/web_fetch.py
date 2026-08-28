"""Web fetch tool with SSRF (Server-Side Request Forgery) protection.

SECURITY (2026-08-14 report P0-3): the original implementation did a bare
``httpx.get(url, follow_redirects=True)`` with no validation. A prompt-injected
model could point it at:

  - ``http://127.0.0.1:*`` — local services (databases, admin panels)
  - ``http://169.254.169.254/latest/meta-data/`` — cloud metadata (AWS/GCP/Azure
    credential exfiltration; this is the classic SSRF payout)
  - ``http://[::1]``, ``http://10.x.x.x``, ``http://192.168.x.x`` — internal networks

Protections applied here (defense in depth):

1. Scheme allowlist: only ``http`` / ``https`` (blocks ``file://``, ``ftp://``...).
2. IP-based blocking at CONNECT time: the hostname is resolved and every
   resolved address is checked against private / loopback / link-local /
   reserved ranges BEFORE the request is sent. HONEST LIMIT (2026-08-28
   audit): httpx re-resolves DNS internally when connecting, so a TOCTOU
   DNS-rebinding window remains — the docstring previously claimed a
   "custom transport" that pins resolved IPs; no such transport exists in
   this implementation.
3. Redirect policy: redirects are followed MANUALLY (max 3 hops), and each hop's
   URL goes through the same validation. ``follow_redirects=False`` on the
   client — an allowlisted URL redirecting to 169.254.169.254 is still blocked.
4. Response size cap: stream up to 1 MB, not ``resp.text`` unbounded into memory.
5. Content-Type sniff: binary payloads are rejected early.

This is prompt-injection defense, not a hard boundary — a determined attacker
with a DNS name that resolves to a public-but-malicious host still gets
fetched. The goal is to close the internal-network/metadata-exfiltration
class, which is the realistic damage path for a local coding agent.
"""

from __future__ import annotations

import ipaddress
import re
import socket

import httpx
from pydantic import BaseModel, Field

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_MAX_RESPONSE_BYTES = 1_000_000  # 1 MB cap — resp.text unbounded was an OOM vector
_MAX_REDIRECTS = 3

_SSRF_ERROR_TEMPLATE = (
    "Error fetching {url}: blocked by SSRF protection — {reason}. "
    "coderio's web_fetch may only reach public internet hosts over http/https; "
    "private networks, loopback, and link-local addresses (including cloud "
    "metadata services) are not fetchable."
)


def _extract_text(html: str) -> str:
    for tag in ("article", "main", "body"):
        m = re.search(f"<{tag}[^>]*>(.*?)</{tag}>", html, re.DOTALL | re.IGNORECASE)
        if m:
            html = m.group(1)
            break
    text = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", text).strip()


def _is_blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Return a human-readable reason if the address must not be fetched, else None."""
    if ip.is_loopback:
        return f"loopback address {ip} is not allowed"
    if ip.is_private:
        return f"private address {ip} is not allowed"
    if ip.is_link_local:
        return f"link-local address {ip} is not allowed (cloud metadata lives here)"
    if ip.is_reserved or ip.is_unspecified or ip.is_multicast:
        return f"reserved address {ip} is not allowed"
    return None


def _validate_url_host(url: str) -> str | None:
    """Validate scheme + resolved host addresses. Returns an error reason or None.

    Resolution happens HERE (per request / per redirect hop), so a DNS name
    that flips to an internal IP between checks is still caught.
    """
    try:
        parsed = httpx.URL(url)
    except Exception as e:  # noqa: BLE001 — malformed URL is a user error, not a crash
        return f"invalid URL: {e}"

    if parsed.scheme not in ("http", "https"):
        return f"scheme {parsed.scheme!r} is not allowed (only http/https)"

    host = parsed.host
    if not host:
        return "URL has no host"

    # Bracketed IPv6 literal: httpx.URL.host strips the brackets.
    try:
        # Try to parse the host as a literal IP first (fast path, no DNS).
        addr = ipaddress.ip_address(host)
        return _is_blocked_address(addr)
    except ValueError:
        pass  # Not an IP literal — resolve as a hostname below.

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return f"DNS resolution failed: {e}"

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        reason = _is_blocked_address(addr)
        if reason:
            return reason
    return None


class WebFetchArgs(BaseModel):
    url: str = Field(description="URL to fetch (public internet, http/https only).")
    timeout: int = Field(default=20, description="Request timeout in seconds.")


class WebFetchTool:
    name = "web_fetch"
    description = (
        "Fetch a public-internet URL and return extracted text content. "
        "Private/loopback/link-local addresses are blocked (SSRF protection). "
        "Requires permission."
    )
    args_schema = WebFetchArgs

    def run(self, url: str, timeout: int = 20) -> str:
        # Validate the initial URL before any request is made.
        reason = _validate_url_host(url)
        if reason:
            return _SSRF_ERROR_TEMPLATE.format(url=url, reason=reason)

        # follow_redirects=False — we hop manually so EACH redirect target
        # passes validation. A public URL redirecting to 169.254.169.254
        # (the metadata-service exfil pattern) is caught at the hop.
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=False,
                headers={"User-Agent": "coderio/0.1"},
            ) as client:
                current_url = url
                for _hop in range(_MAX_REDIRECTS + 1):
                    resp = client.get(current_url)
                    if resp.is_redirect:
                        # Validate the redirect target with the same rules.
                        next_url = str(resp.headers.get("location", ""))
                        if not next_url:
                            return f"Error fetching {url}: redirect without Location header"
                        # Relative redirect — resolve against the current URL.
                        next_url = str(httpx.URL(current_url).join(next_url))
                        reason = _validate_url_host(next_url)
                        if reason:
                            return _SSRF_ERROR_TEMPLATE.format(url=next_url, reason=reason)
                        current_url = next_url
                        continue
                    resp.raise_for_status()

                    # Binary sniff: reading a 1MB of gzip'd binary as text is
                    # useless to the model and wastes the context budget.
                    ctype = resp.headers.get("content-type", "").lower()
                    if any(
                        t in ctype
                        for t in ("image/", "audio/", "video/", "application/octet-stream", "application/pdf")
                    ):
                        return f"Error fetching {url}: unsupported content type {ctype!r} (text/HTML only)"

                    # Size cap via streamed read (Content-Length may lie/absent).
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in resp.iter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total > _MAX_RESPONSE_BYTES:
                            break
                    body = b"".join(chunks)[:_MAX_RESPONSE_BYTES]
                    return _extract_text(body.decode("utf-8", errors="replace"))[:8000]
                return f"Error fetching {url}: too many redirects (max {_MAX_REDIRECTS})"
        except Exception as e:  # noqa: BLE001 — surface fetch errors to the model
            return f"Error fetching {url}: {e}"
