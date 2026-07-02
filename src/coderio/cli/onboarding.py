from __future__ import annotations

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
    plan = [p for p in PROVIDERS if p.plan]
    nonplan = [p for p in PROVIDERS if not p.plan and p.id != "openai_custom"]
    custom = [p for p in PROVIDERS if p.id == "openai_custom"]
    idx = {}
    n = 1
    lines = []
    if plan:
        lines.append("  ── Coding Plan（订阅制）──────────────────")
        for p in plan:
            idx[n] = p
            lines.append(f"  [{n}] {p.label} ({' / '.join(p.models)})")
            n += 1
        lines.append("  ── API Key 直连 ──────────────────────────")
    for p in nonplan:
        idx[n] = p
        lines.append(f"  [{n}] {p.label} ({' / '.join(p.models)})")
        n += 1
    for p in custom:
        idx[n] = p
        lines.append(f"  [{n}] {p.label}")
        n += 1
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


def run_onboarding(prompt_fn, password_fn, creds_file: Path | None = None) -> OnboardingResult | None:
    """Run the interactive wizard. Returns None if user skips."""
    print("检测到尚未配置 API key，启动配置向导：")
    choice = _menu(prompt_fn)
    if choice == _SKIP:
        print("已跳过。稍后可编辑 ~/.coderio/credentials 手动配置。")
        return None
    p = choice
    base_url = p.base_url
    if p.id == "openai_custom":
        base_url = prompt_fn("输入 base_url: ").strip()
    model = _choose_model(prompt_fn, p)
    print(f"  提示：{p.api_key_hint}")
    api_key = password_fn().strip()
    if not api_key:
        print("  未输入 key，已取消。")
        return None
    write_credentials({p.id: api_key}, creds_file)
    return OnboardingResult(provider_id=p.id, model=model, base_url=base_url, kind=p.kind, api_key=api_key)
