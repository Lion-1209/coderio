from __future__ import annotations

import os
from pathlib import Path

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

from coderio.config import Config


def _pick_api_key(provider: str) -> str | None:
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY")
    return os.environ.get("Z_API_KEY") or os.environ.get("OPENAI_API_KEY")


def build_chat_model(cfg: Config, creds_path: Path | str | None = None):
    """Build a chat model.

    If cfg.model.provider_id is set (S1), look up the provider registry for
    base_url/kind and read the key from the credentials file (falling back to
    env). Otherwise use S0 behavior (protocol + env key).
    """
    m = cfg.model
    max_tokens = m.max_output_tokens
    if m.provider_id:
        from coderio.cli.providers import get_provider
        from coderio.cli.credentials import get_key

        info = get_provider(m.provider_id)
        if info is None:
            raise ValueError(f"Unknown provider_id: {m.provider_id!r}")
        key = get_key(m.provider_id, creds_path) or _pick_api_key(info.kind)
        model_name = m.default or info.default_model
        if info.kind == "anthropic":
            return ChatAnthropic(model=model_name, base_url=info.base_url, api_key=key, max_tokens=max_tokens)
        return ChatOpenAI(model=model_name, base_url=info.base_url, api_key=key, max_tokens=max_tokens)

    api_key = _pick_api_key(m.provider)
    if m.provider == "openai_compatible":
        return ChatOpenAI(model=m.default, base_url=m.base_url, api_key=api_key, max_tokens=max_tokens)
    if m.provider == "anthropic":
        return ChatAnthropic(model=m.default, base_url=m.base_url, api_key=api_key, max_tokens=max_tokens)
    raise ValueError(f"Unknown provider: {m.provider!r} (expected 'openai_compatible' or 'anthropic')")
