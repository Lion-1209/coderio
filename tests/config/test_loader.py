import textwrap
from pathlib import Path

from coderio.config import Config, load_config


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text), encoding="utf-8")


def test_load_defaults_when_no_files(tmp_path, monkeypatch):
    for k in ("CODERIO_MODEL", "CODERIO_PROVIDER", "CODERIO_BASH_SHELL"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config(search_from="nohome", user_dir=tmp_path)
    assert cfg.model.default == "glm-4.5"
    assert cfg.tools.bash_shell == ""


def test_user_config_overrides_defaults(tmp_path):
    user_cfg = tmp_path / "userhome" / "config.toml"
    write(user_cfg, """
        [model]
        default = "gpt-4o"
    """)
    proj = tmp_path / "proj"
    cfg = load_config(search_from=proj, user_dir=tmp_path / "userhome")
    assert cfg.model.default == "gpt-4o"


def test_project_config_overrides_user(tmp_path):
    user_cfg = tmp_path / "userhome" / "config.toml"
    write(user_cfg, '[model]\ndefault = "gpt-4o"\n')
    proj = tmp_path / "proj"
    write(proj / ".coderio" / "config.toml", '[model]\ndefault = "claude-sonnet"\n')
    cfg = load_config(search_from=proj, user_dir=tmp_path / "userhome")
    assert cfg.model.default == "claude-sonnet"


def test_env_overrides_all(tmp_path, monkeypatch):
    user_cfg = tmp_path / "userhome" / "config.toml"
    write(user_cfg, '[model]\ndefault = "gpt-4o"\n')
    monkeypatch.setenv("CODERIO_MODEL", "glm-4.5-air")
    proj = tmp_path / "proj"
    cfg = load_config(search_from=proj, user_dir=tmp_path / "userhome")
    assert cfg.model.default == "glm-4.5-air"


def test_env_provider_and_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("CODERIO_PROVIDER", "anthropic")
    monkeypatch.setenv("CODERIO_BASH_SHELL", "/bin/bash")
    cfg = load_config(search_from="nohome", user_dir=tmp_path)
    assert cfg.model.provider == "anthropic"
    assert cfg.tools.bash_shell == "/bin/bash"


def test_project_search_stops_at_home(tmp_path, monkeypatch):
    """A project search from a dir nested under the real home must not pick up the
    home ~/.coderio as a project config (regression: found via live test)."""
    for k in ("CODERIO_MODEL", "CODERIO_PROVIDER", "CODERIO_BASH_SHELL"):
        monkeypatch.delenv(k, raising=False)
    realhome = tmp_path / "realhome"
    (realhome / ".coderio").mkdir(parents=True)
    (realhome / ".coderio" / "config.toml").write_text('[model]\ndefault = "home-glm"\n', encoding="utf-8")
    # search_from is NESTED inside the (patched) home so the walk would reach home
    # unless it stops at the home boundary.
    proj = realhome / "projects" / "myproj"
    proj.mkdir(parents=True)
    monkeypatch.setattr(
        "coderio.config.loader.os.path.expanduser",
        lambda a: str(realhome),
    )
    cfg = load_config(search_from=proj, user_dir=tmp_path / "userhome")
    assert cfg.model.default == "glm-4.5"


def test_parses_cli_section_and_provider_id(tmp_path):
    user_cfg = tmp_path / "userhome" / "config.toml"
    write(user_cfg, """
        [model]
        provider_id = "bigmodel_coding_plan"
        default = "glm-5.2"

        [skills]
        repo_url = "https://example/my-skills"

        [cli]
        theme = "light"
        show_tool_output = false
    """)
    proj = tmp_path / "proj"
    cfg = load_config(search_from=proj, user_dir=tmp_path / "userhome")
    assert cfg.model.provider_id == "bigmodel_coding_plan"
    assert cfg.model.default == "glm-5.2"
    assert cfg.skills.repo_url == "https://example/my-skills"
    assert cfg.cli.theme == "light"
    assert cfg.cli.show_tool_output is False


def test_missing_cli_section_uses_defaults(tmp_path):
    cfg = load_config(search_from="nohome", user_dir=tmp_path)
    assert cfg.cli.theme == "dark"
    assert cfg.cli.show_tool_output is True
    assert cfg.skills.repo_url == "https://github.com/Lion-1209/Lion-Skills"


# --- profiles (multi-config) ---

def test_parses_profiles_and_active(tmp_path):
    user_cfg = tmp_path / "userhome" / "config.toml"
    write(user_cfg, """
        active_profile = "智谱套餐"

        [[profiles]]
        name = "智谱套餐"
        provider_id = "bigmodel_coding_plan"
        model = "glm-5.2"
        base_url = "https://open.bigmodel.cn/api/anthropic"
        kind = "anthropic"

        [[profiles]]
        name = "OpenAI"
        provider_id = "openai"
        model = "gpt-4o"
        base_url = "https://api.openai.com/v1"
        kind = "openai_compatible"
    """)
    proj = tmp_path / "proj"
    cfg = load_config(search_from=proj, user_dir=tmp_path / "userhome")
    assert len(cfg.profiles) == 2
    assert cfg.active_profile == "智谱套餐"
    assert cfg.profiles[0].name == "智谱套餐"
    assert cfg.profiles[0].provider_id == "bigmodel_coding_plan"
    assert cfg.profiles[0].kind == "anthropic"
    assert cfg.profiles[1].name == "OpenAI"
    assert cfg.profiles[1].model == "gpt-4o"


def test_no_profiles_is_backward_compatible(tmp_path):
    """A config with only a legacy [model] section (no profiles) must load fine
    and leave profiles empty + active_profile empty — build_chat_model then uses
    the unchanged [model] path."""
    user_cfg = tmp_path / "userhome" / "config.toml"
    write(user_cfg, """
        [model]
        provider_id = "bigmodel_coding_plan"
        default = "glm-5.2"
    """)
    proj = tmp_path / "proj"
    cfg = load_config(search_from=proj, user_dir=tmp_path / "userhome")
    assert cfg.profiles == []
    assert cfg.active_profile == ""
    assert cfg.model.provider_id == "bigmodel_coding_plan"  # legacy path intact


def test_empty_active_profile_when_unset(tmp_path):
    """profiles present but active_profile absent → active_profile stays empty
    (build_chat_model defaults to the first profile)."""
    user_cfg = tmp_path / "userhome" / "config.toml"
    write(user_cfg, """
        [[profiles]]
        name = "first"
        provider_id = "openai"
        model = "gpt-4o"
        kind = "openai_compatible"
    """)
    proj = tmp_path / "proj"
    cfg = load_config(search_from=proj, user_dir=tmp_path / "userhome")
    assert len(cfg.profiles) == 1
    assert cfg.active_profile == ""


def test_malformed_profile_entries_skipped(tmp_path):
    """Incomplete [[profiles]] entries (missing required fields) are skipped
    rather than crashing the whole config load."""
    user_cfg = tmp_path / "userhome" / "config.toml"
    write(user_cfg, """
        [[profiles]]
        name = "good"
        provider_id = "openai"
        model = "gpt-4o"

        [[profiles]]
        name = "missing-model"
        provider_id = "openai"
    """)
    proj = tmp_path / "proj"
    cfg = load_config(search_from=proj, user_dir=tmp_path / "userhome")
    assert len(cfg.profiles) == 1
    assert cfg.profiles[0].name == "good"
