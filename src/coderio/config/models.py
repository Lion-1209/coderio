from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModelConfig:
    default: str = "glm-4.5"
    provider: str = "openai_compatible"
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    provider_id: str = ""
    max_output_tokens: int = 16384


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


@dataclass
class ToolsConfig:
    bash_shell: str = ""
    permission_mode: str = "confirm"
    max_tool_rounds: int = 25


@dataclass
class SkillsConfig:
    auto_load: bool = True
    stage_auto_inject: bool = True
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
    trigger_ratio: float = 0.6         # compact at 60% of the context window (lowered
                                       # from 0.75 — a 30-read_file analysis session
                                       # hit 61k tokens without triggering; 60% gives
                                       # earlier, healthier compaction)
    keep_recent: int = 8               # messages preserved verbatim at the tail
    model_context_limit: int = 128_000  # assumed window size; conservative default


@dataclass
class Config:
    model: ModelConfig = None
    tools: ToolsConfig = None
    skills: SkillsConfig = None
    session: SessionConfig = None
    cli: CliConfig = None
    context: ContextConfig = None
    # Named profiles (multi-config). Empty list = legacy single-config mode:
    # build_chat_model falls through to the [model] section's 3-layer path,
    # so existing users with no profiles are unaffected.
    profiles: list = None
    active_profile: str = ""

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
