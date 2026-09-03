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


# ------------------------------------------------- adversarial bypass vectors
# 2026-08-28 audit: 8 real bypass vectors against the OLD first-token-only rm
# check. All must be BLOCKED now (segment split + wrapper unwrap + PowerShell
# patterns). `python -c "shutil.rmtree(...)"` is deliberately NOT in this
# list — arbitrary-language code is a documented boundary (anti-footgun, not
# anti-adversary).
@pytest.mark.parametrize(
    "cmd",
    [
        'sh -c "rm -rf /"',
        'bash -c "rm -rf /"',
        "echo / | xargs rm -rf",
        "find / -exec rm -rf {} +",
        "find / -name '*.log' -execdir rm -rf {} +",
        "Remove-Item -Recurse -Force C:\\",
        "Remove-Item -recurse *",
        r'powershell -Command "Remove-Item -Recurse -Force C:\Users"',
        "rd /s /q C:\\",
        "echo hi\nrm -rf /",
        "cd /tmp\nrm -rf /",
        "echo hi\r\nrm -rf /",
    ],
)
def test_adversarial_bypass_vectors_blocked(cmd):
    reason = CommandPolicy.default().check_command(cmd)
    assert reason is not None, f"bypass vector NOT blocked: {cmd!r}"


def test_safe_pipelines_still_pass():
    """The segment split must not break legitimate pipelines."""
    assert CommandPolicy.default().check_command("cat log.txt | grep ERROR | head -5") is None
    assert CommandPolicy.default().check_command("ls -la && echo done") is None
    assert CommandPolicy.default().check_command("pytest -q; echo finished") is None
    assert CommandPolicy.default().check_command("git log | head") is None


def test_wrapper_recursion_depth():
    """Nested wrappers unwrap fully: sh -c bash -c 'rm -rf /' is blocked."""
    assert CommandPolicy.default().check_command("sh -c \"bash -c 'rm -rf /'\"") is not None


# ------------------------------------------------- same-family footguns (P1-1, 2026-09-02)


@pytest.mark.parametrize(
    "command",
    [
        # path-qualified / sudo-wrapped shells are the same wrapper as bare sh
        '/bin/sh -c "rm -rf /"',
        'sudo sh -c "rm -rf /"',
        'env LC=C /bin/bash -c "rm -rf /"',
        # Windows accepts these spellings and is case-insensitive
        "rm.exe -rf /",
        "RM -rf /",
        "/bin/rm -rf ~",
    ],
)
def test_blocks_rm_spelling_variants(command):
    """Audit 2026-09-02: `rm.exe`, `RM`, and `/bin/rm` are the same command —
    the exact-name comparison let them past _check_recursive_rm."""
    p = CommandPolicy.default()
    assert p.check_command(command) is not None, f"should block: {command!r}"


@pytest.mark.parametrize(
    "command",
    [
        "echo / | xargs -0 rm -rf",  # xargs flag between xargs and rm
        "echo / | xargs rm --recursive",  # long-form recursive flag
        "echo / | xargs rm -rf",  # the classic shape must stay blocked
    ],
)
def test_blocks_xargs_into_recursive_rm(command):
    """Audit 2026-09-02: the old regex only matched the adjacent
    `xargs rm -rf` shape — `xargs -0 rm` and `xargs rm --recursive` leaked."""
    p = CommandPolicy.default()
    assert p.check_command(command) is not None, f"should block: {command!r}"


@pytest.mark.parametrize(
    "command",
    [
        "find /etc -delete",
        "find ~ -delete",
        "find $HOME -name x -delete",
        "find * -delete",
        "find / -exec rm -rf {} +",
        "find ~ -exec rm {} +",
    ],
)
def test_blocks_find_deletion_at_dangerous_root(command):
    """Audit 2026-09-02: the old find patterns anchored only at bare `/`, so
    the same-family `find /etc -delete` leaked while `rm -rf /etc` was
    blocked — a double standard at equal danger. Relative starts stay legal:
    `find . -delete` / `find foo -delete` mirror the rm relative-target rule."""
    p = CommandPolicy.default()
    assert p.check_command(command) is not None, f"should block: {command!r}"


@pytest.mark.parametrize(
    "command",
    [
        "find . -name '*.tmp' -delete",
        "find foo -delete",
        "find . -name '*.pyc' -exec rm {} +",
        "ls | xargs rm",  # non-recursive rm through xargs stays legal
        "cat list.txt | xargs rm -f",  # -f alone is not recursive
        "echo x | xargs rm -v",
        "sh -c 'echo hello'",
        "sudo apt install curl",
    ],
)
def test_safe_find_xargs_variants_pass(command):
    """The tightening must not block legitimate workdir-scoped cleanup."""
    p = CommandPolicy.default()
    assert p.check_command(command) is None, f"should pass: {command!r}"


# ------------------------------------------------- wrapper flag dimension (P1-1, 2026-09-03)


@pytest.mark.parametrize(
    "command",
    [
        "sudo -u root rm -rf /",  # sudo's own flag + value before the command
        "env -i rm -rf /",  # env flag without a value
        "doas rm -rf /",  # doas prefix
        "sudo -- rm -rf /",  # bare -- separator
        "sudo -C 5 rm -rf /",  # short flag with a value (-C 5)
        "env -u X rm -rf ~",  # env -u takes a value
        r"del /f /s /q C:\Windows",  # cmd del /s is recursive delete too
        r"erase /s /q C:\Windows",
        r"rd /s /q C:\x",
        'bash -c -l "rm -rf /"',  # -l AFTER -c is a legal bash ($0)
    ],
)
def test_blocks_wrapper_flag_dimension(command):
    """Audit 2026-09-03: the 09-02 fix handled the shell-wrapper dimension but
    leaked the wrapper-FLAG dimension — sudo/env/doas flags between the prefix
    and the command, cmd del/erase /s. All are the same footgun class."""
    p = CommandPolicy.default()
    assert p.check_command(command) is not None, f"should block: {command!r}"


@pytest.mark.parametrize(
    "command",
    [
        "sudo apt install curl",
        "env LC=C python x.py",
        "doas make install",
        "sudo --version",
        "env | grep PATH",
        "del /f notes.txt",  # non-recursive single-file delete stays legal
        "sudo -u www-data ls /var/www",
        "bash -c 'echo ok'",
    ],
)
def test_safe_prefixed_commands_pass(command):
    """The prefix stepper must not block legitimate prefixed commands."""
    p = CommandPolicy.default()
    assert p.check_command(command) is None, f"should pass: {command!r}"
