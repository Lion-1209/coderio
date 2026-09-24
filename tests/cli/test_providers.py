from coderio.cli.providers import PROVIDERS, get_provider


def test_registry_has_expected_providers():
    ids = {p.id for p in PROVIDERS}
    expected = frozenset(
        {
            "bigmodel_coding_plan",
            "stepfun_coding_plan",
            "bigmodel_api",
            "stepfun_api",
            "openai_custom",
        }
    )
    assert expected.issubset(ids)


def test_coding_plan_flag_split():
    plan = {p.id for p in PROVIDERS if p.plan}
    nonplan = {p.id for p in PROVIDERS if not p.plan}
    assert "bigmodel_coding_plan" in plan
    assert "bigmodel_api" in nonplan


def test_bigmodel_coding_plan_models():
    p = get_provider("bigmodel_coding_plan")
    assert p is not None
    assert p.kind == "anthropic"
    assert p.base_url == "https://open.bigmodel.cn/api/anthropic"
    assert "glm-5.2" in p.models
    assert "glm-5.1" in p.models
    assert "glm-5-turbo" in p.models
    assert "glm-4.7" in p.models
    assert p.default_model == "glm-5.2"


def test_stepfun_coding_plan_is_anthropic_protocol():
    # StepFun Step Plan exposes an Anthropic-protocol endpoint at /step_plan
    # (posts to {base_url}/v1/messages). Must stay kind="anthropic" — switching
    # to openai_compatible makes ChatOpenAI join paths as {base_url}/chat/
    # completions, which 404s on this base_url.
    p = get_provider("stepfun_coding_plan")
    assert p.kind == "anthropic"
    assert p.base_url == "https://api.stepfun.com/step_plan"
    assert not p.base_url.endswith("/v1")


def test_stepfun_api_is_openai_protocol():
    p = get_provider("stepfun_api")
    assert p.kind == "openai_compatible"
    assert p.base_url == "https://api.stepfun.com/v1"


def test_model_names_are_lowercase():
    for p in PROVIDERS:
        for m in p.models:
            assert m == m.lower(), f"{p.id} model {m} not lowercase"


def test_get_unknown_returns_none():
    assert get_provider("nope") is None


def test_no_zai_in_registry():
    assert all(not p.id.startswith("zai") for p in PROVIDERS)


# ------------------------------------------------- resolved_provider_kind (D1-2)
def test_resolved_provider_kind_default_openai_compatible():
    from coderio.config.models import Config
    from coderio.llm.factory import resolved_provider_kind

    cfg = Config()
    cfg.model.provider = "openai_compatible"
    assert resolved_provider_kind(cfg) == "openai_compatible"


def test_resolved_provider_kind_legacy_model_section():
    from coderio.config.models import Config
    from coderio.llm.factory import resolved_provider_kind

    cfg = Config()
    cfg.model.provider = "anthropic"
    assert resolved_provider_kind(cfg) == "anthropic"


def test_resolved_provider_kind_registry_provider_id():
    """A known provider_id resolves through the REGISTRY's kind — the raw
    [model].provider field must not decide (that's the #24 class of bug:
    display/payload built from a field the active client doesn't use)."""
    from coderio.config.models import Config
    from coderio.llm.factory import resolved_provider_kind

    cfg = Config()
    cfg.model.provider_id = "bigmodel_coding_plan"  # registry kind = anthropic
    cfg.model.provider = "openai_compatible"  # stale raw field
    assert resolved_provider_kind(cfg) == "anthropic"


def test_resolved_provider_kind_named_profile_wins():
    """With a named profile active, the PROFILE's provider decides — even
    when [model] points somewhere else."""
    from coderio.config.models import Config, Profile
    from coderio.llm.factory import resolved_provider_kind

    cfg = Config()
    cfg.model.provider = "anthropic"
    cfg.profiles = [Profile(name="sf", provider_id="stepfun_api", model="step-3.7-flash")]
    cfg.active_profile = "sf"
    assert resolved_provider_kind(cfg) == "openai_compatible"


def test_resolved_provider_kind_profile_custom_provider():
    """A custom profile (provider_id not in the registry) falls back to the
    profile's own kind field, mirroring build_chat_model's layer-0 path."""
    from coderio.config.models import Config, Profile
    from coderio.llm.factory import resolved_provider_kind

    cfg = Config()
    cfg.profiles = [Profile(name="gw", provider_id="my-gateway", model="m", kind="anthropic")]
    cfg.active_profile = "gw"
    assert resolved_provider_kind(cfg) == "anthropic"


# ------------------------------------------------- ensure_context_limit (WhaleDock follow-up)
def _cfg_for_probe(tmp_path, monkeypatch, *, profiles=True):
    """Config + HOME pointed at tmp so persistence lands in the sandbox."""
    import coderio.llm.factory as factory
    from coderio.config.models import Config, ModelConfig, Profile

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / ".coderio").mkdir(exist_ok=True)
    factory._PROBE_MEMO.clear()
    if profiles:
        cfg = Config()
        cfg.profiles = [Profile(name="sf", provider_id="stepfun_api", model="m1")]
        cfg.active_profile = "sf"
    else:
        cfg = Config()
        cfg.model = ModelConfig(default="m1", provider_id="stepfun_api")
    return cfg


def test_ensure_context_limit_early_returns_known_value(tmp_path, monkeypatch):
    """A profile with context_limit already set must NEVER probe."""
    import coderio.llm.factory as factory
    from coderio.config.models import Config, Profile

    monkeypatch.chdir(tmp_path)
    cfg = Config()
    cfg.profiles = [Profile(name="sf", provider_id="stepfun_api", model="m1", context_limit=256000)]
    cfg.active_profile = "sf"
    factory._PROBE_MEMO.clear()

    def _no_probe(*a, **kw):
        raise AssertionError("must not probe when the value is known")

    # ensure() imports probe lazily from coderio.llm.probe — patch the source module.
    import coderio.llm.probe as probe_mod

    monkeypatch.setattr(probe_mod, "probe_context_limit", _no_probe)
    assert factory.ensure_context_limit(cfg) == 256000


def test_ensure_context_limit_probes_persists_and_caches(tmp_path, monkeypatch):
    """Unknown → probe once → persist into the ACTIVE profile entry →
    subsequent calls hit the in-process memo (probe called exactly once)."""
    import tomllib

    import coderio.llm.factory as factory
    import coderio.llm.probe as probe_mod

    cfg = _cfg_for_probe(tmp_path, monkeypatch)
    # Real-world shape: the config file EXISTS (hand-edited or from an older
    # onboarding) but the profile lacks context_limit. Persistence is
    # read-modify-write; a missing file stays missing (onboarding owns creating it).
    (tmp_path / ".coderio" / "config.toml").write_text(
        'active_profile = "sf"\n\n[[profiles]]\nname = "sf"\nprovider_id = "stepfun_api"\nmodel = "m1"\n',
        encoding="utf-8",
    )
    calls = []

    def _fake_probe(kind, base_url, api_key, model, timeout=5.0):
        calls.append((kind, base_url, model))
        return 256000

    monkeypatch.setattr(probe_mod, "probe_context_limit", _fake_probe)
    # credentials: no creds file → falls to env key; not needed by fake probe.
    assert factory.ensure_context_limit(cfg) == 256000
    assert factory.ensure_context_limit(cfg) == 256000
    assert len(calls) == 1, "second call must hit the memo"
    assert calls[0][2] == "m1"

    cfg_path = tmp_path / ".coderio" / "config.toml"
    assert cfg_path.is_file(), "probed limit must be persisted"
    with open(cfg_path, "rb") as f:
        data = tomllib.load(f)
    assert data["profiles"][0]["context_limit"] == 256000
    assert data["profiles"][0]["name"] == "sf"


def test_ensure_context_limit_failure_is_memoized_not_persisted(tmp_path, monkeypatch):
    """Probe failure → 0, no config write, and no re-probe within the process
    (a provider without the models endpoint must not pay probe latency every
    turn)."""
    import coderio.llm.factory as factory
    import coderio.llm.probe as probe_mod

    cfg = _cfg_for_probe(tmp_path, monkeypatch)
    calls = []

    def _failing_probe(*a, **kw):
        calls.append(1)
        return 0

    monkeypatch.setattr(probe_mod, "probe_context_limit", _failing_probe)
    assert factory.ensure_context_limit(cfg) == 0
    assert factory.ensure_context_limit(cfg) == 0
    assert len(calls) == 1, "failed probe is memoized for the process"
    assert not (tmp_path / ".coderio" / "config.toml").exists(), "failure must not persist"


def test_ensure_context_limit_legacy_model_section(tmp_path, monkeypatch):
    """The no-profiles path persists into [model].context_limit."""
    import tomllib

    import coderio.llm.factory as factory
    import coderio.llm.probe as probe_mod

    cfg = _cfg_for_probe(tmp_path, monkeypatch, profiles=False)
    (tmp_path / ".coderio" / "config.toml").write_bytes(b'[model]\nprovider_id = "stepfun_api"\ndefault = "m1"\n')
    monkeypatch.setattr(probe_mod, "probe_context_limit", lambda *a, **kw: 128000)
    assert factory.ensure_context_limit(cfg) == 128000
    with open(tmp_path / ".coderio" / "config.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["model"]["context_limit"] == 128000
    assert data["model"]["default"] == "m1", "other fields preserved"
