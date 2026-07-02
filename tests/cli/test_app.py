from typer.testing import CliRunner

from coderio.cli.app import app


def test_help_shows_subcommands():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    out = result.output
    assert "skills" in out


def test_skills_list_help():
    result = CliRunner().invoke(app, ["skills", "--help"])
    assert result.exit_code == 0
    assert "install" in result.output


def test_config_command_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = CliRunner().invoke(app, ["config"])
    assert result.exit_code == 0
    assert "model" in result.output.lower()
    assert "provider" in result.output.lower()
