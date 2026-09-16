"""#24: display/persistence paths must use the RESOLVED model name.

cfg.model.default is the raw [model] field — with a named profile active it
names a different model than the one actually serving requests (live case:
banner said step-3.7-flash while the active profile ran water18-0910).
"""

from __future__ import annotations

from types import SimpleNamespace

from coderio.llm.factory import resolved_model_name


def _cfg(active_profile, profiles, default, provider_id="stepfun_coding_plan"):
    return SimpleNamespace(
        active_profile=active_profile,
        profiles=profiles,
        model=SimpleNamespace(default=default, provider_id=provider_id),
    )


def test_active_profile_model_wins():
    cfg = _cfg(
        active_profile="P",
        profiles=[SimpleNamespace(name="P", model="water18-0910")],
        default="step-3.7-flash",
    )
    assert resolved_model_name(cfg) == "water18-0910"


def test_legacy_path_uses_model_default():
    cfg = _cfg(active_profile="", profiles=[], default="step-3.7-flash")
    assert resolved_model_name(cfg) == "step-3.7-flash"


def test_stale_active_profile_falls_back_to_default():
    """active_profile naming a nonexistent profile → legacy [model].default,
    mirroring _resolve_profile's fall-through."""
    cfg = _cfg(
        active_profile="ghost",
        profiles=[SimpleNamespace(name="real", model="other")],
        default="step-3.7-flash",
    )
    assert resolved_model_name(cfg) == "step-3.7-flash"
