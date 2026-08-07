"""coderio: a skill-driven coding agent."""

# Single source of truth for the version is pyproject.toml's [project].version.
# Read it at runtime via importlib.metadata so __version__ can never drift.
# Falls back to "0.0.0" only when the package isn't installed (e.g. running
# directly from a source checkout without `pip install -e .`) — in that case
# the banner shows "0.0.0", which is obviously wrong rather than silently
# stale, prompting the developer to install the package.
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("coderio")
except PackageNotFoundError:  # pragma: no cover - dev-only fallback
    __version__ = "0.0.0"
