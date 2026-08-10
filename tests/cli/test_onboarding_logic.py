"""Pure-logic tests for onboarding functions (no terminal, no network).

These cover the config-writing and onboarding-necessity logic that the TUI
onboarding screen delegates to. The TUI screen itself (OnboardingScreen) is
tested via Textual's headless harness in test_tui_onboarding.py.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from coderio.cli.onboarding import OnboardingResult, _save_profile_to_config
from coderio.cli.repl import _needs_onboarding

# ----------------------------------------------------- _save_profile_to_config


def _make_result(**overrides) -> OnboardingResult:
    """Build an OnboardingResult with sensible defaults for testing."""
    defaults = {
        "provider_id": "bigmodel_coding_plan",
        "model": "GLM-5.2",
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "kind": "anthropic",
        "api_key": "test-key-123",
        "context_limit": 0,
    }
    defaults.update(overrides)
    return OnboardingResult(**defaults)


def _read_config(config_path: Path) -> dict:
    """Read a config.toml back as a dict (empty if missing/corrupt)."""
    if not config_path.is_file():
        return {}
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def test_save_profile_to_empty_config(tmp_path):
    """Appending a profile to a non-existent config creates the file + section."""
    config_path = tmp_path / "config.toml"
    result = _make_result()
    _save_profile_to_config(result, "my-profile", config_path)
    data = _read_config(config_path)
    assert "profiles" in data
    assert len(data["profiles"]) == 1
    assert data["profiles"][0]["name"] == "my-profile"
    assert data["profiles"][0]["provider_id"] == "bigmodel_coding_plan"
    assert data["active_profile"] == "my-profile"


def test_save_profile_appends_to_existing(tmp_path):
    """A new profile is appended after existing ones (not replacing them)."""
    config_path = tmp_path / "config.toml"
    _save_profile_to_config(_make_result(model="glm-4.5"), "first", config_path)
    _save_profile_to_config(_make_result(model="step-3.7"), "second", config_path)
    data = _read_config(config_path)
    assert len(data["profiles"]) == 2
    assert data["profiles"][0]["name"] == "first"
    assert data["profiles"][1]["name"] == "second"
    assert data["active_profile"] == "second"  # last saved wins


def test_save_profile_replaces_same_name(tmp_path):
    """Re-configuring an existing profile replaces it in place, not duplicates."""
    config_path = tmp_path / "config.toml"
    _save_profile_to_config(_make_result(model="old-model"), "my-profile", config_path)
    _save_profile_to_config(_make_result(model="new-model"), "my-profile", config_path)
    data = _read_config(config_path)
    assert len(data["profiles"]) == 1, "same-name profile must replace, not duplicate"
    assert data["profiles"][0]["model"] == "new-model"


def test_save_profile_preserves_other_sections(tmp_path):
    """Read-modify-write must not clobber unrelated config sections."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[tools]\npermission_mode = "confirm"\n\n[model]\ndefault = "old"\n', encoding="utf-8")
    _save_profile_to_config(_make_result(), "my-profile", config_path)
    data = _read_config(config_path)
    assert data["tools"]["permission_mode"] == "confirm"
    assert data["model"]["default"] == "old"


def test_save_profile_writes_context_limit_when_probed(tmp_path):
    """When context_limit > 0 (probe succeeded), it's persisted to the profile."""
    config_path = tmp_path / "config.toml"
    result = _make_result(context_limit=256_000)
    _save_profile_to_config(result, "my-profile", config_path)
    data = _read_config(config_path)
    assert data["profiles"][0]["context_limit"] == 256_000


def test_save_profile_omits_context_limit_when_zero(tmp_path):
    """When context_limit == 0 (probe failed/skipped), it's NOT written —
    don't overwrite a previously-good value with a fallback marker."""
    config_path = tmp_path / "config.toml"
    result = _make_result(context_limit=0)
    _save_profile_to_config(result, "my-profile", config_path)
    data = _read_config(config_path)
    assert "context_limit" not in data["profiles"][0]


# --------------------------------------------------------------- _needs_onboarding


def test_needs_onboarding_when_nothing_configured(tmp_path, monkeypatch):
    """No credentials, no config, no env vars → onboarding needed (True)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("Z_API_KEY", raising=False)
    creds_path = tmp_path / "credentials"
    assert _needs_onboarding(creds_path) is True


def test_needs_onboarding_skipped_when_credentials_exist(tmp_path, monkeypatch):
    """An existing credentials file with at least one key → skip onboarding."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("Z_API_KEY", raising=False)
    creds_path = tmp_path / "credentials"
    # Credentials file is TOML: [provider_id]\nkey = "..."
    creds_path.write_text('[bigmodel_coding_plan]\nkey = "sk-test"\n', encoding="utf-8")
    assert _needs_onboarding(creds_path) is False


def test_needs_onboarding_skipped_when_config_has_provider(tmp_path, monkeypatch):
    """A config.toml with [model].provider_id → skip onboarding."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("Z_API_KEY", raising=False)
    creds_path = tmp_path / "credentials"
    config_path = tmp_path / "config.toml"
    config_path.write_text('[model]\nprovider_id = "bigmodel_coding_plan"\n', encoding="utf-8")
    assert _needs_onboarding(creds_path) is False


def test_needs_onboarding_skipped_when_env_var_set(tmp_path, monkeypatch):
    """An ANTHROPIC_API_KEY env var → skip onboarding (even with no files)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    creds_path = tmp_path / "credentials"
    assert _needs_onboarding(creds_path) is False


def test_needs_onboarding_with_z_api_key_env(tmp_path, monkeypatch):
    """The Z_API_KEY env var also skips onboarding."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("Z_API_KEY", "z-key-from-env")
    creds_path = tmp_path / "credentials"
    assert _needs_onboarding(creds_path) is False
