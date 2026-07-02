from __future__ import annotations

import getpass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from coderio.agent.loop import run_agent
from coderio.agent.prompts import ActiveSkills
from coderio.config import Config, load_config
from coderio.config.bootstrap import ensure_user_dirs
from coderio.llm import build_chat_model
from coderio.session.store import Session
from coderio.skills.store import load_skill_store, SkillStore
from coderio.tools import build_default_tools
from coderio.tools.permission import PermissionGate, PermissionMode, RichPromptPermissionGate, AutoPermissionGate

from coderio.cli.stream import RichStream

BUNDLED_SKILLS = Path(__file__).resolve().parents[1] / "skills"


def build_gate(cfg: Config, console=None):
    mode = cfg.tools.permission_mode
    if mode == PermissionMode.AUTO:
        return AutoPermissionGate()
    if mode == PermissionMode.PLAN:
        return PermissionGate(PermissionMode.PLAN)
    return RichPromptPermissionGate(console=console)


def build_runtime(
    search_from: Path | str = ".",
    save_dir: Path | str | None = None,
    session: Session | None = None,
    console=None,
    creds_path: Path | str | None = None,
    mode_override: str | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
):
    cfg = load_config(search_from=search_from)

    if mode_override:
        from dataclasses import replace as _replace
        cfg = _replace(cfg, tools=_replace(cfg.tools, permission_mode=mode_override))

    if model_override:
        from dataclasses import replace as _replace
        cfg = _replace(cfg, model=_replace(cfg.model, default=model_override))

    if provider_override:
        from dataclasses import replace as _replace
        cfg = _replace(cfg, model=_replace(cfg.model, provider_id=provider_override))

    if cfg.skills.auto_load:
        store = load_skill_store(BUNDLED_SKILLS, Path.home() / ".coderio" / "skills", Path(search_from) / ".coderio" / "skills")
    else:
        store = SkillStore()

    model = build_chat_model(cfg, creds_path=creds_path)
    tools = build_default_tools(cfg.tools.bash_shell)
    gate = build_gate(cfg, console=console)

    if session is None:
        save = save_dir or Path(cfg.session.save_dir).expanduser()
        session = Session.create(save, {"model": cfg.model.default, "provider": cfg.model.provider})

    active = ActiveSkills()
    stream = RichStream(console or Console())
    return cfg, store, model, tools, gate, session, active, stream


def _maybe_onboard(console, creds_path):
    """Run onboarding if no key configured. Returns provider_id or None."""
    from coderio.cli.credentials import read_credentials
    creds = read_credentials(creds_path)
    if creds:
        return next(iter(creds))
    from coderio.cli.onboarding import run_onboarding
    result = run_onboarding(
        prompt_fn=lambda msg: console.input(f"{msg} "),
        password_fn=lambda: getpass.getpass("API key: "),
        creds_file=creds_path,
    )
    return result.provider_id if result else None


def _resolve_resume(cfg: Config, resume: str | None, continue_last: bool) -> Session:
    save_dir = Path(cfg.session.save_dir).expanduser()
    if resume:
        return Session.load_by_id(save_dir, resume)
    recent = Session.list_recent(save_dir, limit=1)
    if not recent:
        raise SystemExit("No previous session to continue.")
    return Session.load_by_id(save_dir, recent[0])


def run_repl(
    search_from: Path | str = ".",
    save_dir: Path | str | None = None,
    resume: str | None = None,
    continue_last: bool = False,
    provider_override: str | None = None,
    model_override: str | None = None,
    creds_path: Path | str | None = None,
) -> None:
    console = Console()
    ensure_user_dirs()
    if creds_path is None:
        creds_path = Path.home() / ".coderio" / "credentials"
    _maybe_onboard(console, creds_path)
    try:
        cfg, store, model, tools, gate, session, active, stream = build_runtime(
            search_from=search_from,
            save_dir=save_dir,
            console=console,
            creds_path=creds_path,
            provider_override=provider_override,
            model_override=model_override,
        )
    except Exception as e:
        # Initialization errors (missing API key, bad provider, network) must not
        # crash with a raw traceback. Surface a clear, actionable message instead.
        console.print(Panel(
            f"[red]启动失败:[/red] {type(e).__name__}: {e}\n\n"
            "[dim]常见原因: API key 未配置 / provider 无效 / 网络不通。[/dim]\n"
            "[dim]运行 coderio config 检查配置, 或设置 ANTHROPIC_API_KEY 环境变量。[/dim]",
            title="[red]coderio 启动错误[/red]", border_style="red",
        ))
        return
    console.print(
        Panel(
            f"[bold magenta]coderio[/bold magenta]  [dim]model=[/dim]{cfg.model.default}  [dim]perm=[/dim]{gate.mode}"
            "\n[dim]模式:[/dim] [cyan]single-agent[/cyan] (交互式). 大需求可用 [yellow]/crew <需求>[/yellow] 进 6-agent 流水线.\n[dim]输入 /help 看命令, /exit 退出[/dim]",
            title="[bold magenta]coderio[/bold magenta]",
            border_style="magenta",
        )
    )
    _loop(console, cfg, store, model, tools, gate, session, active, stream, search_from, save_dir, creds_path)


def _loop(console, cfg, store, model, tools, gate, session, active, stream, search_from, save_dir, creds_path):
    from coderio.cli.commands import handle_slash, ReplContext
    while True:
        try:
            line = console.input("[bold cyan]▸ you[/bold cyan] ")
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye.")
            return
        if not line.strip():
            continue
        if line.startswith("/"):
            try:
                ctx = ReplContext(
                    available_skills=store.names(),
                    active_skills_names={s.name for s in active.all()},
                    permission_mode=gate.mode,
                    model_name=cfg.model.default,
                    provider_id=cfg.model.provider_id,
                    api_key="",
                    base_url=cfg.model.base_url,
                    recent_sessions=Session.list_recent(Path(cfg.session.save_dir).expanduser()),
                    usage=getattr(stream, "usage", None),
                    stream=stream,
                )
                res = handle_slash(line, ctx)
            except Exception as e:
                # A slash-command error is NOT fatal — show it cleanly and keep the
                # REPL alive. The agent should surface errors, never crash the shell
                # with a raw traceback (which leaves the user stuck mid-session).
                console.print(f"[red]命令错误:[/red] {type(e).__name__}: {e}")
                console.print("[dim]会话已保留，可继续输入。输入 /help 查看可用命令。[/dim]")
                continue
            if res.message and res.message != "__SKILLS_INSTALL__":
                console.print(res.message)
            if not res.continue_loop:
                return
            if res.message == "__SKILLS_INSTALL__":
                _do_skills_install(console, cfg, store, search_from)
            if res.reset_runtime:
                from dataclasses import replace as _replace
                if res.new_permission_mode:
                    cfg = _replace(cfg, tools=_replace(cfg.tools, permission_mode=res.new_permission_mode))
                    gate = build_gate(cfg, console=console)
                if line.strip().split(maxsplit=1)[0] == "/model":
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) > 1 and parts[1].strip():
                        cfg = _replace(cfg, model=_replace(cfg.model, default=parts[1].strip()))
                        model = build_chat_model(cfg, creds_path=creds_path)
                if line.strip().split(maxsplit=1)[0] == "/clear":
                    active.clear()
                    save = save_dir or Path(cfg.session.save_dir).expanduser()
                    session = Session.create(save, model=cfg.model.default)
            continue
        try:
            # Multimodal: detect image paths in the user's input and build
            # content blocks (text + image) for vision-capable models.
            from coderio.cli.multimodal import build_user_content, extract_images
            imgs = extract_images(line)
            if imgs:
                console.print(f"[dim]📎 已附加 {len(imgs)} 张图片: "
                              + ", ".join(p for p, _, _ in imgs) + "[/dim]")
            user_content = build_user_content(line)
            run_agent(
                user_input=user_content,
                model=model,
                tools=tools,
                gate=gate,
                skill_store=store,
                active_skills=active,
                session=session,
                stream=stream,
                max_rounds=cfg.tools.max_tool_rounds,
                stage_auto_inject=cfg.skills.stage_auto_inject,
                harness_enabled=cfg.skills.harness,
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow] Session preserved.")
        except Exception as e:
            # Agent/loop errors are surfaced cleanly, not as a raw traceback.
            # The REPL stays alive so the user can retry or adjust. Only truly
            # fatal errors (API auth/network) repeat; tool errors are already
            # handled inside the loop as tool results.
            console.print(f"[red]运行错误:[/red] {type(e).__name__}: {e}")
            console.print("[dim]会话已保留，可继续输入或重试。[/dim]")


def _do_skills_install(console, cfg, store, search_from):
    from coderio.cli.skills_cmd import install_skills
    result = install_skills(
        repo_url=cfg.skills.repo_url,
        target_dir=Path.home() / ".coderio" / "skills",
    )
    if result.success:
        console.print(f"[green]Skills {result.action}:[/green] {', '.join(result.skills)}")
        new_store = load_skill_store(BUNDLED_SKILLS, Path.home() / ".coderio" / "skills", Path(search_from) / ".coderio" / "skills")
        store._skills = new_store._skills
    else:
        console.print(f"[red]Error:[/red] {result.message}")


def main() -> None:
    run_repl()


if __name__ == "__main__":
    main()
