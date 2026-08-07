"""Command-content review policy for the shell (execute) tool.

This is the cheapest layer of defense against destructive shell commands — it
catches the obvious catastrophe patterns (``rm -rf /``, ``mkfs``, fork bombs)
before they reach ``subprocess.run(shell=True)``. It is NOT a real OS sandbox:
a determined model can obfuscate commands (base64 decode, variable expansion,
``$(...)`` substitution) to bypass regex matching. The goal is to stop the
*accidental* and *careless* cases, not a adversarial one.

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


@dataclass
class CommandPolicy:
    """Decides whether a shell command or network call may proceed.

    Attributes:
        extra_blocked: user-supplied patterns (from config.toml
            [tools].blocked_commands). Appended to the built-in defaults, so
            both layers apply. Each entry is a regex string.
        network_allowed: if False, web_fetch and web_search are blocked
            entirely (offline mode). Defaults to True.
    """

    extra_blocked: list[str] = field(default_factory=list)
    network_allowed: bool = True
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
        """
        if not command:
            return None
        self._ensure_compiled()
        for pattern, reason in self._compiled:
            if pattern.search(command):
                return reason
        return None

    @classmethod
    def default(cls) -> CommandPolicy:
        """The policy used when config doesn't specify one: built-in blacklist
        active, network allowed. Safe default that adds protection without
        breaking any normal coding workflow."""
        return cls()
