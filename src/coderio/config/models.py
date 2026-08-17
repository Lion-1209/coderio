from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModelConfig:
    default: str = "glm-4.5"
    provider: str = "openai_compatible"
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    provider_id: str = ""
    max_output_tokens: int = 16384
    # Context window size (tokens) for [model].default, probed at setup time.
    # 0 = not probed (fall back to ContextConfig.model_context_limit). Mirrors
    # Profile.context_limit for the legacy single-config path. Kept on ModelConfig
    # (not ContextConfig) because it's per-model, not per-compaction-policy.
    context_limit: int = 0


@dataclass
class Profile:
    """A named, self-contained model configuration (provider + model + endpoint).

    Bundles everything build_chat_model needs to construct a chat client, so the
    user can keep several providers side by side (e.g. a Coding Plan subscription
    and a personal OpenAI key) and switch between them with /profile without
    re-running onboarding. The API key itself lives in the credentials file
    (keyed by provider_id), not here — same as the legacy [model] path.
    """

    name: str
    provider_id: str
    model: str
    base_url: str = ""
    kind: str = "openai_compatible"
    # Context window size (tokens) for THIS model, discovered at setup time by
    # probing the provider's /v1/models/{id} endpoint. 0 = not probed (fall back
    # to ContextConfig.model_context_limit). Stored per-profile because different
    # providers/models have different windows — without this, a 256K model like
    # step-3.7-flash gets mis-treated as the global default (200K), triggering
    # compaction at 120K instead of 153K.
    context_limit: int = 0


@dataclass
class ToolsConfig:
    bash_shell: str = ""
    permission_mode: str = "confirm"
    # Workspace root for the shell backend's CWD. Empty = use the process CWD
    # (the directory coderio was launched from). File-path isolation is handled
    # by deepagents' backend virtual_mode (not by a coderio-side policy) — this
    # value only affects where shell commands run.
    workspace_root: str = ""
    # Extra regex patterns appended to the built-in command blacklist. The
    # built-in defaults (rm -rf /, mkfs, fork bomb, etc.) are always active;
    # these are ADDITIONAL blocks for project-specific commands you never want
    # run (e.g. ["git push --force", "npm publish"]). See command_policy.py.
    blocked_commands: list[str] = field(default_factory=list)
    # Master switch for web_fetch / web_search. False = offline mode (both web
    # tools blocked). True (default) = web tools available subject to the
    # normal permission gate.
    network_allowed: bool = True
    # Whitelist mode: when True, commands whose first token isn't in the
    # allowed set (built-in + allowed_commands) are flagged for confirmation
    # (NOT hard-blocked — FULL mode still allows them). Defaults to False
    # (blacklist-only, backward compatible). See command_policy.py.
    whitelist_mode: bool = False
    # User-supplied additions to the built-in command whitelist. Applied only
    # when whitelist_mode is True. E.g. ["docker", "kubectl"] to allow those
    # without confirmation in whitelist mode.
    allowed_commands: list[str] = field(default_factory=list)
    # Sandbox mode: OS-level isolation for the execute tool.
    #   "off"    = no OS sandbox (regex blacklist + whitelist only) [default]
    #   "job"    = Job Object resource limits + reliable process-tree kill
    #              (prevents fork bombs, OOM, orphaned grandchildren)
    #   "write"  = Job Object + Windows Restricted Token write isolation
    #              (reads system-wide, writes gated — needs Win10+, no admin)
    # On Linux, "write" uses bubblewrap if available (see linux_sandbox.py).
    sandbox_mode: str = "off"
    # When True + sandbox_mode != "off", the execute (shell) tool auto-approves
    # without a confirmation prompt (Claude Code's "autoAllowBashIfSandboxed"
    # design: the sandbox provides the real isolation boundary, so per-command
    # prompts become noise). Defaults False (backward compat). The blacklist
    # still applies — rm -rf / is blocked even in auto-allow mode.
    auto_allow_if_sandboxed: bool = False
    # Filesystem isolation 4-tuple for the sandbox (Linux bubblewrap only;
    # Windows ignores it — token is no-op). See SandboxFsConfig.
    sandbox_fs: "SandboxFsConfig | None" = None


@dataclass
class SandboxFsConfig:
    """Filesystem isolation config for the sandbox (Claude-Code-compatible 4-tuple).

    Applied on Linux via bubblewrap mounts. Windows ignores these (the token
    is currently a no-op — see win_sandbox.py docstring). All paths support:
      - ``~/foo``  → home-relative
      - ``./foo``  → workspace-relative
      - ``foo``    → workspace-relative (same as ./)
      - ``/abs``   → absolute

    Semantics (bwrap mount order matters — later mounts override earlier):
      - workspace is ALWAYS read-write (built-in, no need to list it)
      - ``allow_write``  → extra read-write mounts (e.g. /tmp/build, ~/.cache)
      - ``deny_write``   → read-only override (e.g. .git/hooks inside workspace)
      - ``deny_read``    → tmpfs blackhole (path exists but contents invisible)
      - ``allow_read``   → read-only re-mount punching through a deny_read
    """

    allow_write: list[str] = field(default_factory=list)
    deny_write: list[str] = field(default_factory=list)
    deny_read: list[str] = field(default_factory=list)
    allow_read: list[str] = field(default_factory=list)


@dataclass
class HookSpec:
    """One ``[[hooks]]`` entry — a user shell command fired at a lifecycle point.

    See agent/hooks.py for the IO contract (stdin JSON, exit 0/2 semantics)
    and the event set. Hooks live in config.toml, so repo-level hooks ride the
    existing repo-config trust gate (config/trust.py) — no separate trust flow.
    """

    event: str  # SessionStart | UserPromptSubmit | PreToolUse | PostToolUse | Stop
    command: str  # shell command; receives event JSON on stdin
    matcher: str = ""  # regex on tool_name (tool events only); "" = all
    timeout: int = 60  # seconds; timeout is fail-open, never bricks the turn


@dataclass
class SkillsConfig:
    auto_load: bool = True
    harness: bool = True
    repo_url: str = "https://github.com/Lion-1209/Lion-Skills"


@dataclass
class CliConfig:
    theme: str = "dark"
    show_tool_output: bool = True


@dataclass
class SessionConfig:
    save_dir: str = "~/.coderio/sessions"


@dataclass
class ContextConfig:
    """Context-window compaction settings (spec: harness phase 2).

    When the provider-reported input_tokens exceeds ``trigger_ratio`` of
    ``model_context_limit``, old messages are summarized into a single system
    message and the most recent ``keep_recent`` are kept verbatim. Disabled
    when ``enabled=False`` (no compaction attempts, original behavior).
    """

    enabled: bool = True
    trigger_ratio: float = 0.6  # compact at 60% of the context window (lowered
    # from 0.75 — a 30-read_file analysis session
    # hit 61k tokens without triggering; 60% gives
    # earlier, healthier compaction)
    keep_recent: int = 8  # messages preserved verbatim at the tail
    model_context_limit: int = 200_000  # assumed window size when the active
    # profile has no probed context_limit.
    # Raised from 128K (too aggressive — a
    # 256K model was compacting at 76K) to
    # 200K, the floor for modern models
    # (Claude, GPT-4o, step-3.7). The real
    # value comes from probe_context_limit
    # at setup time, stored per-profile.


@dataclass
class Config:
    # All sub-config fields default to None and are populated in __post_init__.
    # The `Type = None` default is a dataclass idiom: the field IS declared as
    # the concrete type, but defaults to None before __post_init__ runs. mypy
    # accepts this for dataclasses (the None is the "not yet initialized"
    # sentinel, replaced in __post_init__ which the loader always calls).
    model: ModelConfig = None  # type: ignore[assignment]
    tools: ToolsConfig = None  # type: ignore[assignment]
    skills: SkillsConfig = None  # type: ignore[assignment]
    session: SessionConfig = None  # type: ignore[assignment]
    cli: CliConfig = None  # type: ignore[assignment]
    context: ContextConfig = None  # type: ignore[assignment]
    # Named profiles (multi-config). Empty list = legacy single-config mode:
    # build_chat_model falls through to the [model] section's 3-layer path,
    # so existing users with no profiles are unaffected.
    profiles: list = None  # type: ignore[assignment]
    active_profile: str = ""
    # User hooks ([[hooks]] tables). Empty list = no hooks (default).
    hooks: list = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.model is None:
            object.__setattr__(self, "model", ModelConfig())
        if self.tools is None:
            object.__setattr__(self, "tools", ToolsConfig())
        if self.skills is None:
            object.__setattr__(self, "skills", SkillsConfig())
        if self.session is None:
            object.__setattr__(self, "session", SessionConfig())
        if self.cli is None:
            object.__setattr__(self, "cli", CliConfig())
        if self.context is None:
            object.__setattr__(self, "context", ContextConfig())
        if self.profiles is None:
            object.__setattr__(self, "profiles", [])
        if self.hooks is None:
            object.__setattr__(self, "hooks", [])
