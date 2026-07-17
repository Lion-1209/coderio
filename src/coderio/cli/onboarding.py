from __future__ import annotations

import tomllib
import tomli_w
from dataclasses import dataclass
from pathlib import Path

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
    for l in lines:
        print(l)
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
    data["model"] = model_section
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "wb") as f:
        tomli_w.dump(data, f)


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
    if p.id != "ollama":
        print(f"  提示：{p.api_key_hint}")
        api_key = password_fn().strip()
        if not api_key:
            print("  未输入 key，已取消。")
            return None
    else:
        api_key = "ollama"  # Ollama doesn't need a key
    write_credentials({p.id: api_key}, creds_file)
    result = OnboardingResult(provider_id=p.id, model=model, base_url=base_url, kind=p.kind, api_key=api_key)
    # Persist provider_id/model/base_url to config.toml so build_chat_model
    # uses the S1 (provider registry) path — not the broken S0 fallback.
    if creds_file is not None:
        config_path = creds_file.parent / "config.toml"
        _save_to_config(result, config_path)
    print("  配置完成！")
    return result
