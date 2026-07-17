from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    kind: str
    plan: bool
    base_url: str
    models: tuple[str, ...]
    default_model: str
    api_key_hint: str = ""


_BIGMODEL_MODELS = ("glm-5.2", "glm-5.1", "glm-5-turbo", "glm-4.7")
_STEPFUN_MODELS = ("step-3.7-flash", "step-3.5-flash", "step-3.5-flash-2603")

PROVIDERS: tuple[ProviderInfo, ...] = (
    ProviderInfo(
        id="bigmodel_coding_plan",
        label="智谱 GLM - Coding Plan",
        kind="anthropic",
        plan=True,
        base_url="https://open.bigmodel.cn/api/anthropic",
        models=_BIGMODEL_MODELS,
        default_model="glm-5.2",
        api_key_hint="智谱开放平台 → 套餐概览 → 新建 API Key",
    ),
    ProviderInfo(
        id="stepfun_coding_plan",
        label="阶跃 StepFun - Step Plan",
        kind="anthropic",
        plan=True,
        base_url="https://api.stepfun.com/step_plan",
        models=_STEPFUN_MODELS,
        default_model="step-3.7-flash",
        api_key_hint="阶跃开放平台 → Step Plan 订阅 → 专用 API Key",
    ),
    ProviderInfo(
        id="bigmodel_api",
        label="智谱 GLM - API Key",
        kind="anthropic",
        plan=False,
        base_url="https://open.bigmodel.cn/api/anthropic",
        models=_BIGMODEL_MODELS,
        default_model="glm-5.2",
        api_key_hint="智谱开放平台 → API Keys",
    ),
    ProviderInfo(
        id="stepfun_api",
        label="阶跃 StepFun - API Key",
        kind="openai_compatible",
        plan=False,
        base_url="https://api.stepfun.com/v1",
        models=_STEPFUN_MODELS,
        default_model="step-3.7-flash",
        api_key_hint="阶跃开放平台 → API Keys",
    ),
    ProviderInfo(
        id="openai",
        label="OpenAI",
        kind="openai_compatible",
        plan=False,
        base_url="https://api.openai.com/v1",
        models=("gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"),
        default_model="gpt-4o",
        api_key_hint="platform.openai.com → API Keys",
    ),
    ProviderInfo(
        id="anthropic",
        label="Anthropic Claude",
        kind="anthropic",
        plan=False,
        base_url="https://api.anthropic.com",
        models=("claude-sonnet-4-20250514", "claude-haiku-4-20250414"),
        default_model="claude-sonnet-4-20250514",
        api_key_hint="console.anthropic.com → API Keys",
    ),
    ProviderInfo(
        id="ollama",
        label="Ollama（本地）",
        kind="openai_compatible",
        plan=False,
        base_url="http://localhost:11434/v1",
        models=(),
        default_model="",
        api_key_hint="无需 API key（确保 ollama serve 已运行）",
    ),
    ProviderInfo(
        id="openai_custom",
        label="OpenAI 兼容（自定义）",
        kind="openai_compatible",
        plan=False,
        base_url="",
        models=(),
        default_model="",
        api_key_hint="自定义 provider 的 API key",
    ),
)


def get_provider(provider_id: str) -> ProviderInfo | None:
    for p in PROVIDERS:
        if p.id == provider_id:
            return p
    return None
