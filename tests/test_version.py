"""Version single-source-of-truth (2026-08-07 report P1-4).

pyproject.toml is the ONLY place the version is declared. __init__.py reads it
via importlib.metadata, and the TUI banner imports __version__ from coderio.
This test guards against reintroducing a hardcoded version that drifts.
"""

from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path


def test_version_resolves_from_package_metadata():
    """coderio.__version__ must come from the installed package metadata, not a
    hardcoded literal. If pip install -e . wasn't run, this fails loudly
    (returns '0.0.0') rather than silently showing a stale number."""
    import coderio

    assert coderio.__version__ != "0.0.0", "package not installed — run `pip install -e .` so __version__ resolves"
    # Must match what importlib.metadata reports.
    assert coderio.__version__ == version("coderio")


def test_version_matches_pyproject():
    """The installed version must match pyproject.toml's declaration. Drift
    means someone bumped pyproject without reinstalling, or hardcoded a
    different version somewhere."""
    import coderio

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "no version declaration in pyproject.toml"
    assert coderio.__version__ == m.group(1), (
        f"pyproject.toml={m.group(1)!r} but installed={coderio.__version__!r}; run `pip install -e .` to sync"
    )
