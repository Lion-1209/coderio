from coderio.cli.onboarding import run_onboarding, OnboardingResult, _save_to_config
from coderio.cli.credentials import read_credentials


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
    # Menu: 1-2 plan, 3-4 cn_direct, 5-6 intl, 7 ollama, 8 custom, 9 skip
    answers = iter(["9"])
    result = run_onboarding(
        prompt_fn=lambda _: next(answers),
        password_fn=lambda: "",
        creds_file=tmp_path / "credentials",
    )
    assert result is None
    assert not (tmp_path / "credentials").exists()


def test_openai_custom_asks_base_url(tmp_path):
    # openai_custom is now menu item 8
    answers = iter(["8", "https://my.api/v1", "my-model", "sk-x"])
    result = run_onboarding(
        prompt_fn=lambda _: next(answers),
        password_fn=lambda: next(iter(["sk-x"])),
        creds_file=tmp_path / "credentials",
    )
    assert result.provider_id == "openai_custom"
    assert result.base_url == "https://my.api/v1"
    assert result.model == "my-model"


def test_onboarding_persists_to_config(tmp_path):
    """Onboarding must write provider_id/model to config.toml so build_chat_model
    uses the S1 path — without this the entered key is never read."""
    creds_file = tmp_path / "credentials"
    config_path = tmp_path / "config.toml"
    answers = iter(["1", "", "sk-big-123"])
    result = run_onboarding(
        prompt_fn=lambda _: next(answers),
        password_fn=lambda: next(iter(["sk-big-123"])),
        creds_file=creds_file,
    )
    assert result is not None
    # config.toml should now have provider_id + model
    assert config_path.is_file()
    import tomllib
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    assert data["model"]["provider_id"] == "bigmodel_coding_plan"
    assert data["model"]["default"] == "glm-5.2"


def test_ollama_no_key_required(tmp_path):
    """Ollama provider should skip the API key prompt."""
    # ollama is menu item 7
    answers = iter(["7", "qwen2.5-coder"])
    result = run_onboarding(
        prompt_fn=lambda _: next(answers),
        password_fn=lambda: "should-not-be-called",
        creds_file=tmp_path / "credentials",
    )
    assert result.provider_id == "ollama"
    assert result.model == "qwen2.5-coder"
    assert result.api_key == "ollama"


def test_openai_provider_available():
    """OpenAI direct provider should be in the registry for international users."""
    from coderio.cli.providers import get_provider
    p = get_provider("openai")
    assert p is not None
    assert p.kind == "openai_compatible"
    assert "api.openai.com" in p.base_url


def test_anthropic_provider_available():
    """Anthropic direct provider should be in the registry."""
    from coderio.cli.providers import get_provider
    p = get_provider("anthropic")
    assert p is not None
    assert p.kind == "anthropic"
