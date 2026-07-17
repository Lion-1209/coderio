from __future__ import annotations

import os
import tomllib
from dataclasses import replace
from pathlib import Path

from coderio.config.models import (
    Config,
    ModelConfig,
    ToolsConfig,
    SkillsConfig,
    CliConfig,
    SessionConfig,
)


def _read_toml(path: Path) -> dict:
    if path.is_file():
        with open(path, "rb") as f:
            return tomllib.load(f)
    return {}


def _merge(base: dict, override: dict) -> dict:
    """Shallow-merge per section; override wins at the key level."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for k, v in override.items():
        if isinstance(v, dict):
            out.setdefault(k, {})
            out[k].update(v)
        else:
            out[k] = v
    return out


def _find_project_dir(search_from: Path) -> Path:
    """Walk upward from search_from looking for a project's .coderio/config.toml.

    Never ascends into the user's home directory (the home ~/.coderio is the USER
    layer, handled separately), so a temp dir nested under home won't accidentally
    pick up the real user config as if it were a project config.
    """
    cur = search_from.resolve()
    home = Path(os.path.expanduser("~")).resolve()
    for parent in [cur, *cur.parents]:
        if parent == home:
            break
        if (parent / ".coderio" / "config.toml").is_file():
            return parent
    return search_from.resolve()


def _default_user_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".coderio"


def _from_dict(data: dict) -> Config:
    cfg = Config()
    m = data.get("model", {})
    t = data.get("tools", {})
    s = data.get("skills", {})
    se = data.get("session", {})
    cl = data.get("cli", {})

    # Validate int fields — TOML may give strings/bools if the user mis-types.
    def _int(section: dict, key: str, default: int, section_name: str) -> int:
        v = section.get(key, default)
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(
                f"config.toml [{section_name}] {key} 必须是整数，但得到 {type(v).__name__}: {v!r}")
        return v

    # Validate permission_mode against the known enum values.
    perm = t.get("permission_mode", cfg.tools.permission_mode)
    if isinstance(perm, str):
        perm_lower = perm.lower()
        valid = ("confirm", "plan", "auto")
        if perm_lower not in valid:
            raise ValueError(
                f"config.toml [tools] permission_mode='{perm}' 无效。"
                f"可选值: {', '.join(valid)}")
        perm = perm_lower

    return Config(
        model=ModelConfig(
            default=m.get("default", cfg.model.default),
            provider=m.get("provider", cfg.model.provider),
            base_url=m.get("base_url", cfg.model.base_url),
            provider_id=m.get("provider_id", ""),
            max_output_tokens=_int(m, "max_output_tokens", cfg.model.max_output_tokens, "model"),
        ),
        tools=ToolsConfig(
            bash_shell=t.get("bash_shell", cfg.tools.bash_shell),
            permission_mode=perm,
            max_tool_rounds=_int(t, "max_tool_rounds", cfg.tools.max_tool_rounds, "tools"),
        ),
        skills=SkillsConfig(
            auto_load=s.get("auto_load", cfg.skills.auto_load),
            stage_auto_inject=s.get("stage_auto_inject", cfg.skills.stage_auto_inject),
            harness=s.get("harness", cfg.skills.harness),
            repo_url=s.get("repo_url", cfg.skills.repo_url),
        ),
        session=SessionConfig(
            save_dir=se.get("save_dir", cfg.session.save_dir),
        ),
        cli=CliConfig(
            theme=cl.get("theme", cfg.cli.theme),
            show_tool_output=cl.get("show_tool_output", cfg.cli.show_tool_output),
        ),
    )


def _apply_env(cfg: Config) -> Config:
    model = cfg.model
    v = os.environ.get("CODERIO_MODEL")
    if v:
        model = replace(model, default=v)
    v = os.environ.get("CODERIO_PROVIDER")
    if v:
        model = replace(model, provider=v)
    tools = cfg.tools
    v = os.environ.get("CODERIO_BASH_SHELL")
    if v:
        tools = replace(tools, bash_shell=v)
    return Config(model=model, tools=tools, skills=cfg.skills, session=cfg.session, cli=cfg.cli)


def load_config(search_from: Path | str = ".", user_dir: Path | str | None = None) -> Config:
    """Load config merging: defaults < user < project < env.

    Layers (low->high): built-in defaults, user ~/.coderio, project ./.coderio, env vars.
    """
    search_from = Path(search_from)
    if user_dir is None:
        user_dir = _default_user_dir()
    data = _merge(_read_toml(Path(user_dir) / "config.toml"),
                  _read_toml(_find_project_dir(search_from) / ".coderio" / "config.toml"))
    cfg = _from_dict(data)
    return _apply_env(cfg)
