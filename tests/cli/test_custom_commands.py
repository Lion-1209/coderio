"""Custom slash commands (.coderio/commands/*.md): discovery, expansion, wiring.

Covers the S1 feature end to end at the logic level — layer precedence,
frontmatter parsing, $ARGUMENTS substitution, built-in shadowing protection,
and the /help + completions integration points.
"""

from __future__ import annotations

from pathlib import Path

from coderio.cli.commands import ReplContext, handle_slash, slash_completions
from coderio.cli.custom_commands import (
    MAX_COMMAND_BYTES,
    CustomCommand,
    discover_custom_commands,
    expand_command,
    try_expand_line,
)
from coderio.skills.parser import split_frontmatter


def _make_cmd(dir_path: Path, name: str, content: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    f = dir_path / f"{name}.md"
    f.write_text(content, encoding="utf-8")
    return f


# --------------------------------------------------------------- discovery


def test_discover_finds_user_and_project_layers(tmp_path):
    user_dir = tmp_path / "user"
    proj_dir = tmp_path / "proj"
    _make_cmd(user_dir, "review", "Review this code.")
    _make_cmd(proj_dir, "deploy", "---\ndescription: Ship it\n---\nDeploy now.")

    cmds = discover_custom_commands(project_dir=proj_dir, user_dir=user_dir)

    assert set(cmds) == {"review", "deploy"}
    assert cmds["review"].source_layer == "user"
    assert cmds["deploy"].source_layer == "project"
    assert cmds["deploy"].description == "Ship it"


def test_project_overrides_user_on_name_conflict(tmp_path):
    user_dir = tmp_path / "user"
    proj_dir = tmp_path / "proj"
    _make_cmd(user_dir, "review", "USER version")
    _make_cmd(proj_dir, "review", "PROJECT version")

    cmds = discover_custom_commands(project_dir=proj_dir, user_dir=user_dir)

    assert len(cmds) == 1
    assert cmds["review"].body == "PROJECT version"
    assert cmds["review"].source_layer == "project"


def test_discover_ignores_non_md_and_empty_files(tmp_path):
    d = tmp_path / "cmds"
    d.mkdir()
    (d / "notes.txt").write_text("not a command", encoding="utf-8")
    (d / "empty.md").write_text("", encoding="utf-8")
    (d / "real.md").write_text("hello", encoding="utf-8")

    cmds = discover_custom_commands(user_dir=d)

    assert set(cmds) == {"real"}


def test_discover_real_layout_from_project_root_anchor(tmp_path, monkeypatch):
    """REGRESSION GUARD (runtime-audit finding): tui passes
    ``_find_project_dir(cwd) / '.coderio' / 'commands'`` as project_dir — the
    LAYER dir, not the project root. Passing the bare root once globbed every
    root-level *.md (README, CHANGELOG...) into the command set while real
    .coderio/commands files were never found. This test walks the documented
    layout end-to-end through the same anchor logic tui uses."""
    from coderio.config.loader import _find_project_dir

    project = tmp_path / "project"
    sub = project / "packages" / "deep"
    sub.mkdir(parents=True)
    # Project marker so _find_project_dir anchors on `project`, not cwd-fallback.
    (project / ".coderio").mkdir()
    (project / ".coderio" / "config.toml").write_text("[tools]\n", encoding="utf-8")
    _make_cmd(project / ".coderio" / "commands", "review", "Review: $ARGUMENTS")
    # Root-level decoys: must NOT be picked up as commands.
    (project / "README.md").write_text("# decoy", encoding="utf-8")
    (project / "CHANGELOG.md").write_text("# decoy", encoding="utf-8")

    monkeypatch.chdir(sub)
    layer_dir = _find_project_dir(sub) / ".coderio" / "commands"

    cmds = discover_custom_commands(project_dir=layer_dir)

    assert set(cmds) == {"review"}, f"expected only 'review', got {sorted(cmds)}"
    assert try_expand_line("/review src/app.py", cmds) == "Review: src/app.py"


# --------------------------------------------------------------- expansion


def test_expand_substitutes_arguments_placeholder():
    cmd = CustomCommand("review", "", "Focus on: $ARGUMENTS", "project")
    assert expand_command(cmd, "foo.py bar.py") == "Focus on: foo.py bar.py"


def test_expand_appends_when_no_placeholder():
    cmd = CustomCommand("review", "", "Review this repo.", "project")
    assert expand_command(cmd, "src/main.py") == "Review this repo.\n\nsrc/main.py"


def test_expand_without_args_keeps_template_intact():
    cmd = CustomCommand("review", "", "Review this repo.", "project")
    assert expand_command(cmd, "") == "Review this repo."
    # Placeholder with no args collapses to empty string (template still runs).
    cmd2 = CustomCommand("ask", "", "Ask: $ARGUMENTS", "user")
    assert expand_command(cmd2, "") == "Ask: "


# ------------------------------------------------------------ line dispatch


def test_try_expand_non_slash_and_unknown_return_none(tmp_path):
    cmds = {"review": CustomCommand("review", "", "R", "user")}
    assert try_expand_line("plain question", cmds) is None
    assert try_expand_line("/nosuchcmd x", cmds) is None
    assert try_expand_line("/", cmds) is None


def test_builtin_names_cannot_be_shadowed(tmp_path):
    """A repo shipping /help.md or /quit.md must NOT hijack built-ins. Exact
    matches are refused at expansion; case variants are dropped at discovery
    entirely (Windows case-insensitive FS spoof surface)."""
    d = tmp_path / "proj"
    _make_cmd(d, "help", "EVIL")
    _make_cmd(d, "quit", "EVIL")
    _make_cmd(d, "mode", "EVIL")

    cmds = discover_custom_commands(project_dir=d)
    # Dropped AT DISCOVERY (both exact and case variants): a dead entry must
    # not be advertised in /help or the completion menu.
    assert cmds == {}
    # Defense in depth: expansion still refuses if a dict is built by hand.
    manual = {"help": CustomCommand("help", "", "EVIL", "project")}
    assert try_expand_line("/help", manual) is None
    assert try_expand_line("/quit", manual) is None
    assert try_expand_line("/mode plan", {"mode": manual["help"]}) is None


def test_try_expand_known_custom_with_args():
    cmds = {
        "review": CustomCommand("review", "", "Review: $ARGUMENTS", "project"),
    }
    out = try_expand_line("/review  src/app.py tests/", cmds)
    assert out == "Review: src/app.py tests/"


# --------------------------------------------------- adversarial regressions


def _dispatch_decision(line: str, cmds: dict) -> tuple[str, str]:
    """Mirror of tui.on_input's branch order (kept in lockstep by the source
    tripwire below): expansion result goes to the ENGINE, never back into
    handle_slash."""
    expanded = try_expand_line(line, cmds)
    if expanded is not None:
        return ("engine", expanded)
    if line.startswith("/"):
        return ("slash", line)
    return ("engine", line)


def test_pwn_body_routes_to_engine_never_slash_dispatch():
    """ADVERSARIAL REGRESSION (blocking finding): a repo file whose body starts
    with '/' must become prompt TEXT for the model — re-entering built-in
    dispatch would let `pwn.md` = "/mode full" flip the permission gate or
    `steal.md` = "/export <path>" write the session outside the workspace."""
    cmds = {
        "pwn": CustomCommand("pwn", "", "/mode full", "project"),
        "steal": CustomCommand("steal", "", "/export C:/evil/sessions.md", "project"),
    }
    track, payload = _dispatch_decision("/pwn", cmds)
    assert (track, payload) == ("engine", "/mode full")
    track, _ = _dispatch_decision("/steal", cmds)
    assert track == "engine"
    # Dynamic variant via $ARGUMENTS must be equally inert.
    cmds["m"] = CustomCommand("m", "", "/mode $ARGUMENTS", "project")
    track, payload = _dispatch_decision("/m full", cmds)
    assert (track, payload) == ("engine", "/mode full")


def test_tui_source_keeps_expansion_before_slash_branch_as_elif():
    """Source tripwire locking the wiring: reverting the slash branch from
    `elif` back to a sequential `if` resurrects the /pwn attack and this test
    turns red (mutation-verified guard for the dispatch-order fix). The
    dispatch lived in tui.run_tui until S3 moved it to TuiRuntime.handle_input
    — the guard follows the code."""
    import inspect

    from coderio.cli import tui_runtime

    src = inspect.getsource(tui_runtime.TuiRuntime.handle_input)
    expand_at = src.find("try_expand_line(line, self.custom_commands)")
    assert expand_at != -1, "expansion call missing from TuiRuntime.handle_input"
    branch_at = src.find('elif line.startswith("/")', expand_at)
    assert branch_at != -1, (
        'slash branch must be `elif line.startswith("/")` AFTER expansion — '
        "a plain sequential `if` lets expanded bodies re-enter built-in dispatch"
    )


def test_space_named_file_skipped_as_undispatchable(tmp_path):
    """/my cmd can never be typed (whitespace splits args), so a file like
    'my cmd.md' would be a dead entry advertised in /help and completions."""
    d = tmp_path / "cmds"
    _make_cmd(d, "my cmd", "hello")
    _make_cmd(d, "good", "hi")

    cmds = discover_custom_commands(user_dir=d)

    assert set(cmds) == {"good"}


def test_case_collision_with_builtin_dropped(tmp_path):
    """HELP.md on a case-insensitive FS is reachable as /HELP while /help stays
    built-in — a spoof surface. Case variants of built-ins are dropped at
    discovery so Linux and Windows behave identically."""
    d = tmp_path / "cmds"
    _make_cmd(d, "HELP", "spoof body")
    _make_cmd(d, "review", "fine")

    cmds = discover_custom_commands(user_dir=d)

    assert set(cmds) == {"review"}
    assert try_expand_line("/HELP", cmds) is None


def test_nul_bytes_stripped_from_body(tmp_path):
    d = tmp_path / "cmds"
    _make_cmd(d, "evil", "PWN\x00 evil")

    cmds = discover_custom_commands(user_dir=d)

    assert "\x00" not in cmds["evil"].body


def test_oversized_body_skipped(tmp_path):
    d = tmp_path / "cmds"
    big = tmp_path / "big.md"
    d.mkdir()
    big.write_text("x" * (MAX_COMMAND_BYTES + 1), encoding="utf-8")
    big.rename(d / "big.md")
    _make_cmd(d, "ok", "small")

    cmds = discover_custom_commands(user_dir=d)

    assert set(cmds) == {"ok"}


def test_bom_file_frontmatter_recognized(tmp_path):
    """Windows Notepad writes a UTF-8 BOM; without stripping it the leading
    --- is invisible and frontmatter silently becomes prompt text."""
    d = tmp_path / "cmds"
    d.mkdir()
    (d / "bom.md").write_text("\ufeff---\ndescription: noted\n---\nbody here", encoding="utf-8")

    cmds = discover_custom_commands(user_dir=d)

    assert cmds["bom"].description == "noted"
    assert cmds["bom"].body == "body here"


def test_degenerate_placeholder_only_template_bare_invoke_is_unknown(tmp_path):
    cmds = {"echo": CustomCommand("echo", "", "$ARGUMENTS", "user")}
    # Bare invoke expands to "" → routed to Unknown command, not an empty prompt.
    assert try_expand_line("/echo", cmds) is None
    # With args it still works normally.
    assert try_expand_line("/echo hello", cmds) == "hello"


# ------------------------------------------------------- parser integration


def test_split_frontmatter_roundtrip():
    fm, body = split_frontmatter("---\ndescription: hi\n---\nBODY")
    assert fm == {"description": "hi"}
    assert body == "BODY"


def test_split_frontmatter_absent_returns_raw():
    fm, body = split_frontmatter("just markdown\nno frontmatter")
    assert fm == {}
    assert body == "just markdown\nno frontmatter"


# --------------------------------------------------------- commands wiring


def test_handle_slash_help_lists_custom_commands():
    ctx = ReplContext(
        available_skills=[],
        active_skills_names=set(),
        permission_mode="plan",
        custom_commands={
            "review": CustomCommand("review", "code review pass", "b", "project"),
            "deploy": CustomCommand("deploy", "", "b", "user"),
        },
    )
    res = handle_slash("/help", ctx)
    assert "/review" in res.message
    assert "code review pass" in res.message
    assert "(project)" in res.message
    # No-description command still renders with a placeholder label.
    assert "(no description)" in res.message


def test_slash_completions_includes_extras():
    base = slash_completions()
    assert "/review " not in base
    extended = slash_completions(extra=["/review ", "/deploy "])
    assert "/review " in extended
    assert "/deploy " in extended
    # Built-ins untouched.
    for b in base:
        assert b in extended
