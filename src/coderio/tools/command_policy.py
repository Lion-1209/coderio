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


def _check_recursive_rm(command: str) -> str | None:
    """Python-level check for dangerous rm commands with recursive flags.

    Handles flag permutations that regex can't easily express:
    - rm -fr /, rm -rf /, rm -r -f / (short flags in any order)
    - rm --recursive --force /, rm --force --recursive / (long flags, any order)
    - rm --Force / → NOT blocked (--Force is not --recursive)
    - rm -f /etc/passwd → NOT blocked (no -r/-R recursive indicator)

    Only triggers when ``rm`` is the actual command name (not inside echo/cat).
    Returns a reason string if blocked, or None if safe.
    """
    # Extract the command name — only block when rm is actually executed.
    name = _extract_command_name(command)
    if name != "rm":
        return None

    tokens = command.strip().split()
    # Walk past env/sudo/source wrappers to find rm's position.
    idx = 0
    if tokens and tokens[0] in ("env", "sudo", "command"):
        idx = 1
        while idx < len(tokens) and "=" in tokens[idx] and not tokens[idx].startswith("-"):
            idx += 1
    if idx < len(tokens) and tokens[idx] in ("source", "."):
        idx += 2
        while idx < len(tokens) and tokens[idx] in ("&&", "||", ";", "&", "|"):
            idx += 1
    if idx >= len(tokens) or tokens[idx] != "rm":
        return None

    rm_tokens = tokens[idx + 1 :]
    if not rm_tokens:
        return None

    has_recursive = False
    for t in rm_tokens:
        if t.startswith("--"):
            # --recursive (any case) is the recursive indicator.
            # --Force / --force are NOT recursive indicators.
            if re.match(r"^--recursive$", t, re.IGNORECASE):
                has_recursive = True
        elif t.startswith("-") and len(t) > 1 and not t.startswith("--"):
            # Short flag group: must contain r or R to be recursive.
            # -f alone → not recursive. -fr, -rf, -Rf → recursive.
            if "r" in t[1:] or "R" in t[1:]:
                has_recursive = True

    if not has_recursive:
        return None

    # Check for dangerous targets among rm's arguments.
    has_dangerous = False
    for t in rm_tokens:
        stripped = t.strip("\"'")
        if stripped == "/" or stripped.startswith("/"):
            has_dangerous = True
            break
        if stripped == "~" or stripped.startswith("~"):
            has_dangerous = True
            break
        if stripped == "$HOME" or stripped.startswith("$HOME"):
            has_dangerous = True
            break
        if stripped == "*" or stripped.startswith("*"):
            has_dangerous = True
            break

    if not has_dangerous:
        return None

    if any(t.strip("\"'") in ("/", "$HOME", "~") or t.strip("\"'").startswith(("/", "$HOME")) for t in rm_tokens):
        return "recursive delete of system/home directory"
    if "*" in [t.strip("\"'") for t in rm_tokens]:
        return "recursive delete of all files (glob)"
    return "recursive delete of dangerous target"


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
#
# NOTE: rm-specific patterns (rm -rf /, rm -rf ~, etc.) are handled by
# _check_recursive_rm() _before_ the regex loop (see check_command). The
# regex below only covers the non-recursive `--no-preserve-root` case and
# all non-rm destructive patterns.

_DEFAULT_BLOCKED: list[tuple[str, str]] = [
    # Recursive rm detection is handled by _check_recursive_rm() (Python-level).
    # The patterns below catch rm --no-preserve-root (defeats coreutils root
    # protection) which should always be blocked regardless of -r/-R flags.
    (r"\brm\b[^|;&]*--no-preserve-root", "rm with --no-preserve-root (removes root-delete protection)"),
    # find starting at / with -delete: deletes every match under root.
    # `find / -name x -delete` etc. — any find rooted at / ending in -delete.
    (r"\bfind\s+/(?:\s|$).*?-delete\b", "find -delete starting at filesystem root"),
    # find rooted at / whose -exec/-execdir runs rm — the audit's bypass
    # vector: `find / -exec rm -rf {} +` deletes everything without -delete.
    (r"\bfind\s+/(?:\s|$).*?-exec(?:dir)?\b.*\brm\b", "find -exec rm starting at filesystem root"),
    # xargs piping into recursive rm: `echo / | xargs rm -rf`.
    (r"\bxargs\s+(?:sudo\s+)?rm\s+-[a-zA-Z]*[rR]", "xargs piping into recursive rm"),
    # Filesystem format — destroys all data on a device.
    (r"\bmkfs(?:\.\w+)?\s", "filesystem format (destroys all data on target)"),
    # Writing to raw block devices.
    (r"\bdd\b.*\bof\s*=\s*/dev/", "dd writing to a raw device"),
    (r">\s*/dev/(?:sd|nvme|hd)", "redirect to a raw block device"),
    # Fork bomb — the bash classic `:(){:|:&};:` and spaced variants.
    # Allow optional space between `:` and `()` (some shells/users space it).
    (r":\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;", "fork bomb"),
    # Global permission corruption. Accept 777 with or without leading 0
    # (chmod accepts both `777` and `0777`; the old pattern missed 0777).
    # Also catches mode-before-flag order: chmod 777 -R / (same destruction).
    (r"\bchmod\s+-R\s+0?777\s+/(?:\S|$)", "recursive world-writable on system directory"),
    (r"\bchmod\s+0?777\s+-R\s+/(?:\S|$)", "recursive world-writable on system directory (mode-before-flag)"),
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


# Shell wrappers whose quoted argument is itself a shell command. When a
# segment starts with one of these, the quoted body is unwrapped and
# re-checked recursively — `sh -c "rm -rf /"` is an rm, not a sh.
# Shell separators that start a NEW command. Quoted separators are not
# honored by this naive split — over-splitting only makes us check shorter
# fragments, which fails SAFE (may over-block a fragment, never under-block).
_SEGMENT_SPLIT_RE = re.compile("&&|\\|\\||[;|&]|\r\n|\n")


# Shells whose "-c/--command" (or cmd's /c) argument is itself a command to
# re-check. Token-based parsing handles merged short flags (bash -lc) and
# cmd's slash-style flags — neither survives a regex approach cleanly
# (2026-08-28 adversarial review findings #4/#5).
_WRAPPER_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "powershell", "pwsh", "cmd"}


def _unwrap_wrapper(seg: str) -> str | None:
    """If ``seg`` is a shell wrapper (``bash -lc "..."``, ``cmd /c ...``),
    return everything after the -c/--command//c flag; else None."""
    tokens = seg.split()
    if not tokens:
        return None
    shell = tokens[0].lower().strip("\"'") if len(tokens[0]) > 1 else tokens[0]
    if shell not in _WRAPPER_SHELLS:
        return None
    for j in range(1, len(tokens)):
        t = tokens[j].lower()
        if shell == "cmd":
            if t in ("/c", "/k"):
                inner = " ".join(tokens[j + 1 :]).strip().strip("\"'")
                return inner or None
        else:
            # -c / --command / merged short flags ending in c (bash -lc)
            if t in ("-c", "--command") or (t.startswith("-") and t.endswith("c") and len(t) > 2 and "w" not in t):
                # strip the wrapping quotes: '"rm -rf /"' must become a
                # command whose first token _check_recursive_rm recognizes
                inner = " ".join(tokens[j + 1 :]).strip().strip("\"'")
                return inner or None
    return None


def _iter_shell_segments(command: str) -> list[str]:
    """Split a command into segments at shell separators, unwrapping one
    level of ``sh -c "..."``-style wrappers recursively.

    - ``cmd1 && cmd2 | cmd3`` → [cmd1, cmd2, cmd3]
    - ``sh -c "rm -rf /"``   → the inner ``rm -rf /`` (plus the sh wrapper)

    Over-splitting inside quotes fails SAFE: a fragment is still checked
    against every pattern. KNOWN LIMIT: quoted fragments are split too, so
    a command like sh -c "'rm' -rf /" gets its quotes stripped and the
    inner command's first token may still carry a stray quote character
    (token de-quoting is best-effort) — this check is anti-footgun, not
    anti-obfuscation.
    """
    segments: list[str] = []

    def _walk(cmd: str) -> None:
        for raw in _SEGMENT_SPLIT_RE.split(cmd):
            seg = raw.strip()
            if not seg:
                continue
            inner = _unwrap_wrapper(seg)
            if inner is not None:
                _walk(inner)
                continue
            segments.append(seg)

    _walk(command)
    return segments


def _check_recursive_windows_delete(command: str) -> str | None:
    """Windows counterparts of _check_recursive_rm (2026-08-28 audit: the
    blacklist had no PowerShell/cmd destructive patterns).

    Catches:
    - ``Remove-Item -Recurse ...`` aimed at dangerous targets (case-insensitive)
    - ``rd /s ...`` / ``rmdir /s ...`` (cmd's recursive delete)

    The dangerous-target set mirrors the rm check: drive roots, ~, globs.
    """
    tokens = command.split()
    # Strip quotes per token: inside a -Command wrapper the whole payload is
    # one quoted string, so tokens arrive like '"remove-item' — the quote
    # must not hide the cmdlet from detection.
    low_tokens = [t.lower().strip("\"'") for t in tokens]

    # Remove-Item -Recurse (flag may appear in any position, case-insensitive;
    # -Force is not required for the destructive part). Aliases rm/del/erase
    # and the flag prefix -r/-rec also bind to -Recurse in PowerShell.
    delete_aliases = {"remove-item", "ri", "rm", "del", "erase"}
    if any(t in delete_aliases for t in low_tokens):
        has_recurse = any(t.startswith("-") and t.lstrip("-").startswith("r") for t in low_tokens)
        if has_recurse:
            args = [t for t, lt in zip(tokens, low_tokens) if not lt.startswith("-")]
            dangerous = any(
                a.lower().rstrip("\\/") in ("c:", "c:\\", "\\\\", "~", "*")
                or a.lower().startswith(("c:\\", "c:/", "~", "*"))
                or a == "*"
                for a in args
            )
            if dangerous:
                return "PowerShell recursive delete of system/home directory (Remove-Item -Recurse)"

    # cmd builtins: rd /s, rmdir /s — recursive directory removal.
    # Dangerous-target filter MIRRORS the rm/Remove-Item checks (audit finding
    # #7: an unfiltered rd /s hard-blocked legitimate build cleanup like
    # `rd /s /q .\build`). Relative targets pass; roots/home block.
    if low_tokens and low_tokens[0] in ("rd", "rmdir"):
        if "/s" in low_tokens:
            args = [t for t, lt in zip(tokens, low_tokens) if not lt.startswith("/") and not lt.startswith("-")]
            dangerous = any(
                a.lower().rstrip("\\/") in ("c:", "~") or a.lower().startswith(("c:\\", "\\\\")) or a in ("/", "\\")
                for a in args
            )
            if dangerous or not args:
                return "cmd recursive directory delete (rd /s)"

    return None


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
        # PASS 1 — whole-string regex: some patterns (fork bomb) span shell
        # separators and would be destroyed by segment splitting. Never split
        # before this pass.
        for pattern, reason in self._compiled:
            if pattern.search(command):
                return reason
        # PASS 2 — per-segment command-name checks (rm / PowerShell delete):
        # a separator starts a NEW command whose first token is meaningful
        # again (`echo / | xargs rm -rf` has xargs as a segment head — the
        # whole-string check never saw the rm as a command name).
        for segment in _iter_shell_segments(command):
            py_reason = _check_recursive_rm(segment)
            if py_reason is not None:
                return py_reason
            win_reason = _check_recursive_windows_delete(segment)
            if win_reason is not None:
                return win_reason
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
