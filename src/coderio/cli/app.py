from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from coderio.config import load_config
from coderio.config.bootstrap import ensure_user_dirs
from coderio.skills.store import SkillStore, load_skill_store

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="coderio — a skill-driven coding agent.",
)

skills_app = typer.Typer(help="Manage skills (install/list/update).")
app.add_typer(skills_app, name="skills")

mcp_app = typer.Typer(help="Manage MCP servers (add/list/remove).")
app.add_typer(mcp_app, name="mcp")

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
        provider_override=provider,
        model_override=model,
        resume=resume,
        continue_last=continue_last,
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


# ----------------------------------------------------------- coderio mcp


@mcp_app.command("list")
def mcp_list_cmd():
    """List configured MCP servers (project + user scope)."""
    from coderio.cli.mcp_cmd import mcp_list

    console = Console()
    entries = mcp_list()
    if not entries:
        console.print("No MCP servers configured. Add one with [cyan]coderio mcp add <name> --command ...[/cyan]")
        return
    # Group by scope for readability.
    project = [(n, p) for n, s, p in entries if s == "project"]
    user = [(n, p) for n, s, p in entries if s == "user"]
    if project:
        console.print("[bold green]project scope[/bold green] (.mcp.json):")
        for name, path in project:
            console.print(f"  - {name}   [dim]{path}[/dim]")
    if user:
        console.print("[bold blue]user scope[/bold blue] (~/.coderio/mcp.json):")
        for name, path in user:
            console.print(f"  - {name}   [dim]{path}[/dim]")


@mcp_app.command("add")
def mcp_add_cmd(
    name: str = typer.Argument(..., help="Server name (used as tool-name prefix)."),
    type: str = typer.Option("stdio", "--type", help="Transport: stdio (default), http, or sse."),
    command: str = typer.Option(None, "--command", help="stdio: executable to run (e.g. npx)."),
    url: str = typer.Option(None, "--url", help="http/sse: server endpoint URL."),
    scope: str = typer.Option("project", "--scope", help="Config scope: project (default) or user."),
    arg: list[str] = typer.Option(
        None, "--arg", help="stdio: argument (repeatable, in order). E.g. --arg -y --arg @mcp/server-fs"
    ),
):
    """Add an MCP server to the project (.mcp.json) or user (~/.coderio/mcp.json) config."""
    from coderio.cli.mcp_cmd import mcp_add

    result = mcp_add(
        name,
        server_type=type,
        command=command,
        url=url,
        args=arg,
        scope=scope,
    )
    console = Console()
    if result.success:
        console.print(f"[green]{result.action.capitalize()}[/green]: {result.message}")
    else:
        console.print(f"[red]Error:[/red] {result.message}")
        raise typer.Exit(1)


@mcp_app.command("remove")
def mcp_remove_cmd(
    name: str = typer.Argument(..., help="Server name to remove."),
    scope: str = typer.Option("project", "--scope", help="Config scope: project (default) or user."),
):
    """Remove an MCP server entry (no-op if the name is absent)."""
    from coderio.cli.mcp_cmd import mcp_remove

    result = mcp_remove(name, scope=scope)
    console = Console()
    if result.action == "noop":
        console.print(f"[yellow]No-op:[/yellow] {result.message}")
    else:
        console.print(f"[green]{result.action.capitalize()}[/green]: {result.message}")


@app.command("run")
def run_cmd(
    task: str = typer.Argument(..., help="Task text for a one-shot headless agent run."),
    provider: str = typer.Option(None, "--provider", help="Override provider_id."),
    model: str = typer.Option(None, "--model", help="Override model name."),
    permission: str = typer.Option(
        "plan",
        "--permission",
        "-p",
        help="Permission tier: plan (default, read-only), confirm/auto_edit, full (--dangerously-skip-permissions).",
    ),
    session_id: str = typer.Option(None, "--session-id", help="Resume a prior session by id."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Print only the final result."),
    skip_permissions: bool = typer.Option(
        False,
        "--dangerously-skip-permissions",
        help="Run with full permissions (no prompts). Explicit opt-in — headless default is read-only plan mode.",
    ),
    timeout: int = typer.Option(
        0,
        "--timeout",
        help="Wall-clock limit in seconds (0 = unlimited). On timeout: exit 124. Recommended for CI.",
    ),
):
    """One-shot headless agent run (CI / scripting / benchmarks).

    Streams tokens to stdout by default; tool progress goes to stderr. Never
    prompts interactively — untrusted repo config or missing credentials are
    hard errors. Default permission is plan (read-only); use
    --dangerously-skip-permissions for full access. Exit codes: 0 success,
    1 config error, 2 agent failure, 124 timeout. The command blacklist
    still applies in every mode.
    """
    from coderio.cli.run_cmd import run_headless

    run_headless(
        task,
        provider=provider,
        model=model,
        permission=permission,
        session_id=session_id,
        quiet=quiet,
        skip_permissions=skip_permissions,
        timeout=timeout,
    )


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


def main_entry() -> None:
    # Ensure stdout/stderr use UTF-8 on Windows. The default Windows console
    # encoding (cp1252) can't represent CJK characters in the Rich/Typer output
    # (Chinese UI strings, skill names), causing UnicodeEncodeError. Reconfiguring
    # here makes `coderio --help` and `coderio config` work in any locale —
    # matching the behavior Linux/macOS users get by default.
    import sys

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass  # already reconfigured or unsupported — ignore
    app()


if __name__ == "__main__":
    main_entry()
