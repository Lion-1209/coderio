from __future__ import annotations

import os
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from coderio.config import Config

# Max SDK-level retries on transient failures (429 / 5xx / network). The OpenAI
# client defaults to 2 and Anthropic to 2; bump to 3 so a brief rate-limit spike
# or a momentary network blip doesn't immediately surface as a fatal error to
# the user. The SDK already backs off exponentially between attempts.
_MAX_RETRIES: int = 3
# Per-request timeout in seconds. Without an explicit value the SDK default
# applies (OpenAI: none/60s, Anthropic: 600s), which is inconsistent across
# providers and can hang the TUI on a stuck connection. 90s covers slow coding
# models while still failing fast on a dead endpoint.
_REQUEST_TIMEOUT: int = 90


def _build_client(kind: str, *, model: str, base_url: str, api_key, max_tokens: int):
    """Construct a ChatAnthropic or ChatOpenAI with uniform retry/timeout settings.

    Centralizes the two knobs (max_retries, timeout) so every provider layer
    (profile, registry provider_id, custom provider_id, S0 fallback) gets the
    same resilience instead of each call site setting them ad hoc.
    """
    common = dict(
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_tokens=max_tokens,
        max_retries=_MAX_RETRIES,
        timeout=_REQUEST_TIMEOUT,
    )
    if kind == "anthropic":
        return ChatAnthropic(**common)
    return ChatOpenAI(**common)


def _pick_api_key(provider: str) -> str | None:
    if provider == "anthropic":
        # Z_API_KEY fallback (2026-09-04 audit P0-7): the onboarding gate
        # (_needs_onboarding) and the headless error message both tell users
        # "set Z_API_KEY" — and a user who does that lands on an
        # anthropic-kind provider (bigmodel/stepfun coding plan), for which
        # Z_API_KEY IS the key. The old code read only ANTHROPIC_API_KEY, so
        # the documented path produced a guaranteed auth failure. The
        # Anthropic vendor's own variable keeps precedence.
        return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("Z_API_KEY")
    return os.environ.get("Z_API_KEY") or os.environ.get("OPENAI_API_KEY")


def resolved_model_name(cfg: Config) -> str:
    """The model NAME build_chat_model would actually use for this config.

    Mirrors build_chat_model's layer resolution (named profile → registry →
    ``[model].default``). Display and persistence paths must use this (or the
    live model object's ``model_name``) instead of reading ``cfg.model.default``
    directly — with a named profile active, that raw field shows a different
    model than the one serving requests (#24, found in live testing
    2026-09-16: banner said step-3.7-flash while the active profile ran
    water18-0910).
    """
    profile = _resolve_profile(cfg)
    if profile is not None:
        return profile.model or cfg.model.default
    if cfg.model.provider_id:
        from coderio.cli.providers import get_provider

        info = get_provider(cfg.model.provider_id)
        if info is not None and info.default_model:
            return cfg.model.default or info.default_model
    return cfg.model.default


def resolved_provider_kind(cfg: Config) -> str:
    """The provider KIND ("anthropic" | "openai_compatible") the active
    profile/registry entry resolves to — the same layer resolution
    build_chat_model performs.

    Callers that build wire-format-dependent payloads must branch on THIS,
    not on ``cfg.model.provider``: with a named profile active, the raw field
    can disagree with the client actually serving requests (the same class of
    bug as #24's model-name display). The multimodal image-block builder is
    the first such caller (D1-2): Anthropic image blocks sent to an
    OpenAI-protocol provider are a guaranteed 400 with an error that never
    mentions the real cause.

    getattr throughout: callers (and tests) pass duck-typed config stubs
    that carry only the fields their path touches.
    """
    profile = _resolve_profile(cfg)
    if profile is not None:
        from coderio.cli.providers import get_provider

        info = get_provider(getattr(profile, "provider_id", ""))
        if info is not None:
            return info.kind
        return getattr(profile, "kind", "") or "openai_compatible"
    if getattr(cfg.model, "provider_id", ""):
        from coderio.cli.providers import get_provider

        info = get_provider(cfg.model.provider_id)
        if info is not None:
            return info.kind
        # Custom provider_id not in the registry — config.toml's provider
        # field decides (mirrors build_chat_model's layer-2 fallthrough).
        return getattr(cfg.model, "provider", "") or "openai_compatible"
    return getattr(cfg.model, "provider", "") or "openai_compatible"


def resolved_context_limit(cfg: Config) -> int:
    """The context window (tokens) for the model the active profile resolves
    to, or 0 when unknown (never probed / never configured).

    Mirrors build_chat_model's layer resolution (same as
    resolved_model_name/resolved_provider_kind). Consumers must treat 0 as
    "unknown" — e.g. MicrocompactMiddleware stays off rather than guessing a
    window. getattr throughout for duck-typed config stubs.
    """
    profile = _resolve_profile(cfg)
    if profile is not None:
        return int(getattr(profile, "context_limit", 0) or 0)
    model_cfg = getattr(cfg, "model", None)
    return int(getattr(model_cfg, "context_limit", 0) or 0)


# In-process memo for ensure_context_limit: (kind, base_url, model) -> probed
# limit (0 = probe FAILED and is also memoized — a provider without the
# models endpoint must not add probe latency to EVERY turn). Process-scoped
# on purpose: a transient failure deserves a retry next launch, not a retry
# every turn; a success is persisted to config and never re-probed.
_PROBE_MEMO: dict[tuple[str, str, str], int] = {}


def ensure_context_limit(cfg: Config, creds_path: Path | str | None = None) -> int:
    """resolved_context_limit, probing and persisting when unknown.

    WHY THIS EXISTS (WhaleDock incident follow-up, 2026-09-24): onboarding
    probes the context window once at setup, but switching models via
    /model, adding profiles, or hand-editing config leaves context_limit=0
    forever — microcompact silently stays off for that profile. This runs
    at the build_turn_spec choke point: first turn on an unprobed model
    pays one probe (≤4s), the value is persisted into config.toml, and
    every later turn is a dict lookup.

    Failure semantics match probe_context_limit: 0 = unknown, never raises,
    callers treat 0 as "stay off" — identical to the pre-existing behavior.
    """
    known = resolved_context_limit(cfg)
    if known > 0:
        return known
    from coderio.llm.probe import probe_context_limit

    profile = _resolve_profile(cfg)
    kind = base_url = api_key = model = ""
    if profile is not None:
        from coderio.cli.credentials import get_key
        from coderio.cli.providers import get_provider

        info = get_provider(getattr(profile, "provider_id", ""))
        kind = info.kind if info else (getattr(profile, "kind", "") or "openai_compatible")
        base_url = (info.base_url if info and info.base_url else getattr(profile, "base_url", "")) or ""
        model = getattr(profile, "model", "") or (info.default_model if info else "") or ""
        api_key = get_key(getattr(profile, "provider_id", ""), creds_path) or _pick_api_key(kind) or ""
    else:
        m = getattr(cfg, "model", None)
        if m is None:
            return 0
        from coderio.cli.credentials import get_key
        from coderio.cli.providers import get_provider

        info = get_provider(getattr(m, "provider_id", ""))
        kind = info.kind if info else (getattr(m, "provider", "") or "openai_compatible")
        base_url = (info.base_url if info else getattr(m, "base_url", "")) or ""
        model = getattr(m, "default", "") or (info.default_model if info else "") or ""
        api_key = get_key(getattr(m, "provider_id", ""), creds_path) or _pick_api_key(getattr(m, "provider", "")) or ""

    memo_key = (kind, base_url, model)
    if memo_key in _PROBE_MEMO:
        return _PROBE_MEMO[memo_key]
    limit = probe_context_limit(kind, base_url, api_key, model, timeout=4.0)
    _PROBE_MEMO[memo_key] = limit
    if limit > 0:
        try:
            _persist_context_limit(cfg, limit, creds_path)
        except Exception:  # noqa: BLE001, S110 — best-effort; the memo already holds this process's value
            pass
    return limit


def _persist_context_limit(cfg: Config, limit: int, creds_path: Path | str | None) -> None:
    """Write the probed limit into the user's config.toml (read-modify-write,
    preserving every other section — same pattern as _save_profile_to_config).

    Targets the ACTIVE profile entry when profiles exist, else [model].
    """
    import tomllib

    import tomli_w

    path = Path(creds_path).parent / "config.toml" if creds_path else Path.home() / ".coderio" / "config.toml"
    if not path.is_file():
        return  # nothing to update — onboarding owns creating the file
    with open(path, "rb") as f:
        data = tomllib.load(f)
    profiles = data.get("profiles")
    if isinstance(profiles, list) and profiles:
        active = data.get("active_profile") or (profiles[0].get("name") if isinstance(profiles[0], dict) else "")
        for p in profiles:
            if isinstance(p, dict) and p.get("name") == active:
                # Only write when meaningfully different — avoid churn.
                if p.get("context_limit") != limit:
                    p["context_limit"] = limit
                    with open(path, "wb") as f:
                        tomli_w.dump(data, f)
                return
        return
    m = data.get("model")
    if isinstance(m, dict) and m.get("context_limit") != limit:
        m["context_limit"] = limit
        with open(path, "wb") as f:
            tomli_w.dump(data, f)


def _resolve_profile(cfg: Config):
    """Pick the Profile to build from, or None to fall through to the legacy path.

    - active_profile set → find the matching Profile by name. If the name
      doesn't match any profile (stale config), fall through to legacy.
    - active_profile empty but profiles exist → default to the first one, so a
      freshly-onboarded user with one profile and no explicit active_profile
      still uses it.
    - no profiles → None (legacy [model] path, unchanged behavior).

    getattr: callers include test/CLI stubs that carry only the fields their
    path touches (a SimpleNamespace without `profiles` must resolve to the
    legacy path, not AttributeError).
    """
    profiles = getattr(cfg, "profiles", None) or []
    if not profiles:
        return None
    active = getattr(cfg, "active_profile", "") or ""
    if active:
        for p in profiles:
            if p.name == active:
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
        from coderio.cli.credentials import get_key
        from coderio.cli.providers import get_provider

        info = get_provider(profile.provider_id)
        key = get_key(profile.provider_id, creds_path) or _pick_api_key(info.kind if info else profile.kind)
        model_name = profile.model or (info.default_model if info and info.default_model else "")
        # Registry providers supply their own base_url/kind; custom profiles
        # carry their own (mirrors the [model] layer 1 vs 2 split).
        base_url = info.base_url if info and info.base_url else profile.base_url
        kind = info.kind if info else profile.kind
        if not base_url:
            raise ValueError(
                f"profile '{profile.name}': provider_id '{profile.provider_id}' "
                f"has no base_url. Set base_url in the profile or use a known provider_id."
            )
        return _build_client(
            kind,
            model=model_name,
            base_url=base_url,
            api_key=key,
            max_tokens=max_tokens,
        )

    if m.provider_id:
        from coderio.cli.credentials import get_key
        from coderio.cli.providers import get_provider

        info = get_provider(m.provider_id)
        key = get_key(m.provider_id, creds_path) or _pick_api_key(info.kind if info else m.provider)
        model_name = m.default or (info.default_model if info else "")
        if info:
            # Known provider — use registry base_url/kind.
            return _build_client(
                info.kind,
                model=model_name,
                base_url=info.base_url,
                api_key=key,
                max_tokens=max_tokens,
            )
        # Custom provider_id not in registry — use config.toml base_url/provider.
        kind = m.provider or "openai_compatible"
        if not m.base_url:
            raise ValueError(
                f"provider_id '{m.provider_id}' is not a known provider and "
                f"no base_url is set in config.toml. Either use a known provider_id, "
                f"or set [model] base_url and provider in config.toml."
            )
        # (P2 cleanup 2026-09-04: the old `if kind == "anthropic"` branch here
        # was byte-identical to the fallthrough below — one call suffices.)
        return _build_client(
            kind,
            model=model_name,
            base_url=m.base_url,
            api_key=key,
            max_tokens=max_tokens,
        )

    api_key = _pick_api_key(m.provider)
    if m.provider in ("openai_compatible", "anthropic"):
        return _build_client(
            m.provider,
            model=m.default,
            base_url=m.base_url,
            api_key=api_key,
            max_tokens=max_tokens,
        )
    raise ValueError(f"Unknown provider: {m.provider!r} (expected 'openai_compatible' or 'anthropic')")
