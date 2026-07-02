from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class InstallResult:
    success: bool
    action: str = ""
    skills: list[str] = field(default_factory=list)
    message: str = ""


def _list_skills(target: Path) -> list[str]:
    return sorted(p.parent.name for p in target.glob("*/SKILL.md"))


def install_skills(repo_url: str, target_dir: Path | str, force: bool = False) -> InstallResult:
    """Clone repo_url into target_dir, or git-pull if it already exists as a repo.

    - target missing OR empty -> clone (normal first install)
    - target is a git repo -> git pull (update)
    - target non-empty and not a git repo -> error unless --force
    """
    target = Path(target_dir)
    target.parent.mkdir(parents=True, exist_ok=True)

    def _is_empty(d: Path) -> bool:
        return not d.is_dir() or not any(d.iterdir())

    if not target.exists() or _is_empty(target):
        if target.exists():
            target.rmdir()
        try:
            subprocess.run(
                ["git", "clone", "--quiet", repo_url, str(target)],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as e:
            return InstallResult(success=False, message=f"git clone failed: {e.stderr.strip() or e}")
        return InstallResult(
            success=True,
            action="cloned",
            skills=_list_skills(target),
            message=f"Cloned {repo_url} -> {target}",
        )

    if not (target / ".git").is_dir():
        if force:
            import shutil
            shutil.rmtree(target)
            return install_skills(repo_url, target, force=False)
        return InstallResult(
            success=False,
            message=f"{target} exists, is not empty, and is not a git repo (use --force to overwrite).",
        )

    try:
        subprocess.run(
            ["git", "-C", str(target), "pull", "--quiet"],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        return InstallResult(success=False, message=f"git pull failed: {e.stderr.strip() or e}")
    return InstallResult(
        success=True,
        action="updated",
        skills=_list_skills(target),
        message=f"Updated {target}",
    )
