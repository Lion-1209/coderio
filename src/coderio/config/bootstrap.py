from __future__ import annotations

from pathlib import Path

STRUCTURE = (".coderio", ".coderio/skills", ".coderio/sessions", ".coderio/logs")

_SAMPLE_CONFIG = """# coderio configuration.
# The [model] section is populated by the onboarding wizard on first run.
# You can also edit it manually. API keys are stored in ~/.coderio/credentials.

[tools]
bash_shell = ""                       # empty = auto-detect (Git Bash on Windows, bash on Linux/macOS)
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
