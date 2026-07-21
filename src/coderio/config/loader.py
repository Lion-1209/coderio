from __future__ import annotations

import os
import tomllib
from dataclasses import replace
from pathlib import Path

from coderio.config.models import (
    Config,
    ModelConfig,
    Profile,
    ToolsConfig,
    SkillsConfig,
    CliConfig,
    ContextConfig,
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


def _parse_profiles(data: dict) -> list:
    """Parse the [[profiles]] array into Profile objects.

    Each table element must have at least name + provider_id + model. base_url
    and kind fall back to "" and "openai_compatible". Malformed entries (missing
    required fields) are skipped rather than crashing — a typo in one profile
    shouldn't prevent the whole config from loading.
    """
    raw = data.get("profiles")
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        pid = entry.get("provider_id")
        model = entry.get("model")
        if not (name and pid and model):
            continue  # incomplete profile — skip silently
        # context_limit is optional and best-effort: a missing/non-int value
        # just means "not probed yet" (0), don't raise — the runtime falls back
        # to ContextConfig.model_context_limit.
        cl_raw = entry.get("context_limit", 0)
        context_limit = cl_raw if isinstance(cl_raw, int) and not isinstance(cl_raw, bool) else 0
        out.append(Profile(
            name=name,
            provider_id=pid,
            model=model,
            base_url=entry.get("base_url", ""),
            kind=entry.get("kind", "openai_compatible"),
            context_limit=context_limit,
        ))
    return out


def _resolve_active_profile(data: dict) -> str:
    """Resolve the active profile name from config, with a sane default.

    Returns the explicit `active_profile` value if set and non-empty. Otherwise
    empty string — build_chat_model treats empty active_profile as "use the
    legacy [model] path", so old users with no profiles are unaffected. (The
    "default to first profile when active is unset" lives in build_chat_model,
    not here, because the loader shouldn't mutate user intent on disk.)
    """
    active = data.get("active_profile", "")
    if isinstance(active, str):
        return active.strip()
    return ""


def _from_dict(data: dict) -> Config:
    cfg = Config()
    m = data.get("model", {})
    t = data.get("tools", {})
    s = data.get("skills", {})
    se = data.get("session", {})
    cl = data.get("cli", {})
    cx = data.get("context", {})

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

    # context_limit is optional in [model]; a missing/non-int value falls back
    # to 0 (not probed). Don't use the strict _int() helper — context_limit is
    # a best-effort optimization, not a required config field.
    m_cl_raw = m.get("context_limit", 0)
    m_context_limit = m_cl_raw if isinstance(m_cl_raw, int) and not isinstance(m_cl_raw, bool) else 0
    return Config(
        model=ModelConfig(
            default=m.get("default", cfg.model.default),
            provider=m.get("provider", cfg.model.provider),
            base_url=m.get("base_url", cfg.model.base_url),
            provider_id=m.get("provider_id", ""),
            max_output_tokens=_int(m, "max_output_tokens", cfg.model.max_output_tokens, "model"),
            context_limit=m_context_limit,
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
        context=ContextConfig(
            enabled=cx.get("enabled", cfg.context.enabled),
            trigger_ratio=cx.get("trigger_ratio", cfg.context.trigger_ratio),
            keep_recent=_int(cx, "keep_recent", cfg.context.keep_recent, "context"),
            model_context_limit=_int(cx, "model_context_limit", cfg.context.model_context_limit, "context"),
        ),
        profiles=_parse_profiles(data),
        active_profile=_resolve_active_profile(data),
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
    # Preserve profiles/active_profile/context — env overrides only touch model/tools.
    return Config(model=model, tools=tools, skills=cfg.skills, session=cfg.session,
                  cli=cfg.cli, context=cfg.context,
                  profiles=cfg.profiles, active_profile=cfg.active_profile)


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
