from coderio.cli.commands import handle_slash


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
        self.profiles = []
        self.active_profile = ""
        self.usage = None
        self.stream = None


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


# ----------------------------------------------- autocomplete / single-source
def test_completions_cover_every_registered_command():
    """Every command in SLASH_COMMANDS must appear in the autocomplete list.
    This is the guard against drift: add a command but forget to list it for
    completion, and this test fails."""
    from coderio.cli.commands import SLASH_COMMANDS, slash_completions

    completions = slash_completions()
    for cmd in SLASH_COMMANDS:
        # at least one completion candidate starts with the bare command name
        assert any(c.startswith(cmd.name) for c in completions), f"{cmd.name} missing from completions"


def test_completions_include_aliases():
    """/quit must be completable even though it's an alias of /exit."""
    from coderio.cli.commands import slash_completions

    completions = slash_completions()
    assert "/quit" in completions


def test_every_completion_resolves_to_real_command():
    """No phantom completions: every string the suggester offers must resolve to
    a real command (not 'Unknown command'). Strips any argument after the command
    name first, since handle_slash parses command + arg separately."""
    from coderio.cli.commands import slash_completions

    ctx = _FakeCtx()
    ctx.usage = {"input_tokens": 0, "output_tokens": 0}
    for comp in slash_completions():
        # "/mode confirm" -> command "/mode", arg "confirm"
        parts = comp.split(maxsplit=1)
        cmd = parts[0]
        res = handle_slash(cmd, ctx)
        msg = (res.message or "").lower()
        assert "unknown command" not in msg, f"{cmd} offered but unrecognized"


def test_help_lists_all_commands():
    """/help must list every registered command (no command silently absent)."""
    from coderio.cli.commands import SLASH_COMMANDS

    msg = handle_slash("/help", _FakeCtx()).message or ""
    for cmd in SLASH_COMMANDS:
        assert cmd.name in msg, f"{cmd.name} missing from /help output"


def test_help_includes_subcommands_like_skills_install():
    """/skills install is a real subcommand; /help should surface it."""
    msg = handle_slash("/help", _FakeCtx()).message or ""
    assert "skills" in msg.lower()


# ----------------------------------------------- /resume (interactive picker)
def test_resume_no_sessions_reports_none():
    ctx = _FakeCtx()
    ctx.recent_sessions = []
    res = handle_slash("/resume", ctx)
    assert "No sessions" in (res.message or "")


def test_resume_no_arg_signals_picker():
    """/resume with no arg must return __OPEN_PICKER__ so the TUI shows the
    interactive list — NOT a bare id or a 'type an id' prompt (that was the
    rejected design; nobody remembers session ids)."""
    ctx = _FakeCtx()
    ctx.recent_sessions = ["20260703-093941-b9f7", "20260702-164237-4xzk"]
    res = handle_slash("/resume", ctx)
    assert res.message == "__OPEN_PICKER__"


def test_resume_explicit_id_loads_directly():
    """/resume <full-id> is the fallback path (picker is primary)."""
    ctx = _FakeCtx()
    ctx.recent_sessions = ["20260703-093941-b9f7", "20260702-164237-4xzk"]
    res = handle_slash("/resume 20260703-093941-b9f7", ctx)
    assert res.new_session_id == "20260703-093941-b9f7"


def test_resume_ambiguous_prefix_lists_matches():
    ctx = _FakeCtx()
    ctx.recent_sessions = ["20260703-093941-b9f7", "20260703-094012-aaaa"]
    res = handle_slash("/resume 20260703", ctx)
    assert res.new_session_id == ""  # not resolved
    assert "多个会话" in (res.message or "")


def test_resume_unknown_id_suggests_picker():
    ctx = _FakeCtx()
    ctx.recent_sessions = ["20260703-093941-b9f7"]
    res = handle_slash("/resume nope", ctx)
    assert "找不到" in (res.message or "")
    assert "/resume" in (res.message or "")  # points back to the picker


# ----------------------------------------------- /profile (multi-config switch)
def test_profile_no_profiles_suggests_setup():
    ctx = _FakeCtx()
    ctx.profiles = []
    res = handle_slash("/profile", ctx)
    assert "/setup" in (res.message or "")


def test_profile_signals_picker():
    """/profile with profiles present must return __OPEN_PROFILE_PICKER__ so the
    TUI shows the interactive list — same pattern as /resume."""
    from coderio.config import Profile

    ctx = _FakeCtx()
    ctx.profiles = [
        Profile(
            name="glm",
            provider_id="bigmodel_coding_plan",
            model="glm-5.2",
            kind="anthropic",
        )
    ]
    res = handle_slash("/profile", ctx)
    assert res.message == "__OPEN_PROFILE_PICKER__"


def test_profile_list_prints_inline():
    """/profile list prints profiles inline (no popup), active one marked ★."""
    from coderio.config import Profile

    ctx = _FakeCtx()
    ctx.profiles = [
        Profile(
            name="glm",
            provider_id="bigmodel_coding_plan",
            model="glm-5.2",
            kind="anthropic",
        ),
        Profile(name="oai", provider_id="openai", model="gpt-4o", kind="openai_compatible"),
    ]
    ctx.active_profile = "glm"
    res = handle_slash("/profile list", ctx)
    msg = res.message or ""
    assert "★" in msg  # active marker
    assert "glm" in msg
    assert "oai" in msg
