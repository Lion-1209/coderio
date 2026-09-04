"""Tests for the web_fetch tool's SSRF protection (2026-08-14 report P0-3).

The blocklist is enforced at the _validate_url_host layer (scheme + resolved
IP checks) BEFORE any network request is made, so these tests need no network
access — every blocked case fails at parse/DNS layer (literal IPs) which is
deterministic.
"""

from __future__ import annotations

import pytest

from coderio.tools.web_fetch import WebFetchTool, _validate_url_host

# ----------------------------------------------------- blocked targets


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/admin",
        "http://localhost/",  # resolves to loopback
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://[::1]/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "file:///etc/passwd",
        "ftp://example.com/file",
    ],
)
def test_ssrf_blocked_targets(url):
    """Loopback / link-local / private / reserved IPs and non-http schemes
    must be rejected with a reason string."""
    reason = _validate_url_host(url)
    assert reason is not None, f"{url} must be blocked by SSRF protection"


def test_ssrf_public_host_allowed():
    """A public hostname passes host validation (the request itself may still
    fail offline — that's fine, validation is what we test here)."""
    assert _validate_url_host("https://example.com") is None
    assert _validate_url_host("http://example.com/path?q=1") is None


@pytest.mark.parametrize(
    "url",
    [
        "http://100.64.1.1/",  # CGNAT shared address space
        "http://100.100.200.200/latest/meta-data/",  # Aliyun IMDS lives in CGNAT
        "http://198.18.0.1/",  # benchmarking range
        "http://192.0.2.1/",  # documentation range
        "http://192.88.99.1/",  # C4: explicit CIDR — is_global lies on Python ≤ 3.11.8
        "http://192.0.0.9/",  # C4: explicit CIDR (192.0.0.0/24)
    ],
)
def test_ssrf_shared_address_ranges_blocked(url):
    """2026-09-04 audit: 100.64/10 is neither private nor link-local by
    ipaddress classification, so the Aliyun metadata service (the classic
    SSRF payout on the most common Chinese cloud) leaked past every named
    check. The is_global catch-all must block every non-global range."""
    reason = _validate_url_host(url)
    assert reason is not None, f"{url} must be blocked by SSRF protection"


def test_ssrf_aliyun_metadata_error_is_actionable():
    """Tool-level: the CGNAT metadata service is blocked with a model-facing
    reason, not a network round-trip."""
    out = WebFetchTool().run("http://100.100.200.200/latest/meta-data/", timeout=5)
    assert "SSRF" in out or "blocked" in out
    assert "100.100.200.200" in out


def test_ssrf_error_message_is_actionable():
    """The model-facing error explains WHAT was blocked and WHY (so it can
    reformulate instead of retrying blindly)."""
    out = WebFetchTool().run("http://169.254.169.254/latest/meta-data/", timeout=5)
    assert "SSRF" in out or "blocked" in out
    assert "169.254.169.254" in out


def test_ssrf_loopback_named_host():
    """`localhost` resolves to 127.0.0.1 — blocked at the DNS-resolution step."""
    reason = _validate_url_host("http://localhost:3000/")
    assert reason is not None and "loopback" in reason


def test_ssrf_scheme_allowlist_only_http_https():
    """file://, ftp://, gopher:// etc. are rejected before any resolution."""
    for scheme in ("file", "ftp", "gopher", "data", "javascript"):
        reason = _validate_url_host(f"{scheme}://example.com/x")
        assert reason is not None, f"{scheme}:// must be blocked"
        assert "scheme" in reason
