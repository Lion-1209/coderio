from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelConfig:
    default: str = "glm-4.5"
    provider: str = "openai_compatible"
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    provider_id: str = ""
    max_output_tokens: int = 16384


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
class Config:
    model: ModelConfig = None
    tools: ToolsConfig = None
    skills: SkillsConfig = None
    session: SessionConfig = None
    cli: CliConfig = None

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
