from __future__ import annotations

from dataclasses import dataclass, field

from coderio.cli.render import mask_key


@dataclass(frozen=True)
class SlashCommand:
    """A single slash command's metadata, for both help and autocomplete.

    ``completions`` lists the full strings the autocomplete should offer when the
    user has typed the command's prefix — including the bare command and any
    subcommand/argument forms. e.g. /mode offers "/mode confirm", "/mode plan",
    "/mode auto". Kept as the ONE source of truth so handle_slash, /help, and the
    TUI suggester can never drift apart.
    """

    name: str  # the bare command, e.g. "/mode"
    summary: str  # one-line description for /help
    completions: list[str] = field(default_factory=list)  # full strings to suggest
    aliases: tuple[str, ...] = ()  # alternate names (e.g. /quit for /exit)


# The single source of truth for all slash commands. handle_slash() below resolves
# against the same names listed here; /help renders from this; the TUI suggester
# feeds its completions list from this. Add a command here and all three update.
SLASH_COMMANDS: list[SlashCommand] = [
    SlashCommand("/help", "show this help", ["/help"]),
    SlashCommand("/exit", "exit the REPL", ["/exit"], aliases=("/quit",)),
    SlashCommand("/skills", "list skills (★ = active)", ["/skills", "/skills install"]),
    SlashCommand("/clear", "reset context (new session + clear active skills)", ["/clear"]),
    SlashCommand("/config", "show current configuration", ["/config"]),
    SlashCommand("/setup", "reconfigure provider/model (onboarding wizard)", ["/setup"]),
    SlashCommand(
        "/profile",
        "switch between saved provider profiles",
        ["/profile", "/profile list"],
    ),
    SlashCommand("/sessions", "list recent sessions", ["/sessions"]),
    SlashCommand("/resume", "resume a past session (opens an interactive picker)", ["/resume "]),
    SlashCommand("/export", "export current session to a markdown file", ["/export "]),
    SlashCommand(
        "/mode",
        "change permission mode",
        ["/mode plan", "/mode confirm", "/mode auto_edit", "/mode full", "/mode auto"],
    ),
    SlashCommand("/model", "switch model at runtime", ["/model "]),
    SlashCommand("/cost", "show token usage for this session", ["/cost"]),
    SlashCommand(
        "/undo",
        "revert the last agent file write (write_file/edit_file/multi_edit)",
        ["/undo"],
    ),
    SlashCommand("/think", "expand the last round's collapsed thinking", ["/think"]),
]


def slash_completions(extra: list[str] | None = None) -> list[str]:
    """Flatten all completion candidates (commands + aliases + subcommands).

    Used by the TUI SuggestFromList. Aliases are included so /quit completes too.
    ``extra`` carries custom command completions (/name form) discovered at
    startup — passed in rather than discovered here so this stays a pure
    function over its arguments (and import-time cheap).
    """
    out: list[str] = []
    for c in SLASH_COMMANDS:
        out.extend(c.completions)
        out.extend(c.aliases)
    if extra:
        out.extend(extra)
    return out


def slash_descriptions() -> dict[str, str]:
    """Map every completion candidate (incl. aliases/subcommand forms) to its
    command's one-line description, for the TUI autocomplete menu's second
    column. Same single source of truth as slash_completions — never drifts."""
    out: dict[str, str] = {}
    for c in SLASH_COMMANDS:
        for cand in [*c.completions, *c.aliases]:
            out[cand] = c.summary
    return out


@dataclass
class ReplContext:
    """Snapshot of REPL state needed by command handlers."""

    available_skills: list[str]
    active_skills_names: set[str]
    permission_mode: str
    new_permission_mode: str = ""
    model_name: str = ""
    provider_id: str = ""
    api_key: str = ""
    base_url: str = ""
    recent_sessions: list[str] = None
    session_save_dir: str = ""  # expanded path to the sessions dir (for /sessions summaries)
    session: object = None  # the current Session object (for /export)
    profiles: list = None  # list[Profile] — saved named profiles
    active_profile: str = ""  # name of the currently active profile
    usage: dict = None
    stream: object = None  # RichStream — for /think to expand collapsed thinking
    custom_commands: dict = None  # {name: CustomCommand} — discovered at startup, rendered by /help


@dataclass
class CommandResult:
    continue_loop: bool = True
    reset_runtime: bool = False
    new_permission_mode: str = ""
    new_session_id: str = ""  # for /resume: the session id to load ("" = none)
    message: str | None = None


def _help_text(ctx=None) -> str:
    """Build /help from SLASH_COMMANDS so the listing never drifts from the
    actual command handlers. Aliases are joined with the primary name.
    Custom commands (ctx.custom_commands) render as a second section so users
    can discover what a repo ships without listing .coderio/commands/."""
    names: dict[str, SlashCommand] = {}
    for c in SLASH_COMMANDS:
        key = c.name
        if c.aliases:
            key = f"{c.name} | {' | '.join(c.aliases)}"
        names[key] = c
    width = max(len(k) for k in names)
    lines = ["coderio slash commands:"]
    for key, c in names.items():
        lines.append(f"  {key:<{width}}  {c.summary}")
    customs = getattr(ctx, "custom_commands", None) or {}
    if customs:
        lines.append("")
        lines.append("custom commands (.coderio/commands):")
        cwidth = max(len(f"/{n}") for n in customs)
        for n, cc in sorted(customs.items()):
            desc = cc.description or "(no description)"
            lines.append(f"  /{n:<{cwidth - 1}}  {desc}  [dim]({cc.source_layer})[/dim]")
    return "\n".join(lines)


def _cmd_help(ctx) -> CommandResult:
    return CommandResult(message=_help_text(ctx))


def _cmd_skills(ctx) -> CommandResult:
    lines = []
    for name in ctx.available_skills:
        mark = "★" if name in ctx.active_skills_names else " "
        lines.append(f"  {mark} {name}")
    return CommandResult(message="Skills (★ = active):\n" + "\n".join(lines))


def _cmd_config(ctx) -> CommandResult:
    base_url = ctx.base_url
    if ctx.provider_id:
        from coderio.cli.providers import get_provider

        info = get_provider(ctx.provider_id)
        if info is not None and info.base_url:
            base_url = info.base_url
    lines = [
        f"  provider: {ctx.provider_id or '(none)'}",
        f"  model:    {ctx.model_name}",
        f"  base_url: {base_url or '(default)'}",
        f"  key:      {mask_key(ctx.api_key)}",
        f"  mode:     {ctx.permission_mode}",
    ]
    return CommandResult(message="Configuration:\n" + "\n".join(lines))


def _cmd_sessions(ctx) -> CommandResult:
    """List recent sessions with a preview (first user message), not bare IDs.

    Uses Session.summaries() — the same machinery the /resume picker uses —
    so the user recognizes a session by what they asked, not by an opaque
    timestamp like '20260703-093941-b9f7'.
    """
    if not ctx.recent_sessions:
        return CommandResult(message="No sessions yet.")
    from coderio.session.store import Session

    save_dir = ctx.session_save_dir or "~/.coderio/sessions"
    summaries = Session.summaries(save_dir, limit=len(ctx.recent_sessions))
    if not summaries:
        # Fallback: show bare IDs if summaries failed (corrupt dir, etc.)
        lines = [f"  [{i}] {sid}" for i, sid in enumerate(ctx.recent_sessions)]
        return CommandResult(message="Recent sessions:\n" + "\n".join(lines))
    lines = []
    for i, s in enumerate(summaries):
        preview = s.get("first_user", "") or "(no user message)"
        meta_parts = []
        if s.get("model"):
            meta_parts.append(s["model"])
        meta_parts.append(f"{s.get('message_count', 0)} msgs")
        meta_parts.append(s.get("mtime", ""))
        meta = "  [dim]" + " · ".join(meta_parts) + "[/dim]"
        lines.append(f"  [{i}] {preview[:70]}\n{meta}")
    return CommandResult(message="Recent sessions:\n" + "\n".join(lines))


def _cmd_resume(ctx, arg: str) -> CommandResult:
    """Resume a prior session.

    Modeled on Claude Code's /resume: with no argument it opens an INTERACTIVE
    picker (the caller — TUI — detects the __OPEN_PICKER__ signal and shows a
    scrollable list with summaries, not bare ids). Nobody remembers a session id
    like '20260703-093941-b9f7', so typing it is a fallback, not the main path.
    """
    arg = arg.strip()
    if not arg:
        if not ctx.recent_sessions:
            return CommandResult(message="No sessions to resume. Run something first.")
        # Signal the TUI to open its interactive picker.
        return CommandResult(message="__OPEN_PICKER__")
    # Explicit id fallback (rare; the picker is the intended path).
    sid = next((s for s in ctx.recent_sessions if s == arg), None)
    if sid is None:
        matches = [s for s in ctx.recent_sessions if s.startswith(arg)]
        if len(matches) == 1:
            sid = matches[0]
        elif len(matches) > 1:
            return CommandResult(
                message=f"id 前缀 {arg!r} 匹配多个会话:\n  "
                + "\n  ".join(matches)
                + "\n请用更完整的前缀或直接 /resume 用选择器。"
            )
        else:
            return CommandResult(message=f"找不到会话 {arg!r}。/resume 打开选择器挑选。")
    return CommandResult(
        new_session_id=sid,
        message=f"已切到会话 {sid}。",
    )


def _cmd_mode(ctx, arg: str) -> CommandResult:
    mode = arg.strip()
    # /mode with no argument → open the visual picker (like /profile does).
    if not mode:
        return CommandResult(message="__OPEN_MODE_PICKER__")
    valid_modes = {"plan", "confirm", "auto_edit", "full", "auto"}
    if mode not in valid_modes:
        return CommandResult(message=f"Invalid mode {mode!r}. Use: plan | confirm | auto_edit | full")
    return CommandResult(
        reset_runtime=True,
        new_permission_mode=mode,
        message=f"Switched to {mode} mode.",
    )


def _cmd_profile(ctx, arg: str) -> CommandResult:
    """Switch between saved provider profiles.

    With no argument (or anything other than 'list'): signal the TUI to open
    the interactive ProfilePickerScreen — a ListView of profiles with the active
    one marked ★, same UX as /resume's session picker. With 'list': print the
    profiles inline (no popup) for a quick glance.
    """
    profiles = ctx.profiles or []
    if not profiles:
        return CommandResult(message="还没有保存的 profile。用 /setup 添加一个配置。")
    if arg.strip() == "list":
        lines = []
        for p in profiles:
            mark = "★" if p.name == ctx.active_profile else " "
            lines.append(f"  {mark} {p.name}  [dim]{p.provider_id} · {p.model}[/dim]")
        return CommandResult(message="Profiles (★ = active):\n" + "\n".join(lines))
    # Signal the TUI to open its interactive picker.
    return CommandResult(message="__OPEN_PROFILE_PICKER__")


def _cmd_clear(ctx) -> CommandResult:
    return CommandResult(reset_runtime=True, message="Context cleared (new session).")


def _cmd_export(ctx, arg: str) -> CommandResult:
    """Export the current session to a markdown file.

    Renders the conversation (user/assistant/tool messages, skipping system
    metadata) as markdown. Tool calls are shown as collapsible-ish code blocks.
    """
    session = ctx.session
    if session is None or not getattr(session, "messages", None):
        return CommandResult(message="No conversation to export yet.")

    # Determine output path: explicit arg, or {session_id}.md in CWD.
    from pathlib import Path

    if arg.strip():
        out_path = Path(arg.strip())
    else:
        sid = getattr(session, "id", "session")
        out_path = Path.cwd() / f"{sid}.md"

    lines: list[str] = []
    model = getattr(session, "meta", {}).get("model", "")
    if model:
        lines.append(f"*Model: {model}*\n")
    for m in session.messages:
        if m.role == "system":
            continue  # skip phase_timeline / context_summary metadata
        if m.role == "user":
            content = m.content if isinstance(m.content, str) else str(m.content)
            lines.append(f"## 🧑 User\n\n{content}\n")
        elif m.role == "assistant":
            content = m.content if isinstance(m.content, str) else str(m.content)
            lines.append(f"## 🤖 Assistant\n\n{content}\n")
            if m.tool_calls:
                for tc in m.tool_calls:
                    name = tc.get("name", "?") if isinstance(tc, dict) else getattr(tc, "name", "?")
                    lines.append(f"  *🔧 {name}*\n")
        elif m.role == "tool":
            name = getattr(m, "name", "tool")
            content = (m.content or "")[:500]  # cap tool output for readability
            lines.append(f"<details><summary>🔧 {name}</summary>\n\n```\n{content}\n```\n\n</details>\n")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return CommandResult(message=f"Exported {len(session.messages)} messages → {out_path}")


def handle_slash(line: str, ctx) -> CommandResult:
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0]
    arg = parts[1] if len(parts) > 1 else ""
    if cmd in ("/exit", "/quit"):
        return CommandResult(continue_loop=False, message="bye.")
    if cmd == "/help":
        return _cmd_help(ctx)
    if cmd == "/skills":
        if arg.strip() == "install":
            return CommandResult(reset_runtime=True, message="__SKILLS_INSTALL__")
        return _cmd_skills(ctx)
    if cmd == "/config":
        return _cmd_config(ctx)
    if cmd == "/sessions":
        return _cmd_sessions(ctx)
    if cmd == "/resume":
        return _cmd_resume(ctx, arg)
    if cmd == "/mode":
        return _cmd_mode(ctx, arg)
    if cmd == "/profile":
        return _cmd_profile(ctx, arg)
    if cmd == "/clear":
        return _cmd_clear(ctx)
    if cmd == "/export":
        return _cmd_export(ctx, arg)
    if cmd == "/model":
        name = arg.strip()
        if not name:
            return CommandResult(message=f"当前模型: {ctx.model_name}")
        return CommandResult(reset_runtime=True, message=f"已切换模型 → {name}（下一轮生效）。")
    if cmd == "/cost":
        u = ctx.usage or {}
        inp = u.get("input_tokens", 0)
        out = u.get("output_tokens", 0)
        if inp == 0 and out == 0:
            return CommandResult(message="本次会话暂无 token 用量(尚未对话或 provider 未返回)。")
        return CommandResult(message=f"Token 用量:\n  输入: {inp}\n  输出: {out}\n  合计: {inp + out}")
    if cmd == "/undo":
        from coderio.tools.checkpoint import DEFAULT_CHECKPOINT

        try:
            result = DEFAULT_CHECKPOINT.undo()
        except OSError as e:
            # e.g. the path an entry guards has become a directory — the
            # entry is already popped; report instead of crashing the TUI.
            return CommandResult(message=f"撤销失败: {e}")
        if result is None:
            return CommandResult(message="没有可撤销的文件写入（栈为空）。")
        verb = "已恢复写入前的原内容" if result.restored else "已删除（该文件由 agent 新建）"
        remaining = len(DEFAULT_CHECKPOINT)
        suffix = f"（还可撤销 {remaining} 步）" if remaining else "（已到底）"
        return CommandResult(message=f"↩ {result.path} {verb}{suffix}")
    if cmd == "/think":
        stream = getattr(ctx, "stream", None)
        if stream is not None and hasattr(stream, "show_last_thinking"):
            stream.show_last_thinking()
            return CommandResult(message=None)
        return CommandResult(message="当前无思考内容可展开。")
    if cmd == "/setup":
        # Signal the TUI to open the OnboardingScreen (same wizard as first run).
        return CommandResult(message="__OPEN_ONBOARDING__")
    return CommandResult(message=f"Unknown command: {cmd}. Type /help.")
