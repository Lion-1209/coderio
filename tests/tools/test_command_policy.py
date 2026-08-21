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


# ----------------------------------------------------- whitelist mode (P0-1, stage B)


def test_whitelist_disabled_by_default():
    """Default policy: whitelist_mode=False → check_whitelist always returns None."""
    p = CommandPolicy()
    assert p.whitelist_mode is False
    # Even an unknown command passes (whitelist is off).
    assert p.check_whitelist("totally-bogus-command --flag") is None


def test_whitelist_blocks_unknown_command():
    """whitelist_mode=True: unknown command flagged (returns reason string)."""
    p = CommandPolicy(whitelist_mode=True)
    result = p.check_whitelist("totally-bogus-command")
    assert result is not None
    assert "totally-bogus-command" in result
    assert "whitelist" in result.lower()


def test_whitelist_allows_known_command():
    """whitelist_mode=True: built-in whitelist commands pass (return None)."""
    p = CommandPolicy(whitelist_mode=True)
    for cmd in ("python script.py", "git status", "npm install", "pytest -x", "ls -la"):
        assert p.check_whitelist(cmd) is None, f"{cmd!r} should be whitelisted"


def test_whitelist_user_allowed_commands():
    """allowed_commands extends the built-in whitelist."""
    p = CommandPolicy(whitelist_mode=True, allowed_commands=["docker", "kubectl"])
    assert p.check_whitelist("docker ps") is None
    assert p.check_whitelist("kubectl get pods") is None
    # Non-allowed still flagged.
    assert p.check_whitelist("terraform apply") is not None


def test_whitelist_extracts_first_token_only():
    """The whitelist checks only the first token (the executable name), not args."""
    from coderio.tools.command_policy import _extract_command_name

    assert _extract_command_name("python script.py --verbose") == "python"
    assert _extract_command_name("git commit -m 'msg'") == "git"
    assert _extract_command_name("ls -la /tmp") == "ls"


def test_whitelist_strips_path_prefix():
    """Path-qualified commands match by bare name: /usr/bin/python → python."""
    from coderio.tools.command_policy import _extract_command_name

    assert _extract_command_name("/usr/bin/python script.py") == "python"
    assert _extract_command_name("./my-tool --flag") == "my-tool"
    assert _extract_command_name("C:\\tools\\node.exe app.js") == "node.exe"


def test_whitelist_skips_source_prefix():
    """``source venv/bin/activate && python ...`` → first real command is python."""
    from coderio.tools.command_policy import _extract_command_name

    name = _extract_command_name("source venv/bin/activate && python script.py")
    assert name == "python"


def test_whitelist_skips_env_prefix():
    """``env VAR=x python ...`` → first real command is python."""
    from coderio.tools.command_policy import _extract_command_name

    name = _extract_command_name("env DJANGO_SETTINGS=x python manage.py runserver")
    assert name == "python"


def test_whitelist_skips_sudo_prefix():
    """``sudo apt install ...`` → first real command is apt."""
    from coderio.tools.command_policy import _extract_command_name

    name = _extract_command_name("sudo apt install -y curl")
    assert name == "apt"


def test_whitelist_empty_command_returns_none():
    """An empty command string returns None (nothing to check)."""
    p = CommandPolicy(whitelist_mode=True)
    assert p.check_whitelist("") is None
    assert p.check_whitelist("   ") is None


def test_whitelist_blacklist_both_active():
    """Blacklist (hard block) takes priority over whitelist (soft flag).

    Even in whitelist mode, a blacklist-matching command returns the blacklist
    reason from check_command (hard block), not the whitelist miss."""
    p = CommandPolicy(whitelist_mode=True)
    # 'rm' isn't in the whitelist, AND 'rm -rf /' matches the blacklist.
    # check_command (blacklist) should return the hard-block reason.
    blocked = p.check_command("rm -rf /")
    assert blocked is not None
    assert "recursive delete" in blocked.lower() or "root" in blocked.lower()
    # check_whitelist would also flag it (rm not in whitelist), but that's a
    # separate layer — the caller checks check_command first.

    # A whitelist miss on a NON-blacklisted command returns the whitelist reason.
    assert p.check_command("totally-bogus-command") is None  # not blacklisted
    assert p.check_whitelist("totally-bogus-command") is not None  # whitelist miss


def test_whitelist_default_allowed_has_dev_tools():
    """The built-in whitelist includes the core dev tooling a coding agent needs."""
    from coderio.tools.command_policy import _DEFAULT_ALLOWED

    for must_have in ("python", "git", "npm", "pytest", "ruff", "ls", "cat", "grep"):
        assert must_have in _DEFAULT_ALLOWED, f"{must_have!r} should be in the default whitelist"


def test_whitelist_destructive_commands_not_in_defaults():
    """The built-in whitelist does NOT include destructive commands (rm, dd, mkfs).

    Even in whitelist mode, these should prompt — they're never auto-allowed."""
    from coderio.tools.command_policy import _DEFAULT_ALLOWED

    for must_block in ("rm", "rmdir", "dd", "mkfs", "shutdown"):
        assert must_block not in _DEFAULT_ALLOWED, f"{must_block!r} must NOT be in the default whitelist (destructive)"


# ----------------------------------------------------- blacklist hardening (P0-5)
# REGRESSION (2026-08-14 report): the original single-flag regex caught the
# HARMLESS bare `rm -rf /` (coreutils refuses it without --no-preserve-root)
# while missing every ACTUALLY-destructive variant. These tests pin all the
# forms the report verified as bypasses.


def test_blacklist_no_preserve_root():
    """--no-preserve-root is the ONLY way `rm -rf /` actually deletes root —
    it must be blocked wherever it appears after rm."""
    p = CommandPolicy()
    for cmd in ("rm -rf / --no-preserve-root", "rm -rf --no-preserve-root /"):
        assert p.check_command(cmd) is not None, f"must block: {cmd!r}"


def test_blacklist_split_flags():
    """Flags written separately (rm -r -f /) must be blocked, not just rm -rf."""
    p = CommandPolicy()
    assert p.check_command("rm -r -f /") is not None
    assert p.check_command("rm -r -f /etc") is not None


def test_blacklist_quoted_root():
    """Quoted root (rm -rf "/") must be blocked."""
    p = CommandPolicy()
    assert p.check_command('rm -rf "/"') is not None
    assert p.check_command("rm -rf '/'") is not None


def test_blacklist_chmod_leading_zero():
    """chmod -R 0777 / (leading-zero mode) must be blocked like chmod -R 777 /."""
    p = CommandPolicy()
    assert p.check_command("chmod -R 0777 /") is not None
    assert p.check_command("chmod -R 777 /") is not None


def test_blacklist_find_root_delete():
    """find starting at / with -delete wipes every match under root."""
    p = CommandPolicy()
    assert p.check_command("find / -delete") is not None
    assert p.check_command("find / -name x -delete") is not None
    assert p.check_command("find / -type f -delete") is not None
    # Relative find -delete is a legitimate (if aggressive) workspace op.
    assert p.check_command("find ./src -delete") is None


def test_blacklist_hardening_no_false_positives():
    """The hardened patterns must not block normal workspace commands."""
    p = CommandPolicy()
    for cmd in ("rm -rf ./build", "rm -rf build", "chmod -R 755 ./deploy", "find . -name test", "echo find /"):
        assert p.check_command(cmd) is None, f"false positive on {cmd!r}"


# ----------------------------------------------------- long-form flag variants (2026-08-21 audit P2-3)


def test_blacklist_long_flags_rm_force_recursive():
    """Long-form rm flags (--recursive, --force) must be blocked when combined
    with deleting root or home."""
    p = CommandPolicy()
    assert p.check_command("rm --recursive --force /") is not None
    assert p.check_command("rm --force --recursive /") is not None
    assert p.check_command("rm --recursive -f /") is not None
    assert p.check_command("rm -rf --recursive --force /") is not None
    # Case-insensitive: --RECURSIVE, --Recursive, etc.
    assert p.check_command("rm --RECURSIVE /") is not None
    assert p.check_command("rm --Recursive --force /") is not None
    # Home directory.
    assert p.check_command("rm --recursive --force ~/") is not None
    assert p.check_command("rm --recursive --force $HOME") is not None


def test_blacklist_long_flags_glob_star():
    """rm --recursive * must be blocked (destroys everything in cwd)."""
    p = CommandPolicy()
    assert p.check_command("rm --recursive --force *") is not None
    assert p.check_command("rm -rf --force *") is not None


def test_blacklist_chmod_mode_before_flag():
    """chmod 777 -R / (mode before flag) must be blocked."""
    p = CommandPolicy()
    assert p.check_command("chmod 777 -R /") is not None
    assert p.check_command("chmod 0777 -R /") is not None
    assert p.check_command("chmod 777 -R /home") is not None
    # Normal chmod should not be blocked.
    assert p.check_command("chmod -R 755 ./deploy") is None
