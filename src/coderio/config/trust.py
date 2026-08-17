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

Non-interactive contexts (tests, CI) use :func:`is_repo_trusted` /
:func:`mark_repo_trusted` directly, or pass ``assume_trusted=True``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

# Files that constitute "repo-level config" and therefore require trust.
# A repo with none of these starts silently (nothing to confirm).
REPO_CONFIG_FILES = (".coderio/config.toml", ".mcp.json")

# Safety-relevant keys called out in the confirmation summary — the user should
# see these even if they don't read the full file.
_SENSITIVE_KEYS = ("permission_mode", "sandbox_mode", "base_url", "auto_allow_if_sandboxed", "network_allowed")


def _trust_store(user_dir: Path | str) -> Path:
    return Path(user_dir) / "trusted-repos.json"


def _repo_fingerprint(project_dir: Path) -> str:
    """Hash the CONTENT of every repo config file (not the path alone).

    Content-keyed trust: editing the config after confirmation re-triggers the
    prompt — a malicious upstream commit can't ride on a previously-granted
    trust.
    """
    h = hashlib.sha256()
    for rel in REPO_CONFIG_FILES:
        p = project_dir / rel
        h.update(rel.encode("utf-8"))
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def existing_repo_configs(project_dir: Path | str) -> list[Path]:
    """Return the repo-level config files that exist (possibly empty list)."""
    project_dir = Path(project_dir)
    return [project_dir / rel for rel in REPO_CONFIG_FILES if (project_dir / rel).is_file()]


def is_repo_trusted(project_dir: Path | str, user_dir: Path | str) -> bool:
    """True when the repo's CURRENT config content matches a stored trust entry.

    A repo with no repo-level config is trusted by definition (nothing to
    confirm). A config change after confirmation invalidates the trust.
    """
    project_dir = Path(project_dir)
    if not existing_repo_configs(project_dir):
        return True
    store = _trust_store(user_dir)
    if not store.is_file():
        return False
    try:
        entries = json.loads(store.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    trusted = entries.get(str(project_dir.resolve()))
    return trusted == _repo_fingerprint(project_dir)


def mark_repo_trusted(project_dir: Path | str, user_dir: Path | str) -> None:
    """Record the repo's current config content as trusted (after the user
    confirms). Best-effort: a failure to write the store logs a warning and
    leaves the repo untrusted (safe direction — the prompt reappears)."""
    project_dir = Path(project_dir)
    store = _trust_store(user_dir)
    try:
        entries: dict = {}
        if store.is_file():
            entries = json.loads(store.read_text(encoding="utf-8"))
            if not isinstance(entries, dict):
                entries = {}
        entries[str(project_dir.resolve())] = _repo_fingerprint(project_dir)
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("could not persist repo trust for %s: %s", project_dir, e)


def summarize_repo_configs(project_dir: Path | str) -> str:
    """Build the human-readable summary shown in the confirmation prompt.

    Lists each file, its size, and any safety-relevant settings found in it —
    so ``permission_mode = "full"`` can't hide inside a long TOML file the user
    didn't read. MCP ``command``/``url`` entries are listed by server name
    (those spawn processes / make network calls at startup).
    """
    project_dir = Path(project_dir)
    lines = []
    for path in existing_repo_configs(project_dir):
        rel = path.relative_to(project_dir).as_posix()
        lines.append(f"{rel} ({path.stat().st_size} bytes)")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            lines.append("  <unreadable>")
            continue
        if path.name == "config.toml":
            for ln in text.splitlines():
                s = ln.strip()
                if any(k in s for k in _SENSITIVE_KEYS) and "=" in s:
                    lines.append(f"  {s}")
        else:  # .mcp.json — list servers (they spawn/connect at startup)
            try:
                servers = json.loads(text).get("mcpServers", {})
                for name in servers:
                    cfg = servers[name] or {}
                    what = cfg.get("command") or cfg.get("url") or "?"
                    lines.append(f"  server {name!r}: {what}")
            except json.JSONDecodeError:
                lines.append("  <invalid JSON>")
    return "\n".join(lines)
