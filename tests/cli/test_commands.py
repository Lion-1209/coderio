from coderio.cli.commands import handle_slash, CommandResult


class _FakeCtx:
    def __init__(self):
        self.active_skills_names = {"debugging"}
        self.available_skills = ["debugging", "testing", "commit-message"]
        self.permission_mode = "confirm"
        self.model_name = "glm-5.2"
        self.provider_id = "bigmodel_coding_plan"
        self.api_key = "sk-abcdef1234"
        self.base_url = "https://open.bigmodel.cn/api/anthropic"
        self.recent_sessions = ["20260625-120000-ab12"]


def test_help_lists_commands():
    res = handle_slash("/help", _FakeCtx())
    assert res.continue_loop is True
    msg = res.message or ""
    assert "help" in msg
    assert msg  # non-empty


def test_exit_stops_loop():
    res = handle_slash("/exit", _FakeCtx())
    assert res.continue_loop is False


def test_quit_alias():
    res = handle_slash("/quit", _FakeCtx())
    assert res.continue_loop is False


def test_skills_lists_with_active_marker():
    res = handle_slash("/skills", _FakeCtx())
    msg = res.message or ""
    assert "debugging" in msg
    assert "testing" in msg


def test_config_shows_masked_key():
    res = handle_slash("/config", _FakeCtx())
    msg = res.message or ""
    assert "glm-5.2" in msg
    assert "****1234" in msg
    assert "sk-abcdef1234" not in msg


def test_config_resolves_base_url_from_registry():
    """When provider_id is set, /config shows the registry's base_url, not the
    stale config.toml value (regression: stepfun config showed zhi pu url)."""
    ctx = _FakeCtx()
    ctx.provider_id = "stepfun_coding_plan"
    ctx.base_url = "https://open.bigmodel.cn/api/paas/v4"
    res = handle_slash("/config", ctx)
    msg = res.message or ""
    assert "api.stepfun.com/step_plan" in msg
    assert "open.bigmodel.cn" not in msg


def test_unknown_command():
    res = handle_slash("/nope", _FakeCtx())
    msg = res.message or ""
    assert "unknown" in msg.lower()


def test_clear_signals_reset():
    res = handle_slash("/clear", _FakeCtx())
    assert res.reset_runtime is True


def test_mode_change_signals_reset():
    res = handle_slash("/mode auto", _FakeCtx())
    assert res.reset_runtime is True
    assert res.new_permission_mode == "auto"


def test_sessions_lists():
    res = handle_slash("/sessions", _FakeCtx())
    msg = res.message or ""
    assert "20260625" in msg


def test_cost_shows_usage_when_available():
    ctx = _FakeCtx()
    ctx.usage = {"input_tokens": 100, "output_tokens": 50}
    res = handle_slash("/cost", ctx)
    msg = res.message or ""
    assert "100" in msg
    assert "50" in msg
    assert "150" in msg


def test_cost_reports_none_when_no_usage():
    ctx = _FakeCtx()
    ctx.usage = None
    res = handle_slash("/cost", ctx)
    msg = res.message or ""
    assert "暂无" in msg or "unavailable" in msg.lower()
