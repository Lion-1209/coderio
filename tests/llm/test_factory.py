import pytest

from coderio.config import Config, ModelConfig, Profile
from coderio.llm import build_chat_model


def test_openai_compatible_uses_chatopenai():
    cfg = Config(model=ModelConfig(provider="openai_compatible", default="glm-4.5", base_url="http://x/v4"))
    model = build_chat_model(cfg)
    from langchain_openai import ChatOpenAI
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "glm-4.5"


def test_anthropic_uses_chatanthropic():
    cfg = Config(model=ModelConfig(provider="anthropic", default="claude-sonnet", base_url=""))
    model = build_chat_model(cfg)
    from langchain_anthropic import ChatAnthropic
    assert isinstance(model, ChatAnthropic)


def test_unknown_provider_raises():
    cfg = Config(model=ModelConfig(provider="bogus", default="x", base_url=""))
    with pytest.raises(ValueError):
        build_chat_model(cfg)


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("Z_API_KEY", "zk-123")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = Config(model=ModelConfig(provider="openai_compatible", default="glm-4.5", base_url="http://x/v4"))
    model = build_chat_model(cfg)
    assert model.openai_api_key.get_secret_value() == "zk-123"


def test_provider_id_uses_registry_and_credentials(tmp_path, monkeypatch):
    from coderio.cli.credentials import write_credentials
    creds = tmp_path / "credentials"
    write_credentials({"bigmodel_coding_plan": "sk-reg"}, path=creds)
    cfg = Config(model=ModelConfig(provider_id="bigmodel_coding_plan", default="glm-5.1", provider="anthropic", base_url=""))
    model = build_chat_model(cfg, creds_path=creds)
    from langchain_anthropic import ChatAnthropic
    assert isinstance(model, ChatAnthropic)
    assert model.model == "glm-5.1"
    assert model.anthropic_api_key.get_secret_value() == "sk-reg"


def test_provider_id_falls_back_to_env_when_no_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    cfg = Config(model=ModelConfig(provider_id="bigmodel_coding_plan", default="glm-5.2", provider="anthropic", base_url=""))
    model = build_chat_model(cfg, creds_path=tmp_path / "nope")
    assert model.anthropic_api_key.get_secret_value() == "env-key"


def test_stepfun_api_uses_openai_protocol(tmp_path):
    from coderio.cli.credentials import write_credentials
    creds = tmp_path / "credentials"
    write_credentials({"stepfun_api": "sk-sf"}, path=creds)
    cfg = Config(model=ModelConfig(provider_id="stepfun_api", default="step-3.7-flash", provider="openai_compatible", base_url=""))
    model = build_chat_model(cfg, creds_path=creds)
    from langchain_openai import ChatOpenAI
    assert isinstance(model, ChatOpenAI)


# --- Layer 0: named profile (multi-config) ---

def test_profile_takes_precedence_over_model(tmp_path):
    """When active_profile is set, the Profile's provider_id/model wins over
    the [model] section's settings (Layer 0 > Layer 1)."""
    from coderio.cli.credentials import write_credentials
    creds = tmp_path / "credentials"
    write_credentials({"openai": "sk-prof"}, path=creds)
    # [model] points at bigmodel, but the profile points at openai.
    cfg = Config(
        model=ModelConfig(provider_id="bigmodel_coding_plan", default="glm-5.2"),
        profiles=[Profile(name="my-openai", provider_id="openai", model="gpt-4o",
                          base_url="https://api.openai.com/v1", kind="openai_compatible")],
        active_profile="my-openai",
    )
    model = build_chat_model(cfg, creds_path=creds)
    from langchain_openai import ChatOpenAI
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "gpt-4o"  # from profile, not [model]
    assert model.openai_api_key.get_secret_value() == "sk-prof"


def test_profile_uses_registry_base_url_when_provider_known(tmp_path):
    """A profile whose provider_id is in the registry uses the registry's
    base_url/kind (mirrors [model] Layer 1)."""
    from coderio.cli.credentials import write_credentials
    creds = tmp_path / "credentials"
    write_credentials({"bigmodel_coding_plan": "sk-reg"}, path=creds)
    cfg = Config(
        profiles=[Profile(name="glm", provider_id="bigmodel_coding_plan",
                          model="glm-5.2", base_url="", kind="anthropic")],
        active_profile="glm",
    )
    model = build_chat_model(cfg, creds_path=creds)
    from langchain_anthropic import ChatAnthropic
    assert isinstance(model, ChatAnthropic)
    assert model.model == "glm-5.2"
    assert model.anthropic_api_key.get_secret_value() == "sk-reg"


def test_profile_defaults_to_first_when_active_unset(tmp_path):
    """profiles present but active_profile empty → build from the first one."""
    from coderio.cli.credentials import write_credentials
    creds = tmp_path / "credentials"
    write_credentials({"openai": "sk-first"}, path=creds)
    cfg = Config(
        profiles=[Profile(name="first", provider_id="openai", model="gpt-4o",
                          base_url="https://api.openai.com/v1", kind="openai_compatible"),
                  Profile(name="second", provider_id="openai", model="gpt-4o-mini",
                          base_url="https://api.openai.com/v1", kind="openai_compatible")],
        active_profile="",  # unset → should default to first
    )
    model = build_chat_model(cfg, creds_path=creds)
    assert model.model_name == "gpt-4o"  # first profile's model


def test_no_profiles_falls_through_to_model(tmp_path):
    """Empty profiles list → Layer 0 skipped, legacy [model] path runs."""
    cfg = Config(
        model=ModelConfig(provider="openai_compatible", default="glm-4.5",
                          base_url="http://x/v4"),
        profiles=[],
        active_profile="",
    )
    model = build_chat_model(cfg)
    from langchain_openai import ChatOpenAI
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "glm-4.5"  # from [model], not a profile


def test_stale_active_profile_falls_through(tmp_path):
    """active_profile names a non-existent profile → fall through to [model]
    rather than crashing."""
    cfg = Config(
        model=ModelConfig(provider="openai_compatible", default="glm-4.5",
                          base_url="http://x/v4"),
        profiles=[Profile(name="real", provider_id="openai", model="gpt-4o",
                          base_url="https://api.openai.com/v1", kind="openai_compatible")],
        active_profile="ghost",  # doesn't match any profile
    )
    model = build_chat_model(cfg)
    from langchain_openai import ChatOpenAI
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "glm-4.5"  # fell through to [model]


# --- retry / timeout resilience (_build_client) ---

def test_build_client_sets_max_retries_and_timeout_openai():
    """Every built ChatOpenAI carries uniform max_retries + timeout so a brief
    429 spike or network blip doesn't immediately surface as a fatal error."""
    from coderio.llm.factory import _MAX_RETRIES, _REQUEST_TIMEOUT
    cfg = Config(model=ModelConfig(provider="openai_compatible", default="glm-4.5",
                                   base_url="http://x/v4"))
    model = build_chat_model(cfg)
    assert model.max_retries == _MAX_RETRIES
    assert model.request_timeout == _REQUEST_TIMEOUT


def test_build_client_sets_max_retries_and_timeout_anthropic():
    """ChatAnthropic gets the same retry/timeout treatment."""
    from coderio.llm.factory import _MAX_RETRIES
    cfg = Config(model=ModelConfig(provider="anthropic", default="claude", base_url=""))
    model = build_chat_model(cfg)
    from langchain_anthropic import ChatAnthropic
    assert isinstance(model, ChatAnthropic)
    assert model.max_retries == _MAX_RETRIES
