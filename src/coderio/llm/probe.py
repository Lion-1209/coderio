"""Provider context-window discovery.

On setup, we want to record the actual model's context window so the context-
compaction threshold is accurate. coderio defaults to 200K (covers most modern
models), but a model like step-3.7-flash (256K) would be mistreated as 200K,
triggering compaction at 120K instead of 153K.

This module queries the provider's model-info endpoint once at onboarding time
and returns the context window size. On ANY failure (network, parse, auth,
shape mismatch), it returns 0 — the caller falls back to the config default.
No retry, no exception: this is a best-effort optimization, never a blocker
for the setup flow.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


# The fields different providers use to report context window size in their
# /v1/models/{id} (OpenAI-compatible) or /v1/models/{id} (Anthropic) responses.
# We check each in order and take the first that's a positive int.
_CONTEXT_FIELDS = (
    "context_length",
    "max_context_length",
    "context_window",
    "context_window_tokens",
    "max_input_tokens",
    "input_context_length",
)


def probe_context_limit(
    provider_kind: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 5.0,
) -> int:
    """Query the provider's model-info endpoint for the context window size.

    Args:
        provider_kind: "anthropic" or "openai_compatible" (any other value
            routes through the OpenAI-compatible path).
        base_url: provider base URL (no trailing slash), e.g.
            "https://api.stepfun.com/v1" or "https://api.anthropic.com".
        api_key: API key for the Authorization header.
        model: model id, e.g. "step-3.7-flash".
        timeout: HTTP timeout in seconds. Default 5s — this runs during
            onboarding and the user is waiting; a stuck probe shouldn't block.

    Returns:
        The context window size in tokens (e.g. 256000), or 0 on any failure
        (network error, auth error, unexpected response shape, missing field).
        Callers MUST treat 0 as "unknown, use default".
    """
    base = (base_url or "").rstrip("/")
    if not base or not model:
        return 0
    try:
        if provider_kind == "anthropic":
            # Anthropic's GET /v1/models/{id} returns {"data": {"id": ..., ...}}
            # and currently does NOT report context_window — but we probe anyway
            # because the API surface may grow. Both header shapes accepted.
            url = f"{base}/models/{urllib.parse.quote(model)}"
            headers = (
                "x-api-key",
                api_key,
                "anthropic-version",
                "2023-06-01",
            )
        else:
            # OpenAI-compatible: GET /v1/models/{id} (note: many compatible
            # providers like Stepfun/DeepSeek/Together expose context_length in
            # the per-model response even though vanilla OpenAI doesn't).
            # base_url may or may not include /v1; normalize to .../v1/models/...
            if base.endswith("/v1"):
                url = f"{base}/models/{urllib.parse.quote(model)}"
            else:
                url = f"{base}/v1/models/{urllib.parse.quote(model)}"
            headers = ("Authorization", f"Bearer {api_key}")
        req = urllib.request.Request(url, headers=_pair_to_dict(headers), method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return 0
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        return _extract_context_limit(payload)
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
        OSError,
    ):
        return 0
    except Exception:
        # Defensive: probe is best-effort, never raise to the caller.
        return 0


def _pair_to_dict(pairs) -> dict[str, str]:
    """Accept either a tuple-of-pairs or a ready dict and return a dict."""
    if isinstance(pairs, dict):
        return pairs
    return {pairs[i]: pairs[i + 1] for i in range(0, len(pairs) - 1, 2)}


def _extract_context_limit(payload) -> int:
    """Pull the context window size from a provider's model-info response.

    Tries the top-level dict first, then the common nested locations
    (``data``, ``data.metadata``) since OpenAI-compatible providers are
    inconsistent about where they put it.
    """
    if not isinstance(payload, dict):
        return 0
    # 1) top-level fields
    n = _first_positive_int(payload)
    if n:
        return n
    # 2) nested under "data" (Anthropic shape, some OpenAI-compatible)
    data = payload.get("data")
    if isinstance(data, dict):
        n = _first_positive_int(data) or _first_positive_int(data.get("metadata"))
        if n:
            return n
    # 3) OpenAI list shape: {"data": [{...}]} — pick the first entry's fields
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                n = _first_positive_int(item)
                if n:
                    return n
    return 0


def _first_positive_int(d) -> int:
    """Return the first value found under any of _CONTEXT_FIELDS that's a
    positive integer. Strings that are all digits count too."""
    if not isinstance(d, dict):
        return 0
    for key in _CONTEXT_FIELDS:
        if key in d:
            v = d[key]
            try:
                iv = int(v)
                if iv > 0:
                    return iv
            except (TypeError, ValueError):
                continue
    return 0
