"""Tests for the workspace path-boundary policy (read/write separation).

Guards the P0 security fix: write tools can't escape the workspace root, but
read tools stay free (an agent must be able to read site-packages / system
configs to do its job). AutoPermissionGate also enforces the policy — --auto
skips confirmation, not the security floor.
"""
import os
from pathlib import Path

import pytest

from coderio.tools.workspace import WorkspacePolicy
from coderio.tools.permission import (
    PermissionGate,
    PermissionMode,
    AutoPermissionGate,
)


# --- WorkspacePolicy unit tests ---

def test_write_outside_root_blocked(tmp_path):
    """A write_file to ../escape.py must be blocked."""
    p = WorkspacePolicy(root=tmp_path)
    allowed, _ = p.check("write_file", {"path": str(tmp_path.parent / "escape.py")})
    assert allowed is False


def test_write_inside_root_allowed(tmp_path):
    """A write_file to root/a.py must pass."""
    p = WorkspacePolicy(root=tmp_path)
    allowed, _ = p.check("write_file", {"path": str(tmp_path / "a.py")})
    assert allowed is True


def test_edit_outside_root_blocked(tmp_path):
    """edit_file to an absolute path outside root must be blocked."""
    p = WorkspacePolicy(root=tmp_path)
    allowed, _ = p.check("edit_file", {"path": os.path.join(os.sep, "etc", "hosts")})
    assert allowed is False


def test_bash_cwd_outside_blocked(tmp_path):
    """bash with cwd=/tmp (outside root) must be blocked."""
    p = WorkspacePolicy(root=tmp_path)
    allowed, _ = p.check("bash", {"command": "ls", "cwd": os.path.join(os.sep, "tmp")})
    assert allowed is False


def test_bash_cwd_inside_allowed(tmp_path):
    """bash with cwd=root/sub must pass."""
    sub = tmp_path / "sub"
    sub.mkdir()
    p = WorkspacePolicy(root=tmp_path)
    allowed, _ = p.check("bash", {"command": "ls", "cwd": str(sub)})
    assert allowed is True


def test_bash_empty_cwd_allowed(tmp_path):
    """bash with no cwd (empty = process default, typically root itself) passes."""
    p = WorkspacePolicy(root=tmp_path)
    allowed, _ = p.check("bash", {"command": "ls"})
    assert allowed is True


def test_read_outside_root_allowed(tmp_path):
    """REGRESSION (read/write separation): read_file to /etc/hosts must PASS —
    reads are unconstrained so the agent can analyze external files."""
    p = WorkspacePolicy(root=tmp_path)
    allowed, _ = p.check("read_file", {"path": os.path.join(os.sep, "etc", "hosts")})
    assert allowed is True


def test_grep_glob_listdir_outside_allowed(tmp_path):
    """All read-class tools pass even with out-of-workspace paths."""
    p = WorkspacePolicy(root=tmp_path)
    for tool in ("grep", "glob", "list_dir"):
        allowed, _ = p.check(tool, {"path": os.path.join(os.sep, "etc"),
                                    "pattern": "x"})
        assert allowed is True, f"{tool} should be unconstrained"


def test_pathless_tools_unaffected(tmp_path):
    """Tools without path args (web_search, todo, note) are never blocked."""
    p = WorkspacePolicy(root=tmp_path)
    for tool in ("web_search", "web_fetch", "todo"):
        allowed, _ = p.check(tool, {"query": "x"})
        assert allowed is True


def test_dotdot_resolved_and_blocked(tmp_path):
    """../../escape must be resolved (not lexically matched) and blocked."""
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    p = WorkspacePolicy(root=tmp_path)
    # From sub, ../../.. goes above tmp_path
    escape = str(sub / ".." / ".." / ".." / "escape.py")
    allowed, _ = p.check("write_file", {"path": escape})
    assert allowed is False


def test_symlink_escape_blocked(tmp_path):
    """A symlink inside root pointing outside is resolved and blocked."""
    if os.name == "nt":
        pytest.skip("symlink test unreliable on Windows without admin")
    link = tmp_path / "link"
    target = tmp_path.parent / "secret"
    target.write_text("secret")
    os.symlink(target, link)
    p = WorkspacePolicy(root=tmp_path)
    allowed, _ = p.check("write_file", {"path": str(link / "x")})
    assert allowed is False


def test_empty_root_uses_cwd(tmp_path, monkeypatch):
    """When root is empty, the policy uses the process CWD."""
    monkeypatch.chdir(tmp_path)
    p = WorkspacePolicy(root="")
    assert p.root == tmp_path.resolve()


# --- PermissionGate integration tests ---

def test_auto_mode_enforces_workspace(tmp_path):
    """REGRESSION (P0): AutoPermissionGate must STILL block workspace escapes.

    --auto mode skips interactive confirmation, NOT the security floor. Without
    this, a model in auto mode could write anywhere on the filesystem."""
    policy = WorkspacePolicy(root=tmp_path)
    gate = AutoPermissionGate(policy=policy)
    # Write inside: allowed (auto + inside)
    assert gate.check("write_file", {"path": str(tmp_path / "a.py")}) is True
    # Write outside: BLOCKED even in auto mode
    assert gate.check("write_file", {"path": str(tmp_path.parent / "x.py")}) is False
    # Read outside: allowed (read tools unconstrained)
    assert gate.check("read_file", {"path": str(tmp_path.parent / "x.py")}) is True


def test_plan_mode_enforces_workspace(tmp_path):
    """Plan mode also enforces the workspace boundary on write tools."""
    policy = WorkspacePolicy(root=tmp_path)
    gate = PermissionGate(PermissionMode.PLAN, policy=policy)
    # Write outside: blocked by BOTH policy AND plan mode
    assert gate.check("write_file", {"path": str(tmp_path.parent / "x.py")}) is False
    # Write inside: blocked by plan mode (destructive), but NOT by policy
    assert gate.check("write_file", {"path": str(tmp_path / "a.py")}) is False


def test_no_policy_means_no_path_check():
    """Backward compatibility: policy=None (the default) skips path validation.

    Existing tests and headless uses that don't pass a policy keep working
    exactly as before — no surprise breakage."""
    gate = AutoPermissionGate()  # no policy
    assert gate.check("write_file", {"path": "/anywhere/x.py"}) is True


def test_workspace_root_from_config(tmp_path):
    """The workspace_root config field flows through to the policy."""
    from coderio.config.models import ToolsConfig
    cfg = ToolsConfig(workspace_root=str(tmp_path))
    policy = WorkspacePolicy(root=cfg.workspace_root)
    assert policy.root == tmp_path.resolve()
    allowed, _ = policy.check("write_file", {"path": str(tmp_path / "ok.py")})
    assert allowed is True
