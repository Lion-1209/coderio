from __future__ import annotations

from pathlib import Path

STRUCTURE = (".coderio", ".coderio/skills", ".coderio/sessions", ".coderio/logs")

_SAMPLE_CONFIG = """# coderio configuration. See docs/superpowers/specs/2026-06-25-coderio-s0-core-design.md
# API keys are read from env vars (OPENAI_API_KEY / ANTHROPIC_API_KEY / Z_API_KEY),
# never stored here.

[model]
default = "glm-4.5"
provider = "openai_compatible"        # openai_compatible | anthropic
base_url = "https://open.bigmodel.cn/api/paas/v4"

[tools]
bash_shell = ""                       # empty = auto-detect Git Bash
permission_mode = "confirm"           # confirm | plan | auto
max_tool_rounds = 25

[skills]
auto_load = true
stage_auto_inject = true

[session]
save_dir = "~/.coderio/sessions"
"""


def ensure_user_dirs(user_dir: Path | str | None = None) -> Path:
    """Create ~/.coderio skeleton if missing. Idempotent: never overwrites config.toml.

    Returns the .coderio directory path.
    """
    if user_dir is None:
        base = Path.home()
    else:
        base = Path(user_dir)
    root = base / ".coderio"
    for sub in ("skills", "sessions", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    cfg = root / "config.toml"
    if not cfg.is_file():
        cfg.write_text(_SAMPLE_CONFIG, encoding="utf-8")
    return root
