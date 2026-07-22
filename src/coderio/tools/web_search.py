from __future__ import annotations

import os

import httpx
from pydantic import BaseModel, Field


class WebSearchArgs(BaseModel):
    query: str = Field(description="Search query.")
    max_results: int = Field(default=5, description="Maximum number of results.")


class WebSearchTool:
    name = "web_search"
    description = "Search the web and return result titles + urls + snippets."
    args_schema = WebSearchArgs

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("CODERIO_WEB_SEARCH_KEY")

    def run(self, query: str, max_results: int = 5) -> str:
        if not self.api_key:
            return "Error: web search not configured (set CODERIO_WEB_SEARCH_KEY)."
        try:
            resp = httpx.get(
                "https://api.tavily.com/search",
                params={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": max_results,
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return f"Error: web search failed: {e}"
        results = data.get("results", [])
        if not results:
            return "No results."
        lines = []
        for r in results:
            lines.append(
                f"- {r.get('title', '')}\n  {r.get('url', '')}\n  {r.get('content', '')[:200]}"
            )
        return "\n".join(lines)
