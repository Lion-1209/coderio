from __future__ import annotations

import os
import tomllib
from dataclasses import replace
from pathlib import Path

from coderio.config.models import (
    CliConfig,
    Config,
    ContextConfig,
    ModelConfig,
    Profile,
    SandboxFsConfig,
    SessionConfig,
    SkillsConfig,
    ToolsConfig,
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


def _find_project_dir(search_from: Path | str) -> Path:
    """Walk upward from search_from looking for a project's .coderio/config.toml.

    Accepts str or Path. Never ascends into the user's home directory (the home
    ~/.coderio is the USER layer, handled separately).
    """
    cur = Path(search_from).resolve()
    home = Path(os.path.expanduser("~")).resolve()
    for parent in [cur, *cur.parents]:
        if parent == home:
            break
        if (parent / ".coderio" / "config.toml").is_file():
            return parent
    return cur


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
        out.append(
            Profile(
                name=name,
                provider_id=pid,
                model=model,
                base_url=entry.get("base_url", ""),
                kind=entry.get("kind", "openai_compatible"),
                context_limit=context_limit,
            )
        )
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


def _read_fs_list(data: dict, key: str, default: list[str]) -> list[str]:
    """Read a list-of-strings field from a config subtable (inline copy of
    _from_dict._str_list, which is a closure-local helper and not visible here).

    Returns the default on missing/malformed values — a bad config shouldn't
    block startup.
    """
    v = data.get(key, default)
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return v
    return default


def _read_sandbox_fs(data, default: SandboxFsConfig | None) -> SandboxFsConfig | None:
    """Read the [tools.sandbox_filesystem] subtable into a SandboxFsConfig.

    Returns the `default` unchanged when the subtable is empty/missing, so
    users who don't configure filesystem isolation get None (bubblewrap uses
    its built-in workspace-only layout).

    Per-key fallbacks come from SandboxFsConfig()'s FIELD DEFAULTS (caught in
    self-test: passing [] here bypassed the deny_write default, so a table
    that omitted deny_write silently lost the ~/.coderio protection).
    """
    if not isinstance(data, dict) or not data:
        return default
    base = default if default is not None else SandboxFsConfig()
    return SandboxFsConfig(
        allow_write=_read_fs_list(data, "allow_write", base.allow_write),
        deny_write=_read_fs_list(data, "deny_write", base.deny_write),
        deny_read=_read_fs_list(data, "deny_read", base.deny_read),
        allow_read=_read_fs_list(data, "allow_read", base.allow_read),
    )


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
            raise ValueError(f"config.toml [{section_name}] {key} 必须是整数，但得到 {type(v).__name__}: {v!r}")
        return v

    def _bool(section: dict, key: str, default: bool) -> bool:
        v = section.get(key, default)
        if not isinstance(v, bool):
            # TOML's true/false parse to Python bool; anything else is a typo.
            # Don't raise — fall back to default so a bad value doesn't block startup.
            return default
        return v

    def _str_list(section: dict, key: str, default: list[str]) -> list[str]:
        v = section.get(key, default)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return v
        # Not a list of strings — fall back to default rather than crashing.
        # A bad config value shouldn't block startup; the user will notice when
        # their extra blocklist doesn't take effect.
        return default

    # Validate permission_mode against the known enum values.
    perm = t.get("permission_mode", cfg.tools.permission_mode)
    if isinstance(perm, str):
        perm_lower = perm.lower()
        valid = ("confirm", "plan", "auto", "full", "auto_edit")
        if perm_lower not in valid:
            raise ValueError(f"config.toml [tools] permission_mode='{perm}' 无效。可选值: {', '.join(valid)}")
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
            workspace_root=t.get("workspace_root", cfg.tools.workspace_root),
            blocked_commands=_str_list(t, "blocked_commands", cfg.tools.blocked_commands),
            network_allowed=_bool(t, "network_allowed", cfg.tools.network_allowed),
            whitelist_mode=_bool(t, "whitelist_mode", cfg.tools.whitelist_mode),
            allowed_commands=_str_list(t, "allowed_commands", cfg.tools.allowed_commands),
            sandbox_mode=t.get("sandbox_mode", cfg.tools.sandbox_mode),
            auto_allow_if_sandboxed=_bool(t, "auto_allow_if_sandboxed", cfg.tools.auto_allow_if_sandboxed),
            sandbox_fs=_read_sandbox_fs(t.get("sandbox_filesystem", {}), cfg.tools.sandbox_fs),
        ),
        skills=SkillsConfig(
            auto_load=s.get("auto_load", cfg.skills.auto_load),
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
        hooks=_parse_hooks(data),
    )


def _parse_hooks(data: dict) -> list:
    """Parse the [[hooks]] array-of-tables into agent.hooks.HookSpec list.

    SINGLE SOURCE OF TRUTH (2026-08-14 v3 audit P0): this MUST produce
    agent.hooks.HookSpec — the class HookRunner.fire actually uses. An earlier
    duplicate dataclass in config/models.py (without .matches()) made every
    tool-event hook from a real config.toml crash with AttributeError while 20
    in-module tests stayed green (the seam, not the modules, was untested).

    Follows _parse_profiles' resilience: a malformed entry (missing event or
    command, wrong types) is skipped with a warning, not a startup crash — a
    bad hook config must not take the agent down. Unknown events are kept and
    filtered later by HookRunner (keeps parsing decoupled from the event set).
    """
    from coderio.agent.hooks import HookSpec

    raw = data.get("hooks", [])
    if not isinstance(raw, list):
        return []
    out: list[HookSpec] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        event = entry.get("event", "")
        command = entry.get("command", "")
        if not isinstance(event, str) or not event or not isinstance(command, str) or not command:
            continue
        matcher = entry.get("matcher", "")
        timeout = entry.get("timeout", 60)
        if not isinstance(matcher, str):
            matcher = ""
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            timeout = 60
        out.append(HookSpec(event=event, command=command, matcher=matcher, timeout=timeout))
    return out


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
    # Preserve profiles/active_profile/context/hooks — env overrides only touch model/tools.
    return Config(
        model=model,
        tools=tools,
        skills=cfg.skills,
        session=cfg.session,
        cli=cfg.cli,
        context=cfg.context,
        profiles=cfg.profiles,
        active_profile=cfg.active_profile,
        hooks=cfg.hooks,
    )


def load_config(search_from: Path | str = ".", user_dir: Path | str | None = None) -> Config:
    """Load config merging: defaults < user < project < env.

    Layers (low->high): built-in defaults, user ~/.coderio, project ./.coderio, env vars.
    """
    search_from = Path(search_from)
    if user_dir is None:
        user_dir = _default_user_dir()
    user_data = _read_toml(Path(user_dir) / "config.toml")
    proj_data = _read_toml(_find_project_dir(search_from) / ".coderio" / "config.toml")
    data = _merge(user_data, proj_data)
    # hooks APPEND instead of overwrite (v3 audit P2). _merge replaces lists
    # wholesale, so a repo's [[hooks]] silently DROPPED the user's protective
    # hooks. Hooks are the one key where user config must survive a repo's
    # override: fire() is first-blocker-wins, so the user's hooks go FIRST —
    # their deny reason is the one the model sees. (Only list-merge in the
    # loader; every other key keeps _merge's replace semantics.)
    if isinstance(proj_data.get("hooks"), list) or isinstance(user_data.get("hooks"), list):
        data["hooks"] = (user_data.get("hooks") or []) + (proj_data.get("hooks") or [])
    cfg = _from_dict(data)
    return _apply_env(cfg)
