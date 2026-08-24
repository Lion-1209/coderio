"""Custom slash commands: ``.coderio/commands/*.md`` (project) + ``~/.coderio/commands/*.md`` (user).

A command file's filename (minus .md) is the command name — ``review.md``
defines ``/review``. Optional frontmatter carries a description for /help and
autocomplete; the body is the PROMPT TEMPLATE sent to the model when invoked.
``$ARGUMENTS`` in the body is replaced with whatever the user typed after the
command (appended as a separate paragraph if the template lacks the
placeholder, so arguments are never silently dropped).

Layering mirrors the skills store: the project layer overrides the user layer
on name conflict (closer/more specific wins). Built-in slash commands always
beat same-named customs — shadowing /help or /exit would be an accident trap.

Security note: command bodies are prompt TEXT loaded from the repo — they are
sent to the model as a user message and are NEVER re-dispatched into the
built-in slash handler (the TUI wires expansion with `elif`, so a body of
"/mode full" is prompt text, not a permission change). They execute no code;
a hostile repo can at worst inject prompt content, which the skills trust gate
already treats as in-scope risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coderio.cli.commands import SLASH_COMMANDS
from coderio.skills.parser import split_frontmatter

ARGUMENTS_PLACEHOLDER = "$ARGUMENTS"
# Prompt templates are small by nature; anything near this size is a token
# bomb (a 10MB body loaded in tests would stream straight at the provider).
MAX_COMMAND_BYTES = 128 * 1024

# Built-in names + aliases (help/exit/quit/...). Derived from the single
# source of truth so adding a built-in can never be silently shadowed by a
# repo shipping a same-named command file.
_BUILTIN_NAMES = frozenset(name.lstrip("/") for c in SLASH_COMMANDS for name in (c.name, *c.aliases))
# Case variants too: on Windows (case-insensitive FS) HELP.md is reachable as
# /HELP while /help stays built-in — a near-miss spoof surface. Dropping
# case-collisions keeps Linux and Windows behavior identical.
_BUILTIN_NAMES_CI = frozenset(n.lower() for n in _BUILTIN_NAMES)


@dataclass(frozen=True)
class CustomCommand:
    """One discovered custom command."""

    name: str  # without the leading slash, e.g. "review"
    description: str  # frontmatter description; "" if absent
    body: str  # prompt template ($ARGUMENTS substituted at invoke time)
    source_layer: str  # "user" | "project" — shown in /help


def _load_layer(layer_dir: Path | None, layer_name: str) -> dict[str, CustomCommand]:
    out: dict[str, CustomCommand] = {}
    if not layer_dir:
        return out
    layer_dir = Path(layer_dir)
    if not layer_dir.is_dir():
        return out
    for md in sorted(layer_dir.glob("*.md")):
        # Name comes from the FILENAME, not frontmatter — the user typed the
        # path to invoke it, so what they see in their file tree must be what
        # they type. Frontmatter 'name' is ignored by design.
        name = md.stem
        try:
            if md.stat().st_size > MAX_COMMAND_BYTES:
                continue
            raw = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # NUL is valid UTF-8 and survives errors="replace", but providers 400
        # on it — strip rather than let one hostile file poison the session.
        raw = raw.replace("\x00", "")
        fm, body = split_frontmatter(raw)
        body = body.strip()
        if not body:
            continue  # empty template would send nothing — skip silently
        if any(ch.isspace() for ch in name):
            # "/my cmd" can never be typed (args split on whitespace), so a
            # space-named file would be a dead entry advertised in /help and
            # the completion menu — skip it instead of advertising a lie.
            continue
        out[name] = CustomCommand(
            name=name,
            description=fm.get("description", ""),
            body=body,
            source_layer=layer_name,
        )
    return out


def discover_custom_commands(
    project_dir: Path | str | None = None,
    user_dir: Path | str | None = None,
) -> dict[str, CustomCommand]:
    """Discover custom commands from both layers.

    Both params are the LAYER DIRECTORIES whose immediate ``*.md`` children are
    command files (e.g. ``<project>/.coderio/commands``) — NOT a project root.
    Callers join the anchor themselves, mirroring load_skill_store's convention.
    Project layer overrides user layer on name conflict. Returns a dict keyed
    by bare command name ("review" for /review).
    """
    merged = _load_layer(Path(user_dir) if user_dir else None, "user")
    merged.update(_load_layer(Path(project_dir) if project_dir else None, "project"))
    # Drop case-variants of built-in names (HELP.md vs /help) — see
    # _BUILTIN_NAMES_CI above for why this is done at merge time.
    return {k: v for k, v in merged.items() if k.lower() not in _BUILTIN_NAMES_CI}


def expand_command(cmd: CustomCommand, args: str) -> str:
    """Substitute the user's arguments into the prompt template.

    With a $ARGUMENTS placeholder it is replaced directly. Without one, the
    args are APPENDED as a final paragraph — dropping them silently would make
    ``/review foo.py`` behave identically to bare ``/review``, which reads as a
    bug from the user's seat.
    """
    args = args.strip()
    if ARGUMENTS_PLACEHOLDER in cmd.body:
        return cmd.body.replace(ARGUMENTS_PLACEHOLDER, args)
    if not args:
        return cmd.body
    return f"{cmd.body}\n\n{args}"


def try_expand_line(line: str, commands: dict[str, CustomCommand]) -> str | None:
    """Expand ``/name args`` to its prompt template, or return None.

    None means "not a custom command" — either not a slash line at all, an
    unknown command, or a BUILT-IN name (built-ins always win; shadowing /help
    or /exit via a repo file would be an accident trap). Returns the expanded
    prompt text for the caller to send as a normal user message.
    """
    if not line.startswith("/"):
        return None
    parts = line.strip().split(maxsplit=1)
    name = parts[0][1:]
    if not name or name in _BUILTIN_NAMES:
        return None
    cc = commands.get(name)
    if cc is None:
        return None
    out = expand_command(cc, parts[1] if len(parts) > 1 else "")
    # Degenerate template ("$ARGUMENTS" alone, invoked bare) expands to "".
    # Returning None routes to "Unknown command" instead of sending an empty
    # user message to the model.
    return out if out.strip() else None
