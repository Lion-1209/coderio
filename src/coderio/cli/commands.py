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
    name: str          # the bare command, e.g. "/mode"
    summary: str       # one-line description for /help
    completions: list[str] = field(default_factory=list)  # full strings to suggest
    aliases: tuple[str, ...] = ()  # alternate names (e.g. /quit for /exit)


# The single source of truth for all slash commands. handle_slash() below resolves
# against the same names listed here; /help renders from this; the TUI suggester
# feeds its completions list from this. Add a command here and all three update.
SLASH_COMMANDS: list[SlashCommand] = [
    SlashCommand("/help", "show this help", ["/help"]),
    SlashCommand("/exit", "exit the REPL", ["/exit"], aliases=("/quit",)),
    SlashCommand("/skills", "list skills (★ = active)",
                 ["/skills", "/skills install"]),
    SlashCommand("/clear", "reset context (new session + clear active skills)",
                 ["/clear"]),
    SlashCommand("/config", "show current configuration", ["/config"]),
    SlashCommand("/sessions", "list recent sessions", ["/sessions"]),
    SlashCommand("/mode", "change permission mode",
                 ["/mode confirm", "/mode plan", "/mode auto"]),
    SlashCommand("/model", "switch model at runtime", ["/model "]),
    SlashCommand("/cost", "show token usage for this session", ["/cost"]),
    SlashCommand("/think", "expand the last round's collapsed thinking", ["/think"]),
]


def slash_completions() -> list[str]:
    """Flatten all completion candidates (commands + aliases + subcommands).

    Used by the TUI SuggestFromList. Aliases are included so /quit completes too.
    """
    out: list[str] = []
    for c in SLASH_COMMANDS:
        out.extend(c.completions)
        out.extend(c.aliases)
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
    usage: dict = None
    stream: object = None  # RichStream — for /think to expand collapsed thinking


@dataclass
class CommandResult:
    continue_loop: bool = True
    reset_runtime: bool = False
    new_permission_mode: str = ""
    message: str | None = None


def _help_text() -> str:
    """Build /help from SLASH_COMMANDS so the listing never drifts from the
    actual command handlers. Aliases are joined with the primary name."""
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
    return "\n".join(lines)


def _cmd_help(ctx) -> CommandResult:
    return CommandResult(message=_help_text())


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
    if not ctx.recent_sessions:
        return CommandResult(message="No sessions yet.")
    lines = [f"  [{i}] {sid}" for i, sid in enumerate(ctx.recent_sessions)]
    return CommandResult(message="Recent sessions:\n" + "\n".join(lines))


def _cmd_mode(ctx, arg: str) -> CommandResult:
    mode = arg.strip()
    if mode not in {"confirm", "auto", "plan"}:
        return CommandResult(message=f"Invalid mode {mode!r}. Use: confirm | plan | auto")
    return CommandResult(
        reset_runtime=True,
        new_permission_mode=mode,
        message=f"Switched to {mode} mode.",
    )


def _cmd_clear(ctx) -> CommandResult:
    return CommandResult(reset_runtime=True, message="Context cleared (new session).")


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
    if cmd == "/mode":
        return _cmd_mode(ctx, arg)
    if cmd == "/clear":
        return _cmd_clear(ctx)
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
    if cmd == "/think":
        stream = getattr(ctx, "stream", None)
        if stream is not None and hasattr(stream, "show_last_thinking"):
            stream.show_last_thinking()
            return CommandResult(message=None)
        return CommandResult(message="当前无思考内容可展开。")
    return CommandResult(message=f"Unknown command: {cmd}. Type /help.")
