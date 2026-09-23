from coderio.cli.providers import PROVIDERS, get_provider


def test_registry_has_expected_providers():
    ids = {p.id for p in PROVIDERS}
    expected = frozenset(
        {
            "bigmodel_coding_plan",
            "stepfun_coding_plan",
            "bigmodel_api",
            "stepfun_api",
            "openai_custom",
        }
    )
    assert expected.issubset(ids)


def test_coding_plan_flag_split():
    plan = {p.id for p in PROVIDERS if p.plan}
    nonplan = {p.id for p in PROVIDERS if not p.plan}
    assert "bigmodel_coding_plan" in plan
    assert "bigmodel_api" in nonplan


def test_bigmodel_coding_plan_models():
    p = get_provider("bigmodel_coding_plan")
    assert p is not None
    assert p.kind == "anthropic"
    assert p.base_url == "https://open.bigmodel.cn/api/anthropic"
    assert "glm-5.2" in p.models
    assert "glm-5.1" in p.models
    assert "glm-5-turbo" in p.models
    assert "glm-4.7" in p.models
    assert p.default_model == "glm-5.2"


def test_stepfun_coding_plan_is_anthropic_protocol():
    # StepFun Step Plan exposes an Anthropic-protocol endpoint at /step_plan
    # (posts to {base_url}/v1/messages). Must stay kind="anthropic" — switching
    # to openai_compatible makes ChatOpenAI join paths as {base_url}/chat/
    # completions, which 404s on this base_url.
    p = get_provider("stepfun_coding_plan")
    assert p.kind == "anthropic"
    assert p.base_url == "https://api.stepfun.com/step_plan"
    assert not p.base_url.endswith("/v1")


def test_stepfun_api_is_openai_protocol():
    p = get_provider("stepfun_api")
    assert p.kind == "openai_compatible"
    assert p.base_url == "https://api.stepfun.com/v1"


def test_model_names_are_lowercase():
    for p in PROVIDERS:
        for m in p.models:
            assert m == m.lower(), f"{p.id} model {m} not lowercase"


def test_get_unknown_returns_none():
    assert get_provider("nope") is None


def test_no_zai_in_registry():
    assert all(not p.id.startswith("zai") for p in PROVIDERS)


# ------------------------------------------------- resolved_provider_kind (D1-2)
def test_resolved_provider_kind_default_openai_compatible():
    from coderio.config.models import Config
    from coderio.llm.factory import resolved_provider_kind

    cfg = Config()
    cfg.model.provider = "openai_compatible"
    assert resolved_provider_kind(cfg) == "openai_compatible"


def test_resolved_provider_kind_legacy_model_section():
    from coderio.config.models import Config
    from coderio.llm.factory import resolved_provider_kind

    cfg = Config()
    cfg.model.provider = "anthropic"
    assert resolved_provider_kind(cfg) == "anthropic"


def test_resolved_provider_kind_registry_provider_id():
    """A known provider_id resolves through the REGISTRY's kind — the raw
    [model].provider field must not decide (that's the #24 class of bug:
    display/payload built from a field the active client doesn't use)."""
    from coderio.config.models import Config
    from coderio.llm.factory import resolved_provider_kind

    cfg = Config()
    cfg.model.provider_id = "bigmodel_coding_plan"  # registry kind = anthropic
    cfg.model.provider = "openai_compatible"  # stale raw field
    assert resolved_provider_kind(cfg) == "anthropic"


def test_resolved_provider_kind_named_profile_wins():
    """With a named profile active, the PROFILE's provider decides — even
    when [model] points somewhere else."""
    from coderio.config.models import Config, Profile
    from coderio.llm.factory import resolved_provider_kind

    cfg = Config()
    cfg.model.provider = "anthropic"
    cfg.profiles = [Profile(name="sf", provider_id="stepfun_api", model="step-3.7-flash")]
    cfg.active_profile = "sf"
    assert resolved_provider_kind(cfg) == "openai_compatible"


def test_resolved_provider_kind_profile_custom_provider():
    """A custom profile (provider_id not in the registry) falls back to the
    profile's own kind field, mirroring build_chat_model's layer-0 path."""
    from coderio.config.models import Config, Profile
    from coderio.llm.factory import resolved_provider_kind

    cfg = Config()
    cfg.profiles = [Profile(name="gw", provider_id="my-gateway", model="m", kind="anthropic")]
    cfg.active_profile = "gw"
    assert resolved_provider_kind(cfg) == "anthropic"
