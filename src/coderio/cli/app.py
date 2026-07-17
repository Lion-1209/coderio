from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from coderio.config import load_config
from coderio.config.bootstrap import ensure_user_dirs
from coderio.skills.store import load_skill_store, SkillStore

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="coderio — a skill-driven coding agent.",
)

skills_app = typer.Typer(help="Manage skills (install/list/update).")
app.add_typer(skills_app, name="skills")

BUNDLED_SKILLS = Path(__file__).resolve().parents[1] / "skills"


def _user_skills_dir() -> Path:
    return Path.home() / ".coderio" / "skills"


def _load_store() -> SkillStore:
    return load_skill_store(BUNDLED_SKILLS, _user_skills_dir(), None)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    resume: str = typer.Option(None, "--resume", help="Resume a session by id."),
    continue_last: bool = typer.Option(False, "--continue", help="Resume most recent session."),
    provider: str = typer.Option(None, "--provider", help="Override provider_id."),
    model: str = typer.Option(None, "--model", help="Override model name."),
):
    """coderio — start the interactive TUI or run a subcommand.

    Launches the Textual TUI: foldable thinking (Ctrl+O), scrollable history,
    slash-command autocomplete. This is the only interactive entry point.
    """
    if ctx.invoked_subcommand is not None:
        return
    ensure_user_dirs()
    from coderio.cli.tui import run_tui
    run_tui(
        provider_override=provider, model_override=model,
        resume=resume, continue_last=continue_last,
    )


@skills_app.command("list")
def skills_list():
    """List installed skills (bundled + user layers)."""
    store = _load_store()
    console = Console()
    names = store.names()
    if not names:
        console.print("No skills installed. Run `coderio skills install`.")
        raise typer.Exit()
    console.print("Installed skills:\n" + "\n".join(f"  - {n}" for n in names))


@skills_app.command("install")
def skills_install(
    repo: str = typer.Option(None, "--repo", help="Git repo URL (default: Lion-Skills)."),
    force: bool = typer.Option(False, "--force", help="Overwrite non-git target."),
):
    """Install/update skills from a git repo (default: Lion-Skills)."""
    from coderio.cli.skills_cmd import install_skills
    cfg = load_config()
    repo_url = repo or cfg.skills.repo_url
    result = install_skills(repo_url, _user_skills_dir(), force=force)
    console = Console()
    if result.success:
        console.print(
            Panel(
                f"{result.action.capitalize()}: {len(result.skills)} skills\n"
                + "\n".join(f"  - {s}" for s in result.skills),
                title="skills",
                border_style="green",
            )
        )
    else:
        console.print(f"[red]Error:[/red] {result.message}")
        raise typer.Exit(1)


@skills_app.command("update")
def skills_update(
    repo: str = typer.Option(None, "--repo", help="Git repo URL."),
):
    """Update installed skills (git pull)."""
    skills_install(repo=repo, force=False)


@app.command("config")
def config_cmd():
    """Print current configuration."""
    cfg = load_config()
    effective_base_url = cfg.model.base_url
    if cfg.model.provider_id:
        from coderio.cli.providers import get_provider
        info = get_provider(cfg.model.provider_id)
        if info is not None:
            effective_base_url = info.base_url or "(user-supplied at runtime)"
    console = Console()
    console.print(
        Panel(
            f"provider_id: {cfg.model.provider_id or '(none)'}"
            f"\nprovider:    {cfg.model.provider}"
            f"\nmodel:       {cfg.model.default}"
            f"\nbase_url:    {effective_base_url}"
            f"\npermission:  {cfg.tools.permission_mode}"
            f"\nskills repo: {cfg.skills.repo_url}",
            title="coderio config",
            border_style="blue",
        )
    )


@app.command("add-provider")
def add_provider_cmd(
    provider_id: str = typer.Option(..., "--id", help="Unique provider ID (e.g. my_openai)."),
    base_url: str = typer.Option(..., "--base-url", help="API base URL."),
    model: str = typer.Option(..., "--model", help="Default model name."),
    kind: str = typer.Option("openai_compatible", "--kind", help="Protocol: openai_compatible or anthropic."),
    api_key: str = typer.Option("", "--key", help="API key (omit to type interactively)."),
):
    """Add a custom provider to config.toml.

    Example:
      coderio add-provider --id my_proxy --base-url https://api.my-proxy.com/v1 \\
          --model gpt-4o --kind openai_compatible --key sk-xxx
    """
    import getpass
    import tomllib
    import tomli_w
    from pathlib import Path

    config_path = Path.home() / ".coderio" / "config.toml"
    creds_path = Path.home() / ".coderio" / "credentials"

    # Read existing config
    data = {}
    if config_path.is_file():
        with open(config_path, "rb") as f:
            data = tomllib.load(f)

    # Update [model] section
    model_section = data.get("model", {})
    model_section["provider_id"] = provider_id
    model_section["default"] = model
    model_section["base_url"] = base_url
    model_section["provider"] = kind
    data["model"] = model_section

    # Write config
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "wb") as f:
        tomli_w.dump(data, f)

    # Save key
    key = api_key or getpass.getpass("API key: ")
    if key:
        from coderio.cli.credentials import write_credentials
        write_credentials({provider_id: key}, creds_path)

    console = Console()
    console.print(f"[green]✅ Provider '{provider_id}' added to {config_path}[/green]")
    console.print(f"   model: {model}  base_url: {base_url}  kind: {kind}")


def main_entry() -> None:
    app()


from coderio.crew.cli_cmd import register as register_crew

register_crew(app)


if __name__ == "__main__":
    main_entry()
