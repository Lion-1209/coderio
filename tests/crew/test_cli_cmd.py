from typer.testing import CliRunner

from coderio.cli.app import app


def test_crew_help_registered():
    result = CliRunner().invoke(app, ["crew", "--help"])
    assert result.exit_code == 0
    out = result.output.lower()
    assert "需求" in out
    assert "request" in out
    assert "auto" in out


def test_crew_command_runs_with_mock(tmp_path, monkeypatch):
    """End-to-end through the Typer command, with a mocked orchestrator."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    import coderio.crew.cli_cmd as cli_cmd_mod

    class _FakeOrch:
        def __init__(self, **kw):
            self.kw = "init"

        def run(self, request):
            from coderio.crew.state import ProjectState

            s = ProjectState(request=request)
            s.clarification = "c"
            s.spec = "s"
            s.commit_message = "done"
            return s

    monkeypatch.setattr(cli_cmd_mod, "CrewOrchestrator", _FakeOrch)

    result = CliRunner().invoke(app, ["crew", "build a snake", "--auto"])
    assert result.exit_code == 0
    assert "build a snake" == result.output or "build a snake" in result.output


def test_crew_cli_shows_partial_when_verify_failed(tmp_path, monkeypatch):
    """REGRESSION (P1): when the orchestrator returns status='partial', the CLI
    must show a yellow warning — NOT the unconditional green '✓ crew 完成'.
    The old code hardcoded green regardless of verification outcome."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    import coderio.crew.cli_cmd as cli_cmd_mod
    from coderio.crew.state import ProjectState

    class _FakePartialOrch:
        def __init__(self, **kw):
            pass

        def run(self, request):
            s = ProjectState(request=request)
            s.verification = "FAIL: tests still red"
            s.commit_message = "best-effort commit"
            s.status = "partial"
            return s

    monkeypatch.setattr(cli_cmd_mod, "CrewOrchestrator", _FakePartialOrch)
    result = CliRunner().invoke(app, ["crew", "x", "--auto"])
    assert result.exit_code == 0
    # The title must reflect the partial outcome (not the old unconditional ✓).
    assert "验证未通过" in result.output or "⚠" in result.output


def test_crew_cli_shows_success_when_verify_passed(tmp_path, monkeypatch):
    """Happy path: status='success' shows the green ✓ title."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    import coderio.crew.cli_cmd as cli_cmd_mod
    from coderio.crew.state import ProjectState

    class _FakeSuccessOrch:
        def __init__(self, **kw):
            pass

        def run(self, request):
            s = ProjectState(request=request)
            s.verification = "[CREW_VERIFY] PASS"
            s.commit_message = "done"
            s.status = "success"
            return s

    monkeypatch.setattr(cli_cmd_mod, "CrewOrchestrator", _FakeSuccessOrch)
    result = CliRunner().invoke(app, ["crew", "x", "--auto"])
    assert result.exit_code == 0
    assert "✓" in result.output
