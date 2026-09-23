"""Executable architecture policy check (change plan Phase 3).

Encodes docs/coderio-architecture.md §1's dependency rule as data
(architecture-policy.yaml) and checks it against the real import graph via
Python's ast module — stdlib only, no dependencies.

Rules checked:
  1. layer-direction: a module may import from its own layer or a LOWER
     one (cli=1 → agent=2 → capabilities=3). An upward import (llm → cli)
     is a violation unless it carries a declared exception.
  2. max-file-lines: per-file line budget from the policy.

BASELINE SEMANTICS (the reason this is adoptable on a project with
history): every violation is fingerprinted (rule\\0file\\0detail). Violations
recorded in .architecture-baseline.json do NOT fail the check; only NEW
ones do. `--update-baseline` re-records the current set (a deliberate,
reviewable act — like ZCode's rule "CI never refreshes the baseline
automatically").

Usage:
    python scripts/architecture/check.py                    # full check
    python scripts/architecture/check.py --changed          # git-changed files + their importers
    python scripts/architecture/check.py --update-baseline  # re-record the baseline
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO_ROOT / "architecture-policy.yaml"
BASELINE_PATH = REPO_ROOT / ".architecture-baseline.json"

# The YAML subset this project's policy file uses (mappings, lists, scalars).
# A hand-rolled reader keeps the checker dependency-free; the policy file is
# deliberately simple (no anchors, no flow style beyond inline lists).


def _parse_simple_yaml(text: str) -> dict:
    """Parse the restricted YAML the policy file uses.

    Supports: nested mappings by indent, `- ` list items (scalar or mapping),
    inline [a, b] lists, and `>` folded scalars. Enough for this file; a
    policy that outgrows it should pull in a real YAML parser deliberately.
    """
    root: dict = {}
    stack: list[tuple[int, dict | list]] = [(-1, root)]

    def _scalar(s: str):
        s = s.strip()
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1].strip()
            return [_scalar(x) for x in inner.split(",")] if inner else []
        if s.isdigit():
            return int(s)
        return s.strip("'\"")

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            item = line[2:].strip()
            if not isinstance(parent, list):
                continue
            if ":" in item and not item.startswith("["):
                # mapping item: "- id: x" possibly followed by indented keys
                key, _, val = item.partition(":")
                entry: dict = {key.strip(): _scalar(val)}
                parent.append(entry)
                stack.append((indent, entry))
                # absorb the item's own indented continuation lines
                while i < len(lines):
                    nxt = lines[i]
                    if not nxt.strip() or nxt.strip().startswith("#"):
                        i += 1
                        continue
                    nxt_indent = len(nxt) - len(nxt.lstrip())
                    if nxt_indent <= indent:
                        break
                    k, _, v = nxt.strip().partition(":")
                    entry[k.strip()] = _scalar(v)
                    i += 1
            else:
                parent.append(_scalar(item))
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "":
            # container: peek at the next non-empty line to decide list/dict
            j = i
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#")):
                j += 1
            child_is_list = j < len(lines) and lines[j].strip().startswith("- ")
            child: dict | list = [] if child_is_list else {}
            if isinstance(parent, dict):
                parent[key] = child
            stack.append((indent, child))
        elif val == ">":
            # folded scalar: collect the indented block
            block: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    block.append("")
                    i += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if nxt_indent <= indent:
                    break
                block.append(nxt.strip())
                i += 1
            if isinstance(parent, dict):
                parent[key] = " ".join(b for b in block if b).strip()
        else:
            if isinstance(parent, dict):
                parent[key] = _scalar(val)
    return root


def _load_policy() -> dict:
    return _parse_simple_yaml(POLICY_PATH.read_text(encoding="utf-8"))


def _module_of(path: Path, modules: dict) -> str | None:
    rel = _rel(path)
    for name, spec in modules.items():
        for root in spec.get("roots", []):
            root_norm = str(root).replace("\\", "/").rstrip("/")
            if rel == root_norm or rel.startswith(root_norm + "/"):
                return name
    return None


def _imports_of(path: Path) -> list[tuple[str, int]]:
    """(module, lineno) for every coderio.* import in the file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("coderio."):
            out.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("coderio."):
                    out.append((a.name, node.lineno))
    return out


def _source_files() -> list[Path]:
    return sorted(p for p in (REPO_ROOT / "src" / "coderio").rglob("*.py") if "__pycache__" not in p.parts)


def _fingerprint(rule: str, file: str, detail: str) -> str:
    return hashlib.sha256(f"{rule}\0{file}\0{detail}".encode()).hexdigest()[:16]


def _rel(path: Path) -> str:
    """Repo-relative posix path, or the absolute path when outside the repo
    (tests check synthetic files in temp dirs)."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _collect_violations(policy: dict, files: list[Path]) -> list[dict]:
    modules = policy.get("modules", {})
    max_lines = int(policy.get("budgets", {}).get("max_file_lines", 0) or 0)
    exceptions = {e.get("id"): e for e in policy.get("exceptions", []) if isinstance(e, dict)}
    violations: list[dict] = []
    for path in files:
        rel = _rel(path)
        src_mod = _module_of(path, modules)
        if max_lines:
            n = len(path.read_text(encoding="utf-8").splitlines())
            if n > max_lines:
                violations.append(
                    {
                        "rule": "max-file-lines",
                        "file": rel,
                        "detail": f"{n} lines > {max_lines}",
                        "fp": _fingerprint("max-file-lines", rel, f"{n}>{max_lines}"),
                    }
                )
        if src_mod is None:
            continue
        src_layer = modules[src_mod].get("layer", 99)
        for target, lineno in _imports_of(path):
            parts = target.split(".")
            dst_mod = parts[1] if len(parts) > 1 else None
            if dst_mod is None or dst_mod == src_mod or dst_mod not in modules:
                continue
            dst_layer = modules[dst_mod].get("layer", 99)
            if dst_layer < src_layer:
                # Upward import. Accepted only via a declared exception id
                # shaped "<src>-imports-<dst>" (an optional suffix narrows
                # it, e.g. "llm-imports-cli-providers").
                prefix = f"{src_mod}-imports-{dst_mod}"
                matched = next((eid for eid in exceptions if eid == prefix or eid.startswith(prefix)), None)
                if matched is None:
                    violations.append(
                        {
                            "rule": "layer-direction",
                            "file": rel,
                            "detail": f"{src_mod}(L{src_layer}) -> {dst_mod}(L{dst_layer}) at line {lineno}",
                            "fp": _fingerprint("layer-direction", rel, f"{src_mod}->{dst_mod}"),
                        }
                    )
    # Dedupe on fingerprint (same edge imported from many lines).
    seen: dict[str, dict] = {}
    for v in violations:
        seen.setdefault(v["fp"], v)
    return list(seen.values())


def _changed_files() -> list[Path]:
    out = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", "src/coderio"],  # noqa: S607 — git resolved via PATH on purpose
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout.split()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "src/coderio"],  # noqa: S607 — git resolved via PATH on purpose
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout.split()
    paths = [REPO_ROOT / p for p in out + untracked if p.endswith(".py")]
    return [p for p in paths if p.exists()]


def main() -> int:
    if not POLICY_PATH.exists():
        print(f"policy not found: {POLICY_PATH}")
        return 2
    policy = _load_policy()
    files = _source_files()
    if "--changed" in sys.argv:
        changed = _changed_files()
        if not changed:
            print("architecture: no changed source files")
            return 0
        # Include importers of changed files (a change to a low module can
        # break its upstream consumers' layering too).
        changed_modules = set()
        for c in changed:
            try:
                rel = c.resolve().relative_to(REPO_ROOT).as_posix()
            except ValueError:
                continue
            if rel.endswith(".py"):
                changed_modules.add(rel[: -len(".py")].replace("/", "."))
        all_imports = {p: _imports_of(p) for p in files}
        changed_set = {p.resolve() for p in changed}
        selected = list(changed)
        for p, imps in all_imports.items():
            if p.resolve() in changed_set:
                continue
            if any(target == m or target.startswith(m + ".") for target, _ in imps for m in changed_modules):
                selected.append(p)
                break
        files = selected
        print(f"architecture: checking {len(files)} changed/affected files")

    violations = _collect_violations(policy, files)

    if "--update-baseline" in sys.argv:
        BASELINE_PATH.write_text(
            json.dumps({"version": 1, "violations": violations}, indent=2) + "\n", encoding="utf-8"
        )
        print(f"baseline updated: {len(violations)} violations recorded")
        return 0

    baseline = {}
    if BASELINE_PATH.exists():
        baseline = {v["fp"] for v in json.loads(BASELINE_PATH.read_text(encoding="utf-8")).get("violations", [])}
    new = [v for v in violations if v["fp"] not in baseline]
    for v in new:
        print(f"NEW VIOLATION [{v['rule']}] {v['file']}: {v['detail']}")
    if new:
        print(f"\n{len(new)} new architecture violation(s). Fix them, or record a deliberate")
        print("exception in architecture-policy.yaml, or --update-baseline knowingly.")
        return 1
    print(f"architecture: OK ({len(violations)} baselined, 0 new)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
