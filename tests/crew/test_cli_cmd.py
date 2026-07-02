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
