from coderio.config import Config, ModelConfig, SessionConfig, SkillsConfig, ToolsConfig


def test_config_defaults():
    cfg = Config()
    assert isinstance(cfg.model, ModelConfig)
    assert isinstance(cfg.tools, ToolsConfig)
    assert isinstance(cfg.skills, SkillsConfig)
    assert isinstance(cfg.session, SessionConfig)
    assert cfg.model.provider == "openai_compatible"
    assert cfg.tools.permission_mode == "confirm"
    assert cfg.tools.max_tool_rounds == 25
    assert cfg.skills.auto_load is True
    assert cfg.skills.stage_auto_inject is True
    assert cfg.tools.bash_shell == ""


def test_cli_and_provider_id_and_repo_url_defaults():
    cfg = Config()
    assert cfg.cli.theme == "dark"
    assert cfg.cli.show_tool_output is True
    assert cfg.skills.repo_url == "https://github.com/Lion-1209/Lion-Skills"
    assert cfg.model.provider_id == ""
