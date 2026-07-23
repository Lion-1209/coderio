from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from coderio.cli.credentials import write_credentials
from coderio.cli.providers import PROVIDERS, ProviderInfo

_SKIP = "skip"


@dataclass
class OnboardingResult:
    provider_id: str
    model: str
    base_url: str
    kind: str
    api_key: str
    # Context window size (tokens) probed from the provider's /v1/models/{id}
    # endpoint at setup time. 0 = probe failed or skipped; the runtime then
    # falls back to ContextConfig.model_context_limit. Persisted into the
    # profile's [[profiles]] table so the threshold is accurate on every run
    # without re-probing.
    context_limit: int = 0


def _menu(prompt_fn) -> ProviderInfo | str:
    """Display grouped provider menu; return chosen ProviderInfo or 'skip'."""
    # Group providers for the menu display.
    plan = [p for p in PROVIDERS if p.plan]
    cn_direct = [p for p in PROVIDERS if not p.plan and p.id in ("bigmodel_api", "stepfun_api")]
    intl = [p for p in PROVIDERS if p.id in ("openai", "anthropic")]
    local = [p for p in PROVIDERS if p.id == "ollama"]
    custom = [p for p in PROVIDERS if p.id == "openai_custom"]
    idx = {}
    n = 1
    lines = []

    def _add_group(title, providers):
        nonlocal n
        lines.append(f"  ── {title} ──")
        for p in providers:
            idx[n] = p
            models_str = f" ({' / '.join(p.models)})" if p.models else ""
            lines.append(f"  [{n}] {p.label}{models_str}")
            n += 1

    if plan:
        _add_group("Coding Plan（订阅制）", plan)
    if cn_direct:
        _add_group("国内 API Key 直连", cn_direct)
    if intl:
        _add_group("国际", intl)
    if local:
        _add_group("本地模型", local)
    if custom:
        _add_group("自定义", custom)
    idx[n] = _SKIP
    lines.append(f"  [{n}] 跳过（稍后手动配置）")
    for line in lines:
        print(line)
    while True:
        raw = prompt_fn("选择 provider 编号: ").strip()
        if raw.isdigit() and int(raw) in idx:
            return idx[int(raw)]
        print(f"  无效，请输入 1-{n}")


def _choose_model(prompt_fn, p: ProviderInfo) -> str:
    if not p.models:
        return prompt_fn("输入模型名: ").strip()
    for i, m in enumerate(p.models, 1):
        star = " *" if m == p.default_model else ""
        print(f"  [{i}] {m}{star}")
    raw = prompt_fn("选择模型（回车=默认）: ").strip()
    if raw == "" or not raw.isdigit():
        return p.default_model
    i = int(raw) - 1
    if 0 <= i < len(p.models):
        return p.models[i]
    return p.default_model


def _save_to_config(result: OnboardingResult, config_path: Path) -> None:
    """Merge the onboarding result into ~/.coderio/config.toml.

    Updates [model] section with provider_id, default, base_url. Preserves all
    other sections the user may have configured. Without this, build_chat_model
    never sees provider_id and falls back to the broken S0 path that ignores the
    credentials file."""
    data: dict = {}
    if config_path.is_file():
        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            data = {}
    model_section = data.get("model", {})
    model_section["provider_id"] = result.provider_id
    model_section["default"] = result.model
    if result.base_url:
        model_section["base_url"] = result.base_url
    model_section["provider"] = result.kind
    # Persist the probed context window so compaction uses the real threshold
    # on every run. Only write when > 0 — a 0 means probe failed/skipped, and
    # we don't want to overwrite a previously-good value with the fallback.
    if getattr(result, "context_limit", 0) > 0:
        model_section["context_limit"] = result.context_limit
    data["model"] = model_section
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "wb") as f:
        tomli_w.dump(data, f)


def _save_profile_to_config(result: OnboardingResult, profile_name: str, config_path: Path) -> None:
    """Append a named profile to config.toml and mark it active.

    Read-modify-write: preserves all existing sections and any prior profiles.
    Appends a [[profiles]] table and sets active_profile to the new name so the
    just-configured profile is immediately usable. If a profile with the same
    name already exists, it is replaced in place (re-configuring rather than
    duplicating).
    """
    data: dict = {}
    if config_path.is_file():
        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            data = {}
    profiles = data.get("profiles", [])
    if not isinstance(profiles, list):
        profiles = []
    entry = {
        "name": profile_name,
        "provider_id": result.provider_id,
        "model": result.model,
        "base_url": result.base_url,
        "kind": result.kind,
    }
    # Persist the probed context window so the compaction threshold is accurate
    # on every subsequent run without re-probing. Only write when > 0 — a 0
    # means the probe failed or wasn't run, and we don't want to overwrite a
    # previously-good value with a fallback marker (e.g. when re-configuring
    # an existing profile off-network).
    if getattr(result, "context_limit", 0) > 0:
        entry["context_limit"] = result.context_limit
    # Replace an existing same-named profile, else append.
    replaced = False
    for i, p in enumerate(profiles):
        if isinstance(p, dict) and p.get("name") == profile_name:
            profiles[i] = entry
            replaced = True
            break
    if not replaced:
        profiles.append(entry)
    data["profiles"] = profiles
    data["active_profile"] = profile_name
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "wb") as f:
        tomli_w.dump(data, f)


def _verify_and_probe(p: ProviderInfo, api_key: str, model: str, base_url: str) -> tuple[bool, str, int]:
    """Verify the key works AND probe the model's context window size.

    Returns (success, message, context_limit). context_limit is 0 on any
    failure (verification failed OR probe failed) — callers fall back to the
    config default in that case, so probe failures never block onboarding.

    The verification uses a 1-token 'hi' request (cheap, fast). On success we
    additionally query /v1/models/{id} to discover the real context window so
    the compaction threshold matches the actual model (e.g. step-3.7-flash is
    256K, not the 200K default). Both calls are best-effort; only the verify
    result gates the wizard.
    """
    try:
        if p.kind == "anthropic":
            from langchain_anthropic import ChatAnthropic

            m = ChatAnthropic(model=model, base_url=base_url, api_key=api_key, max_tokens=1)
        else:
            from langchain_openai import ChatOpenAI

            m = ChatOpenAI(model=model, base_url=base_url, api_key=api_key, max_tokens=1)
        m.invoke("hi")
    except Exception as e:
        msg = str(e).lower()
        if any(k in msg for k in ("authentication", "401", "unauthorized", "api key")):
            return (False, f"API key 无效或已过期: {e}", 0)
        if any(k in msg for k in ("connect", "timeout", "refused", "unreachable")):
            return (False, f"无法连接到 {base_url}，请检查网络: {e}", 0)
        if any(k in msg for k in ("404", "not found", "model")):
            return (False, f"模型 {model} 不可用: {e}", 0)
        return (False, f"验证失败: {type(e).__name__}: {e}", 0)
    # Verify passed — best-effort probe for context window size. Never raises.
    from coderio.llm.probe import probe_context_limit

    context_limit = probe_context_limit(p.kind, base_url, api_key, model)
    suffix = f"（检测到 {context_limit // 1000}K 上下文窗口）" if context_limit > 0 else ""
    return (True, f"验证成功{suffix}", context_limit)


# Back-compat shim: older call sites/tests may still import _verify_key.
def _verify_key(p: ProviderInfo, api_key: str, model: str, base_url: str) -> tuple[bool, str]:
    ok, msg, _ = _verify_and_probe(p, api_key, model, base_url)
    return (ok, msg)


def run_onboarding(prompt_fn, password_fn, creds_file: Path | None = None) -> OnboardingResult | None:
    """Run the interactive wizard. Returns None if user skips."""
    print("检测到尚未配置 API key，启动配置向导：")
    choice = _menu(prompt_fn)
    if choice == _SKIP:
        print("已跳过。稍后可编辑 ~/.coderio/config.toml 手动配置。")
        return None
    p = choice
    base_url = p.base_url
    if p.id == "openai_custom":
        base_url = prompt_fn("输入 base_url: ").strip()
    model = _choose_model(prompt_fn, p)
    context_limit = 0
    if p.id != "ollama":
        print(f"  提示：{p.api_key_hint}")
        api_key = password_fn().strip()
        if not api_key:
            print("  未输入 key，已取消。")
            return None
        # Verify the key works before saving — catches typos, wrong endpoints,
        # expired keys at config time instead of on first message in the TUI.
        # On success, also probe the model's context window so compaction uses
        # the real threshold (256K for step-3.7-flash, 200K for claude, etc).
        print("  正在验证连接...")
        ok, msg, context_limit = _verify_and_probe(p, api_key, model, base_url)
        if not ok:
            print(f"  ❌ {msg}")
            retry = prompt_fn("  重新输入 key？(回车=重试, n=跳过): ").strip().lower()
            if retry == "n":
                return None
            api_key = password_fn().strip()
            if not api_key:
                return None
            print("  正在验证连接...")
            ok, msg, context_limit = _verify_and_probe(p, api_key, model, base_url)
            if not ok:
                print(f"  ❌ {msg}")
                print("  仍验证失败。配置已跳过，请稍后检查配置。")
                return None
        print(f"  ✅ {msg}")
    else:
        api_key = "ollama"  # Ollama doesn't need a key
    write_credentials({p.id: api_key}, creds_file)
    result = OnboardingResult(
        provider_id=p.id,
        model=model,
        base_url=base_url,
        kind=p.kind,
        api_key=api_key,
        context_limit=context_limit,
    )
    # Persist provider_id/model/base_url to config.toml so build_chat_model
    # uses the S1 (provider registry) path — not the broken S0 fallback.
    if creds_file is not None:
        config_path = creds_file.parent / "config.toml"
        _save_to_config(result, config_path)
    print("  配置完成！")
    return result
