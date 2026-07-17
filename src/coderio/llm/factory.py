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

    Resolution order:
      1. provider_id in registry → use registry base_url/kind + credentials key
      2. provider_id set but NOT in registry → use config.toml's base_url/provider
         + credentials key (custom provider from config.toml or 'coderio config add')
      3. no provider_id → S0 fallback: config.toml provider/base_url + env key
    """
    m = cfg.model
    max_tokens = m.max_output_tokens
    if m.provider_id:
        from coderio.cli.providers import get_provider
        from coderio.cli.credentials import get_key

        info = get_provider(m.provider_id)
        key = get_key(m.provider_id, creds_path) or _pick_api_key(
            info.kind if info else m.provider)
        model_name = m.default or (info.default_model if info else "")
        if info:
            # Known provider — use registry base_url/kind.
            if info.kind == "anthropic":
                return ChatAnthropic(model=model_name, base_url=info.base_url, api_key=key, max_tokens=max_tokens)
            return ChatOpenAI(model=model_name, base_url=info.base_url, api_key=key, max_tokens=max_tokens)
        # Custom provider_id not in registry — use config.toml base_url/provider.
        kind = m.provider or "openai_compatible"
        if not m.base_url:
            raise ValueError(
                f"provider_id '{m.provider_id}' is not a known provider and "
                f"no base_url is set in config.toml. Either use a known provider_id, "
                f"or set [model] base_url and provider in config.toml.")
        if kind == "anthropic":
            return ChatAnthropic(model=model_name, base_url=m.base_url, api_key=key, max_tokens=max_tokens)
        return ChatOpenAI(model=model_name, base_url=m.base_url, api_key=key, max_tokens=max_tokens)

    api_key = _pick_api_key(m.provider)
    if m.provider == "openai_compatible":
        return ChatOpenAI(model=m.default, base_url=m.base_url, api_key=api_key, max_tokens=max_tokens)
    if m.provider == "anthropic":
        return ChatAnthropic(model=m.default, base_url=m.base_url, api_key=api_key, max_tokens=max_tokens)
    raise ValueError(f"Unknown provider: {m.provider!r} (expected 'openai_compatible' or 'anthropic')")
