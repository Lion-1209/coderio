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
