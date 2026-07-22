from coderio.cli.providers import PROVIDERS, get_provider, ProviderInfo


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


def test_stepfun_coding_plan_base_url_no_v1():
    p = get_provider("stepfun_coding_plan")
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
