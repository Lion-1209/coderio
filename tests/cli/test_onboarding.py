from coderio.cli.onboarding import run_onboarding, OnboardingResult


def test_select_bigmodel_coding_plan(tmp_path):
    creds_file = tmp_path / "credentials"
    answers = iter(["1", "", "sk-big-123"])
    result = run_onboarding(
        prompt_fn=lambda _: next(answers),
        password_fn=lambda: next(iter(["sk-big-123"])),
        creds_file=creds_file,
    )
    assert isinstance(result, OnboardingResult)
    assert result.provider_id == "bigmodel_coding_plan"
    assert result.model == "glm-5.2"
    assert result.api_key == "sk-big-123"
    from coderio.cli.credentials import read_credentials
    assert read_credentials(creds_file) == {"bigmodel_coding_plan": "sk-big-123"}


def test_select_stepfun_with_explicit_model(tmp_path):
    answers = iter(["2", "2", "sk-step"])
    result = run_onboarding(
        prompt_fn=lambda _: next(answers),
        password_fn=lambda: next(iter(["sk-step"])),
        creds_file=tmp_path / "credentials",
    )
    assert result.provider_id == "stepfun_coding_plan"
    assert result.model == "step-3.5-flash"


def test_skip_returns_none(tmp_path):
    answers = iter(["6"])
    result = run_onboarding(
        prompt_fn=lambda _: next(answers),
        password_fn=lambda: "",
        creds_file=tmp_path / "credentials",
    )
    assert result is None
    assert not (tmp_path / "credentials").exists()


def test_openai_custom_asks_base_url(tmp_path):
    answers = iter(["5", "https://my.api/v1", "my-model", "sk-x"])
    result = run_onboarding(
        prompt_fn=lambda _: next(answers),
        password_fn=lambda: next(iter(["sk-x"])),
        creds_file=tmp_path / "credentials",
    )
    assert result.provider_id == "openai_custom"
    assert result.base_url == "https://my.api/v1"
    assert result.model == "my-model"
