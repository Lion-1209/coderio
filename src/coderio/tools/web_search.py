from __future__ import annotations

from pydantic import BaseModel, Field


class WebSearchArgs(BaseModel):
    query: str = Field(description="Search query.")
    max_results: int = Field(default=5, description="Maximum number of results.")


class WebSearchTool:
    """Free, zero-config web search backed by DuckDuckGo (via the `ddgs` lib).

    No API key required — works out of the box. DuckDuckGo occasionally rate-
    limits automated queries; on failure we return a friendly error string
    (not an exception) so the model can react (retry or switch approach).
    """

    name = "web_search"
    description = "Search the web (DuckDuckGo) and return result titles + urls + snippets. No API key needed."
    args_schema = WebSearchArgs

    def run(self, query: str, max_results: int = 5) -> str:
        try:
            from ddgs import DDGS
        except ImportError:
            return "Error: web search unavailable (ddgs package not installed)."

        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
        except Exception as e:
            # DuckDuckGo may rate-limit (HTTP 202/429) or be temporarily
            # unreachable. Surface a clear, retryable message — the model can
            # wait and retry, or fall back to its own knowledge.
            return (
                f"Error: web search failed ({type(e).__name__}: {e}). "
                "The service may be rate-limiting; retry shortly."
            )

        if not results:
            return "No results found."

        lines = []
        for r in results:
            title = r.get("title", "")
            url = r.get("href", "") or r.get("url", "")
            body = (r.get("body", "") or r.get("snippet", ""))[:200]
            lines.append(f"- {title}\n  {url}\n  {body}")
        return "\n".join(lines)
