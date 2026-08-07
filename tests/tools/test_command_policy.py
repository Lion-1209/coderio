"""Tests for the command-content review policy (shell blacklist + network).

The policy is pure logic (no I/O, no middleware) — these tests verify the
regex patterns catch destructive commands and let safe ones through.

This is the cheapest layer of defense (P0-1 from the 2026-08-07 report):
it won't stop a determined adversary (obfuscated commands bypass regex), but
it catches the accidental/careless cases that cause real-world damage.
"""

from __future__ import annotations

import pytest

from coderio.tools.command_policy import CommandPolicy

# --------------------------------------------------------------- default blacklist


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf / ",  # trailing space
        "rm -rf /*",  # glob on root
        "rm -fr /",  # flag order variant
        "rm -rf /home",  # starts with root
        "sudo rm -rf /",
    ],
)
def test_blocks_recursive_root_delete(command):
    """The classic catastrophe: `rm -rf /` must always be blocked."""
    p = CommandPolicy.default()
    assert p.check_command(command) is not None, f"should block: {command!r}"


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf ~",
        "rm -rf ~/",
        "rm -rf ~/",
        "rm -rf $HOME",
        "rm -rf $HOME/",
    ],
)
def test_blocks_recursive_home_delete(command):
    """`rm -rf ~` nukes the user's home directory — must be blocked."""
    p = CommandPolicy.default()
    assert p.check_command(command) is not None


def test_blocks_rm_rf_star():
    """`rm -rf *` deletes everything in CWD — blocked."""
    p = CommandPolicy.default()
    assert p.check_command("rm -rf *") is not None


def test_allows_targeted_rm():
    """`rm -rf ./build` (a subdirectory) is a normal build-cleanup command."""
    p = CommandPolicy.default()
    assert p.check_command("rm -rf ./build") is None
    assert p.check_command("rm -rf build") is None
    assert p.check_command("rm -rf node_modules dist") is None


@pytest.mark.parametrize(
    "command",
    [
        "mkfs.ext4 /dev/sda",
        "mkfs /dev/sdb",
        "mkfs.btrfs /dev/nvme0n1",
    ],
)
def test_blocks_filesystem_format(command):
    """mkfs destroys all data on the target device — blocked."""
    p = CommandPolicy.default()
    assert p.check_command(command) is not None


def test_blocks_dd_to_device():
    """`dd ... of=/dev/sda` writes raw bytes to a block device — blocked."""
    p = CommandPolicy.default()
    assert p.check_command("dd if=/dev/zero of=/dev/sda bs=1M") is not None


def test_blocks_redirect_to_device():
    """Writing directly to /dev/sd* bypasses the filesystem — blocked."""
    p = CommandPolicy.default()
    assert p.check_command("echo x > /dev/sda") is not None


@pytest.mark.parametrize(
    "command",
    [
        ":(){:|:&};:",  # canonical form
        ": () { : | : & } ;",  # spaced variant
    ],
)
def test_blocks_fork_bomb(command):
    """The bash fork bomb must be caught."""
    p = CommandPolicy.default()
    assert p.check_command(command) is not None


def test_blocks_chmod_777_root():
    """`chmod -R 777 /` makes the entire filesystem world-writable — blocked."""
    p = CommandPolicy.default()
    assert p.check_command("chmod -R 777 /") is not None


@pytest.mark.parametrize(
    "command",
    [
        "shutdown -h now",
        "reboot",
        "sudo halt",
        "poweroff",
    ],
)
def test_blocks_system_power_control(command):
    """No coding task needs shutdown/reboot — blocked."""
    p = CommandPolicy.default()
    assert p.check_command(command) is not None


# --------------------------------------------------------------- safe commands pass


@pytest.mark.parametrize(
    "command",
    [
        "pytest",
        "python -m pytest tests/",
        "ls -la",
        "echo hello",
        "git status",
        "git add .",
        "ruff check src",
        "cat README.md",
        "cd src && ls",
        "npm install",
        "pip install -e .",
        "",  # empty command passes (no-op)
    ],
)
def test_safe_commands_pass(command):
    """Normal development commands must not be blocked by the default policy."""
    p = CommandPolicy.default()
    assert p.check_command(command) is None, f"should not block: {command!r}"


# --------------------------------------------------------------- user blocklist


def test_user_extra_blocked_appended():
    """User-supplied patterns are ADDED to the built-in defaults (both apply)."""
    p = CommandPolicy(extra_blocked=[r"git\s+push\s+--force"])
    # User pattern blocks the custom command.
    assert p.check_command("git push --force origin main") is not None
    # Built-in defaults still active.
    assert p.check_command("rm -rf /") is not None
    # Normal git push (no --force) passes.
    assert p.check_command("git push origin main") is None


def test_invalid_user_regex_skipped():
    """A malformed regex in the user blocklist is skipped, not crashed."""
    p = CommandPolicy(extra_blocked=["[invalid("])  # unbalanced bracket
    # Must not raise — the bad pattern is dropped silently.
    assert p.check_command("ls") is None
    # Valid built-in patterns still work.
    assert p.check_command("rm -rf /") is not None


def test_user_pattern_reason_includes_pattern():
    """When a user pattern matches, the reason mentions which pattern hit."""
    p = CommandPolicy(extra_blocked=[r"npm\s+publish"])
    reason = p.check_command("npm publish")
    assert reason is not None
    assert "npm" in reason or "user blocklist" in reason.lower()


# --------------------------------------------------------------- network toggle


def test_network_allowed_default_true():
    p = CommandPolicy.default()
    assert p.network_allowed is True


def test_network_can_be_disabled():
    p = CommandPolicy(network_allowed=False)
    assert p.network_allowed is False
