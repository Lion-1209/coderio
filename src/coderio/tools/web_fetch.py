from __future__ import annotations

import re

import httpx
from pydantic import BaseModel, Field

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _extract_text(html: str) -> str:
    for tag in ("article", "main", "body"):
        m = re.search(f"<{tag}[^>]*>(.*?)</{tag}>", html, re.DOTALL | re.IGNORECASE)
        if m:
            html = m.group(1)
            break
    text = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", text).strip()


class WebFetchArgs(BaseModel):
    url: str = Field(description="URL to fetch.")
    timeout: int = Field(default=20, description="Request timeout in seconds.")


class WebFetchTool:
    name = "web_fetch"
    description = "Fetch a URL and return extracted text content. Requires permission."
    args_schema = WebFetchArgs

    def run(self, url: str, timeout: int = 20) -> str:
        try:
            resp = httpx.get(
                url,
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": "coderio/0.1"},
            )
            resp.raise_for_status()
        except Exception as e:
            return f"Error fetching {url}: {e}"
        return _extract_text(resp.text)[:8000]
