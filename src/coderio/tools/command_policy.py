"""Command-content review policy for the shell (execute) tool.

This is the cheapest layer of defense against destructive shell commands — it
catches the obvious catastrophe patterns (``rm -rf /``, ``mkfs``, fork bombs)
before they reach ``subprocess.run(shell=True)``. It is NOT a real OS sandbox:
a determined model can obfuscate commands (base64 decode, variable expansion,
``$(...)`` substitution) to bypass regex matching. The goal is to stop the
*accidental* and *careless* cases, not a adversarial one.

Two complementary modes (both can be active at once):

**Blacklist (always on, even in FULL mode)**: built-in patterns that block the
catastrophic commands. Users can append via ``[tools].blocked_commands``. A
blacklist match → hard block (the command never runs).

**Whitelist (opt-in via ``[tools].whitelist_mode = true``)**: when enabled,
commands whose first token isn't in the allowed set are flagged for confirmation
(NOT hard-blocked — they degrade to the tier's confirm path, so FULL still
allows them, CONFIRM prompts, PLAN blocks). This is stricter than the blacklist
alone but less disruptive than hard-denying unknown commands. Users extend the
allowlist via ``[tools].allowed_commands``.

The whitelist, like the blacklist, is name-matching only — ``python -c
"import os; os.system('rm -rf /')"`` passes because the first token is
``python``. It raises the bar for accidental damage (a typo'd command name, a
wrong tool), not for adversarial input. True isolation needs OS-level sandboxing
(see win_sandbox.py / linux_sandbox.py).

For true isolation, run coderio inside a container/VM — see the architecture
doc's security section. This policy is the "at least do this" floor that the
2026-08-07 analysis report (P0-1) asked for.

Design:
- Default blacklist is built-in and always active (even in FULL mode). Users
  can append their own patterns via config.toml [tools].blocked_commands.
- Matching is regex on the raw command string (no shell AST parsing). This
  will false-positive on ``echo "rm -rf /"`` — acceptable, since safety > the
  rare case of quoting a destructive pattern in a string literal.
- ``network_allowed=False`` disables web_fetch/web_search entirely (offline mode).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Built-in blacklist: (pattern, reason). Compiled once at import.
#
# Each pattern matches the destructive form but NOT safe variants:
# - `rm -rf /` blocks recursive root deletion, but `rm -rf ./build` is fine
# - `rm -rf ~` / `rm -rf $HOME` blocks home-dir nuking
# - `mkfs` blocks filesystem formatting
# - `dd ... of=/dev/` blocks writing to raw devices
# - fork bomb `:(){:|:&};:` (and the spaced variant)
# - `chmod -R 777 /` blocks global permission corruption
# - `> /dev/sda` blocks redirect-to-device
# - `shutdown` / `reboot` / `halt` / `poweroff` block system power control
# - `:(){` is the canonical fork-bomb opener (catches variants before the full form)
_DEFAULT_BLOCKED: list[tuple[str, str]] = [
    # Recursive deletion of root or any system directory (/home, /etc, /usr, ...).
    # `rm -rf /home` is just as catastrophic as `rm -rf /`. Any absolute path
    # under root with -rf is blocked; `rm -rf ./build` (relative) is fine.
    (r"\brm\s+-[rRfF]*[rR][fF]*\s+/(?:\S|$)", "recursive delete of system directory (absolute path under /)"),
    # Also catch bare `rm -rf /` with nothing after (trailing space or EOL):
    # the \S branch above needs a non-space char, so handle the bare-root case.
    (r"\brm\s+-[rRfF]*[rR][fF]*\s+/\s*$", "recursive delete of root directory"),
    (r"\brm\s+-[rRfF]*[rR][fF]*\s+~(?:\s|$|/)", "recursive delete of home directory"),
    (r"\brm\s+-[rRfF]*[rR][fF]*\s+\$HOME(?:\s|$|/)", "recursive delete of home directory"),
    (r"\brm\s+-[rRfF]*[rR][fF]*\s+\*", "recursive delete of all files (rm -rf *)"),
    # Filesystem format — destroys all data on a device.
    (r"\bmkfs(?:\.\w+)?\s", "filesystem format (destroys all data on target)"),
    # Writing to raw block devices.
    (r"\bdd\b.*\bof\s*=\s*/dev/", "dd writing to a raw device"),
    (r">\s*/dev/(?:sd|nvme|hd)", "redirect to a raw block device"),
    # Fork bomb — the bash classic `:(){:|:&};:` and spaced variants.
    # Allow optional space between `:` and `()` (some shells/users space it).
    (r":\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;", "fork bomb"),
    # Global permission corruption.
    (r"\bchmod\s+-R\s+777\s+/(?:\S|$)", "recursive world-writable on system directory"),
    # System power control — no coding task needs these.
    (r"\b(?:shutdown|reboot|halt|poweroff)\b", "system power control"),
    # Kernel module manipulation — loading/unloading kernel code.
    (r"\brmmod\b", "kernel module unload"),
    (r"\binsmod\b", "kernel module load"),
    (r"\bmodprobe\b", "kernel module manipulation"),
]

# Built-in whitelist: commands a coding agent legitimately needs. When
# whitelist_mode is enabled, any command whose first token isn't here (or in
# the user's allowed_commands) is flagged for confirmation.
#
# Keep this conservative — adding a command here means "the agent can run this
# without asking, even in whitelist mode". Destructive commands (rm, dd, mkfs)
# are intentionally absent: even in whitelist mode, they should prompt.
_DEFAULT_ALLOWED = frozenset(
    {
        # Language runtimes & package managers
        "python",
        "python3",
        "py",
        "pip",
        "pip3",
        "pipx",
        "uv",
        "poetry",
        "node",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "bun",
        "deno",
        "cargo",
        "rustc",
        "go",
        "java",
        "javac",
        "gradle",
        "mvn",
        "ruby",
        "gem",
        "bundle",
        # Build / task runners
        "make",
        "cmake",
        "ninja",
        "just",
        # Dev tools
        "git",
        "gh",
        "ruff",
        "mypy",
        "pyright",
        "black",
        "isort",
        "pytest",
        "tox",
        "nox",
        "jest",
        "vitest",
        "test",
        "eslint",
        "tsc",
        # Read-only inspection (safe, no side effects)
        "ls",
        "ll",
        "dir",
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "grep",
        "rg",
        "find",
        "fd",
        "which",
        "where",
        "whereis",
        "wc",
        "sort",
        "uniq",
        "diff",
        "comm",
        "cut",
        "tr",
        "awk",
        "sed",
        "stat",
        "file",
        "du",
        "df",
        "env",
        "printenv",
        "echo",
        "printf",
        "date",
        "uname",
        "whoami",
        "hostname",
        # File ops (non-destructive)
        "mkdir",
        "touch",
        "cp",
        "mv",
        "ln",
        "chmod",
        "chown",
        "tree",
        "exa",
        "bat",
        # Shell builtins / misc
        "cd",
        "pwd",
        "export",
        "set",
        "source",
        "eval",
        "true",
        "false",
        "test",
        # Compression (common in build flows)
        "tar",
        "zip",
        "unzip",
        "gzip",
        "gunzip",
    }
)


def _extract_command_name(command: str) -> str:
    """Extract the first token of a command (the executable name).

    Handles a few common prefixes that wrap the real command:
    - ``source venv/bin/activate && python ...`` → ``python``
    - ``sudo apt install ...`` → ``apt``
    - ``env VAR=x python ...`` → ``python``

    Returns the bare name (no path prefix): ``/usr/bin/python`` → ``python``.
    Returns "" for empty/whitespace input.
    """
    if not command or not command.strip():
        return ""
    # Strip leading env-var assignments (``VAR=x foo``) and common wrappers.
    tokens = command.strip().split()
    i = 0
    # Skip ``env`` + its VAR=val args.
    if tokens and tokens[0] in ("env", "sudo", "command"):
        i = 1
        while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
            i += 1
    # Skip ``source x && ...`` / ``. x && ...`` prefix.
    if i < len(tokens) and tokens[i] in ("source", "."):
        # Skip the script arg, then any && / ; separator.
        i += 2
        while i < len(tokens) and tokens[i] in ("&&", "||", ";", "&", "|"):
            i += 1
    if i >= len(tokens):
        return ""
    name = tokens[i]
    # Strip path prefix: /usr/bin/python → python, ./foo → foo.
    if "/" in name or "\\" in name:
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return name


@dataclass
class CommandPolicy:
    """Decides whether a shell command or network call may proceed.

    Attributes:
        extra_blocked: user-supplied patterns (from config.toml
            [tools].blocked_commands). Appended to the built-in defaults, so
            both layers apply. Each entry is a regex string.
        network_allowed: if False, web_fetch and web_search are blocked
            entirely (offline mode). Defaults to True.
        whitelist_mode: if True, commands whose first token isn't in the
            allowed set are flagged for confirmation (see check_whitelist).
            Defaults to False (blacklist-only, backward compatible).
        allowed_commands: user-supplied additions to the built-in whitelist
            (from config.toml [tools].allowed_commands). Applied only when
            whitelist_mode is True.
    """

    extra_blocked: list[str] = field(default_factory=list)
    network_allowed: bool = True
    whitelist_mode: bool = False
    allowed_commands: list[str] = field(default_factory=list)
    # Compiled patterns, lazily built. Cached on first check_command call.
    _compiled: list[tuple[re.Pattern, str]] = field(default_factory=list, repr=False)
    _initialized: bool = field(default=False, repr=False)

    def _ensure_compiled(self) -> None:
        """Compile regexes once, on first use (avoids import-time work if the
        policy is constructed but never checked — e.g. in tests)."""
        if self._initialized:
            return
        for pattern, reason in _DEFAULT_BLOCKED:
            self._compiled.append((re.compile(pattern), reason))
        for user_pattern in self.extra_blocked:
            # User patterns get a generic reason; we don't know their intent.
            try:
                self._compiled.append((re.compile(user_pattern), f"matches user blocklist: {user_pattern}"))
            except re.error:
                # Skip invalid regex rather than crashing — log via the reason
                # so a future check could surface it. Silently dropping is
                # safer than blocking all commands because one pattern is bad.
                continue
        self._initialized = True

    def check_command(self, command: str) -> str | None:
        """Check a shell command against the blacklist.

        Returns a human-readable reason string if the command is blocked, or
        None if it may proceed. The reason is surfaced to the model via a
        ToolMessage so it can understand WHY and try a different approach.

        The blacklist is ALWAYS active (even in FULL mode) — safety takes
        priority. The whitelist (check_whitelist) is a separate, softer layer.
        """
        if not command:
            return None
        self._ensure_compiled()
        for pattern, reason in self._compiled:
            if pattern.search(command):
                return reason
        return None

    def check_whitelist(self, command: str) -> str | None:
        """Check if a command is outside the whitelist (whitelist mode only).

        Returns a reason string if the command's first token is NOT in the
        allowed set (the command should be flagged for confirmation), or None
        if the command is whitelisted (or whitelist_mode is disabled).

        Unlike check_command (hard block), a whitelist miss is NOT a hard
        block — the caller (CommandReviewMiddleware) degrades it to the tier's
        confirm path. This means FULL mode still allows whitelist-miss
        commands, PLAN mode still blocks them (PLAN blocks all shell anyway),
        and CONFIRM/AUTO_EDIT prompt the user.
        """
        if not self.whitelist_mode or not command:
            return None
        name = _extract_command_name(command)
        if not name:
            return None  # empty after extraction — let it through, blacklist will catch real issues
        allowed = _DEFAULT_ALLOWED | frozenset(self.allowed_commands)
        if name not in allowed:
            return (
                f"command {name!r} is not in the whitelist "
                f"(whitelist_mode=true). This is not a hard block — the "
                f"permission tier decides whether to confirm (CONFIRM/AUTO_EDIT) "
                f"or allow (FULL). Add {name!r} to [tools].allowed_commands to "
                f"silence this."
            )
        return None

    @classmethod
    def default(cls) -> CommandPolicy:
        """The policy used when config doesn't specify one: built-in blacklist
        active, network allowed. Safe default that adds protection without
        breaking any normal coding workflow."""
        return cls()
