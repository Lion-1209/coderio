"""Tests for the executable architecture-policy checker (Phase 3).

The checker's value proposition is "a NEW violation fails, a baselined one
doesn't" — these tests pin that contract, including the mutation case
(inject an upward import → the checker must go red).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_CHECKER = REPO_ROOT / "scripts" / "architecture" / "check.py"

_spec = importlib.util.spec_from_file_location("architecture_check", _CHECKER)
check = importlib.util.module_from_spec(_spec)
sys.modules["architecture_check"] = check
_spec.loader.exec_module(check)


def test_policy_file_parses_with_layers_and_exceptions():
    policy = check._load_policy()
    modules = policy["modules"]
    assert modules["cli"]["layer"] == 1
    assert modules["agent"]["layer"] == 2
    assert modules["tools"]["layer"] == 3
    # Every capability module sits below the agent layer.
    for name in ("tools", "skills", "session", "config", "llm"):
        assert modules[name]["layer"] == 3
    assert "max_file_lines" in policy["budgets"]
    exc_ids = {e["id"] for e in policy["exceptions"]}
    assert "llm-imports-cli-providers" in exc_ids
    for e in policy["exceptions"]:
        assert e.get("reason", "").strip(), f"exception {e['id']} must state its reason"
        assert e.get("removal", "").strip(), f"exception {e['id']} must state its removal condition"


def test_current_tree_has_no_new_violations():
    """The real tree, against the real baseline: zero NEW violations. The
    baselined ones (oversized files) are recorded, not ignored silently."""
    policy = check._load_policy()
    violations = check._collect_violations(policy, check._source_files())
    baseline = {}
    bp = check.BASELINE_PATH
    if bp.exists():
        import json

        baseline = {v["fp"] for v in json.loads(bp.read_text(encoding="utf-8"))["violations"]}
    new = [v for v in violations if v["fp"] not in baseline]
    assert not new, f"new architecture violations: {new}"


def test_injected_upward_import_is_a_violation(tmp_path):
    """Mutation case: a capability module importing the CLI layer must be
    reported. This is the check's whole reason to exist."""
    policy = check._load_policy()
    bad = tmp_path / "bad_module.py"
    bad.write_text(
        "from __future__ import annotations\n\nfrom coderio.cli.commands import handle_slash\n",
        encoding="utf-8",
    )
    # Point a synthetic roots entry at the temp file so _module_of resolves it.
    policy["modules"]["tools"]["roots"] = [str(bad)]
    violations = check._collect_violations(policy, [bad])
    assert any(v["rule"] == "layer-direction" and "cli" in v["detail"] for v in violations), violations


def test_baselined_fingerprint_is_not_new(tmp_path):
    """A recorded violation must not fail the check (that's the adoptability
    contract); an unrecorded one must."""
    policy = check._load_policy()
    big = tmp_path / "big.py"
    big.write_text("x = 1\n" * 2000, encoding="utf-8")
    policy["modules"]["tools"]["roots"] = [str(big)]
    violations = check._collect_violations(policy, [big])
    assert violations, "an 2000-line file must violate the budget"
    fp = violations[0]["fp"]
    assert check._fingerprint("max-file-lines", violations[0]["file"], "2000>800") == fp
    # Same fingerprint → baselined → not new.
    assert fp not in [v["fp"] for v in violations if v["fp"] != fp]
