import os
import shutil
import subprocess

import pytest

from coderio.cli.skills_cmd import install_skills

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _make_skill_repo(path):
    """Create a real git repo with one skill, return its path."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    sk = path / "my-skill"
    sk.mkdir()
    (sk / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: test\n---\nbody", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return path


def test_clone_into_empty(tmp_path):
    repo = _make_skill_repo(tmp_path / "repo")
    target = tmp_path / "skills"
    result = install_skills(repo_url=str(repo), target_dir=target)
    assert result.success
    assert (target / "my-skill" / "SKILL.md").is_file()
    assert "my-skill" in result.skills


def test_pull_updates_existing(tmp_path):
    repo = _make_skill_repo(tmp_path / "repo")
    target = tmp_path / "skills"
    install_skills(repo_url=str(repo), target_dir=target)
    sk2 = repo / "second-skill"
    sk2.mkdir()
    (sk2 / "SKILL.md").write_text(
        "---\nname: second-skill\ndescription: 2nd\n---\nbody", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add second"], cwd=repo, check=True)
    result = install_skills(repo_url=str(repo), target_dir=target)
    assert result.success
    assert "second-skill" in result.skills
    assert result.action == "updated"


def test_non_git_target_errors(tmp_path):
    target = tmp_path / "skills"
    target.mkdir()
    (target / "junk").write_text("not a repo", encoding="utf-8")
    result = install_skills(repo_url="https://example/x", target_dir=target)
    assert not result.success
    assert "not a git" in result.message.lower() or "exists" in result.message.lower()


def test_empty_target_clones_without_force(tmp_path):
    """Regression: an empty skills dir (created by ensure_user_dirs) must clone
    without requiring --force (normal first-install case)."""
    repo = _make_skill_repo(tmp_path / "repo")
    target = tmp_path / "skills"
    target.mkdir()
    result = install_skills(repo_url=str(repo), target_dir=target)
    assert result.success
    assert result.action == "cloned"
    assert (target / "my-skill" / "SKILL.md").is_file()
