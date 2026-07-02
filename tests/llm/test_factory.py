import pytest

from coderio.config import Config, ModelConfig
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
