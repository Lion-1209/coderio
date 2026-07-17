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


def _resolve_profile(cfg: Config):
    """Pick the Profile to build from, or None to fall through to the legacy path.

    - active_profile set → find the matching Profile by name. If the name
      doesn't match any profile (stale config), fall through to legacy.
    - active_profile empty but profiles exist → default to the first one, so a
      freshly-onboarded user with one profile and no explicit active_profile
      still uses it.
    - no profiles → None (legacy [model] path, unchanged behavior).
    """
    profiles = cfg.profiles or []
    if not profiles:
        return None
    if cfg.active_profile:
        for p in profiles:
            if p.name == cfg.active_profile:
                return p
        # Stale active_profile name — fall through rather than crash.
        return None
    return profiles[0]


def build_chat_model(cfg: Config, creds_path: Path | str | None = None):
    """Build a chat model.

    Resolution order:
      0. active_profile set → use that Profile's provider_id/model/base_url/kind
         + credentials key (named multi-config, the /profile path)
      1. provider_id in registry → use registry base_url/kind + credentials key
      2. provider_id set but NOT in registry → use config.toml's base_url/provider
         + credentials key (custom provider from config.toml or 'coderio config add')
      3. no provider_id → S0 fallback: config.toml provider/base_url + env key

    Layers 1-3 are the legacy single-config path. Layer 0 takes precedence when
    the user has created named profiles via onboarding; without profiles,
    cfg.active_profile is "" and layers 1-3 run unchanged.
    """
    m = cfg.model
    max_tokens = m.max_output_tokens

    # Layer 0: named profile (multi-config). Takes precedence over [model].
    profile = _resolve_profile(cfg)
    if profile is not None:
        from coderio.cli.providers import get_provider
        from coderio.cli.credentials import get_key
        info = get_provider(profile.provider_id)
        key = get_key(profile.provider_id, creds_path) or _pick_api_key(
            info.kind if info else profile.kind)
        model_name = profile.model or (info.default_model if info and info.default_model else "")
        # Registry providers supply their own base_url/kind; custom profiles
        # carry their own (mirrors the [model] layer 1 vs 2 split).
        base_url = info.base_url if info and info.base_url else profile.base_url
        kind = info.kind if info else profile.kind
        if not base_url:
            raise ValueError(
                f"profile '{profile.name}': provider_id '{profile.provider_id}' "
                f"has no base_url. Set base_url in the profile or use a known provider_id.")
        if kind == "anthropic":
            return ChatAnthropic(model=model_name, base_url=base_url, api_key=key, max_tokens=max_tokens)
        return ChatOpenAI(model=model_name, base_url=base_url, api_key=key, max_tokens=max_tokens)

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
