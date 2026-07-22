from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from coderio.crew.orchestrator import CrewOrchestrator
from coderio.config import load_config
from coderio.config.bootstrap import ensure_user_dirs
from coderio.llm import build_chat_model
from coderio.skills.store import load_skill_store
from coderio.tools.permission import (
    AutoPermissionGate,
    PermissionGate,
    PermissionMode,
    RichPromptPermissionGate,
)
from coderio.tools.workspace import WorkspacePolicy

BUNDLED_SKILLS = Path(__file__).resolve().parents[1] / "skills"


def _build_crew_gate(cfg, console):
    """Construct the crew permission gate with workspace policy attached.

    Same security floor as the REPL gate: workspace boundary is enforced in
    ALL modes, so --auto can't write outside the workspace.
    """
    policy = WorkspacePolicy(root=cfg.tools.workspace_root)
    mode = cfg.tools.permission_mode
    if mode == PermissionMode.AUTO:
        return AutoPermissionGate(policy=policy)
    if mode == PermissionMode.PLAN:
        return PermissionGate(PermissionMode.PLAN, policy=policy)
    return RichPromptPermissionGate(console=console, policy=policy)


def run_crew(
    request: str,
    auto_mode: bool = False,
    creds_path: Path | str | None = None,
    console: Console | None = None,
) -> None:
    """Run the full crew pipeline for a request."""
    console = console or Console()
    ensure_user_dirs()
    cfg = load_config()
    if creds_path is None:
        creds_path = Path.home() / ".coderio" / "credentials"
    store = load_skill_store(BUNDLED_SKILLS, Path.home() / ".coderio" / "skills", None)
    model = build_chat_model(cfg, creds_path=creds_path)
    # --auto uses AutoPermissionGate BUT still attaches the workspace policy —
    # auto skips clarify/spec pauses + interactive confirmations, NOT the
    # workspace security floor.
    if auto_mode:
        policy = WorkspacePolicy(root=cfg.tools.workspace_root)
        gate = AutoPermissionGate(policy=policy)
    else:
        gate = _build_crew_gate(cfg, console)
    from coderio.cli.stream import RichStream

    stream = RichStream(console)

    console.print(
        Panel(
            f"[bold]需求:[/bold] {request}\n模式: "
            f"{'auto (跳过暂停)' if auto_mode else 'interactive (clarify/spec 暂停)'}",
            title="coderio crew",
            border_style="magenta",
        )
    )

    def on_pause(stage: str, output: str) -> str:
        console.print(
            Panel(output, title=f"[{stage}] 产出 — 请回应", border_style="yellow")
        )
        return console.input("[bold]你的回应 (回车=接受默认):[/bold] ")

    orch = CrewOrchestrator(
        model=model,
        store=store,
        gate=gate,
        stream=stream,
        auto_mode=auto_mode,
        on_pause=None if auto_mode else on_pause,
    )
    state = orch.run(request)

    # Display outcome reflects actual status: green only when verify passed,
    # yellow when the crew committed best-effort code after the fix budget was
    # exhausted (partial), red when catastrophic errors occurred. The old code
    # showed an unconditional green "✓ crew 完成" — misleading when the verifier
    # had actually failed.
    status_cfg = {
        "success": ("✓ crew 完成", "green"),
        "partial": ("⚠ crew 完成（验证未通过，请人工复核）", "yellow"),
        "failed": ("✗ crew 失败", "red"),
    }
    title, border = status_cfg.get(state.status, status_cfg["success"])
    console.print(
        Panel(
            f"澄清: {state.clarification[:80]}\nspec: {state.spec[:80]}\n任务: {state.task_list[:80]}"
            f"\n实现: {state.implementation[:80]}\n验证: {state.verification[:80]}\n提交: {state.commit_message}",
            title=title,
            border_style=border,
        )
    )


def register(app):
    @app.command("crew")
    def crew_cmd(
        request: str = typer.Argument(..., help="需求描述，用引号包起来"),
        auto_mode: bool = typer.Option(
            False, "--auto", help="全自动，跳过 clarify/spec 暂停"
        ),
    ):
        """跑完整的多 agent 流水线 (clarify→spec→task→execute→verify→commit)。"""
        run_crew(request, auto_mode)
