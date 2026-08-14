"""Harness state control for the single-agent loop (spec: 2026-06-26-coderio-harness-state-control).

This module turns the "write code then claim done without verifying" failure mode
from a soft prompt rule into a STRUCTURAL constraint. The harness sits inside
``run_deep_agent`` and controls the one thing the model cannot override: whether a
"no tool_calls" response actually TERMINATES the turn, or gets force-continued with
an injected message. All decisions are based on observed tool calls/results
(ground truth), never on the model's self-report.

Four gates (spec §1):
  * PlanGate (soft)       — nudge to decompose before writing, when no todos exist.
  * VerifyGate (hard)     — block "done" while code is written-but-not-run.
  * CompletionGate (hard) — block "done" while non-trivial todos remain pending.
  * GroundingGate (hard)  — block "done" when the model cites code locations it
                            never actually read (analyses built on assumptions,
                            not evidence). Guards the ANALYZE/QA failure mode where
                            the model trusts documentation or memory over source.

VerifyGate + CompletionGate + GroundingGate use progressive escalation:
force-continue twice, then release with a visible warning (never silently, never
infinite-loop).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from coderio.tools.todo import TodoStore

# Tools that mutate files on disk. A successful one of these creates an
# "unverified write" that the VerifyGate watches.
WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file", "multi_edit"})

# Read-only tools that "ground" a claim about a file — if the model cited a path
# and one of these tools touched it, the citation is evidence-backed.
READ_TOOLS: frozenset[str] = frozenset({"read_file", "grep", "list_dir", "glob"})
# Tools that actually READ FILE CONTENTS. Only these ground a citation about a
# file's internals — grep only matches a pattern (the model never sees the full
# file), and list_dir/glob return NAMES without contents. Crediting them would
# let the model cite "loader.py:81 does X" after a `grep pattern="loader"` or a
# `list_dir("src")` that only returned filenames.
CONTENT_READ_TOOLS: frozenset[str] = frozenset({"read_file"})

# Running a shell command counts as a verification attempt — even a failing one
# means the agent tried to run its code, so we stop nagging. This avoids both
# "wrote then claimed done" and infinite "it errored, keep trying" loops.
VERIFY_TOOL: str = "bash"

# After this many forced-continues a gate gives up and releases with a warning.
_MAX_GATE_ATTEMPTS: int = 2

# Match file paths the model might cite in an analysis: "loader.py", "src/x.py",
# "loader.py:81", "agent/loop.py". Requires a dotted extension so ordinary prose
# like "the loader" or "go to step 2" doesn't false-positive. Anchored to a word
# boundary on the left; the path body may include /, \, word chars, dash, dot.
_CITED_FILE_RE = re.compile(
    r"(?<![\w/.\\-])"  # not preceded by word/slash/dot/dash (prevents matching
    # `self._live.py` — the `.` before `_live` blocks it)
    r"(?:[\w./\\-]+[\\/])?"  # optional dir prefix (src/, agent\)
    r"(?:"
    r"[\w-]+\."  # basename stem + dot + extension
    r"(?:py|js|ts|tsx|jsx|md|json|toml|yaml|yml|rs|go|java|rb|sh|css|html|sql|c|cpp|h|hpp|php|swift|kt|scala|lua|vim|el)"
    r"|"
    r"(?:Dockerfile|Makefile|makefile|Gemfile|Rakefile|CMakeLists\.txt|\.env|\.gitignore|\.dockerignore|requirements\.txt|package\.json|tsconfig\.json|go\.mod|go\.sum|Cargo\.toml|Cargo\.lock)"
    r")"
    r"(?::\d+)?"  # optional :line-number
    r"(?![\w])"  # not followed by a word char
)


def _norm_path(p: str) -> str:
    """Normalize a path for read-state dedup.

    Lowercases, converts backslashes to forward slashes, collapses redundant
    './' and '../' segments, and strips whitespace. On case-insensitive
    filesystems (NTFS, APFS default) this correctly treats 'Loop.py' ==
    'loop.py'; on case-sensitive filesystems it's slightly over-eager but the
    worst case is one missed grounding check, never a false positive.

    This is the single source of truth for path comparison in the harness —
    observe() normalizes on write, _was_read() normalizes on compare, and the
    cross-turn pre-fill normalizes when seeding content_read_files.
    Without it, a model that reads 'Loop.py' then cites 'loop.py:81' gets
    force-re-read by GroundingGate (observed in real sessions: 'Loop.py' was
    read 5x in one turn purely from case drift).
    """
    if not p:
        return ""
    p = p.strip().replace("\\", "/")
    parts: list[str] = []
    for part in p.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts).lower()


def _is_success(result: str) -> bool:
    """A write tool result counts as a real write unless it errored.

    All three write tools report failures as ``"Error: ..."`` (verified against
    write_file.py / edit_file.py / multi_edit.py). A failed write changed nothing
    on disk, so it must not trigger the verify gate. Permission rejections from
    the PermissionGate start with ``"Permission denied:"`` — those also changed
    nothing, so they must not count as writes either.
    """
    if not result:
        return False
    return not (result.startswith("Error") or result.startswith("Permission denied"))


# Matches BOTH exit-code marker formats the VerifyGate may see:
#   1. ``[exit_code: N]`` — coderio's legacy BashTool format (bash.py).
#   2. ``[Command failed with exit code N]`` / ``[Command succeeded with exit
#      code N]`` — deepagents' native format (deepagents/middleware/
#      filesystem.py appends this to every execute result's content).
#
# REGRESSION (2026-08-14 report P0-1): the production engine (deepagents)
# wraps ExecuteResponse into a ToolMessage whose .exit_code attribute is
# GONE — the exit code only survives as text in the content. The regex below
# originally only matched format 1, so _parse_exit_code returned None for
# every production execute call → failed tests were treated as "neutral
# pass" → writes_since_verify was cleared → VerifyGate never blocked "done"
# after a FAILING test run. The gate (the project's headline feature) was
# completely inert in production. Both formats must match from now on.
_EXIT_CODE_RE = re.compile(r"\[exit_code:\s*(-?\d+)\]" r"|\[Command (?:succeeded|failed) with exit code (-?\d+)\]")


def _parse_exit_code(result: str) -> int | None:
    """Extract the exit code from a shell-tool result, or None if absent.

    Handles both marker formats (see _EXIT_CODE_RE): coderio's legacy
    ``\\n[exit_code: N]`` and deepagents' ``[Command failed|succeeded with
    exit code N]``. When present, a non-zero value means the verification
    FAILED (tests errored, build broke, lint failed) and must NOT clear the
    unverified-writes list.
    """
    m = _EXIT_CODE_RE.search(result or "")
    if m:
        # group(1) = coderio format, group(2) = deepagents format.
        return int(m.group(1) or m.group(2))
    return None


# File extensions that represent executable/buildable code — writing these
# triggers the VerifyGate (the model must run/test them before claiming done).
# Files NOT in this set (.md docs, .json configs, .yaml manifests, .txt notes,
# .toml, .cfg, .ini, .csv, images, etc.) skip the gate — they're verified by
# reading, not by running. This prevents the most common UX complaint: agent
# writes a .md analysis doc, harness forces pytest, pytest fails (wrong Python
# in bash env), agent concludes "environment is broken".
_VERIFIABLE_EXTS: frozenset[str] = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".rb",
        ".sh",
        ".bash",
        ".zsh",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".swift",
        ".kt",
        ".scala",
        ".lua",
        ".php",
        ".vim",
        ".sql",
        # Build files that affect compilation/testing
        ".cmake",
        ".makefile",
        # Web files that need to be tested in a browser/runtime
        ".html",
        ".css",
        ".vue",
        ".svelte",
    }
)


def _is_verifiable_code(path: str) -> bool:
    """Should writing this file trigger the VerifyGate?

    Returns True for source code files (.py, .js, .ts, etc.) and False for
    documentation/config/data files (.md, .json, .yaml, .txt, .toml, etc.).
    The gate exists to catch 'wrote code but didn't run it' — a markdown
    analysis document doesn't need pytest to verify.
    """
    import os

    ext = os.path.splitext(path)[1].lower()
    if ext in _VERIFIABLE_EXTS:
        return True
    # Special case: files with no extension but known code names
    base = os.path.basename(path).lower()
    if base in {"dockerfile", "makefile", "rakefile", "gemfile"}:
        return True
    return False


# Commands that are clearly verification (testing/linting/building code), not
# just arbitrary shell activity. Matching these means even a `pytest` or `ruff`
# without an explicit filename still counts as a real verification attempt.
_VERIFY_COMMAND_RE = re.compile(
    r"\b(pytest|python\s+-m\s+pytest|python\s+-m\s+unittest|"
    r"npm\s+(test|run)|npx\s+jest|yarn\s+test|"
    r"cargo\s+(test|build|check)|go\s+(test|build|vet)|"
    r"ruff|flake8|pylint|mypy|eslint|tsc|cargo\s+clippy|"
    r"make\s+(test|check|build)|cmake|"
    r"ruby\s+-Itest|bundle\s+exec\s+rake\s+test"
    r")\b",
    re.IGNORECASE,
)


def _command_verifies_written(command: str, written_files: list[str]) -> bool:
    """Does this bash command plausibly run or test the written code?

    Returns True if:
      - The command is a known test/lint/build tool (pytest, npm test, cargo
        test, ruff, etc.) — these verify code regardless of explicit filenames.
      - The command references a written file by basename or path — e.g.
        ``python src/foo.py`` or ``node app.js``.

    Returns False for commands that don't touch the written files at all,
    like ``echo done``, ``ls``, ``pwd``, ``git status`` — these would let the
    agent bypass VerifyGate without actually running its code.
    """
    if not command:
        return False
    # Known verification tools count even without explicit file references.
    if _VERIFY_COMMAND_RE.search(command):
        return True
    # Check if any written file's basename or path appears in the command.
    cmd_lower = command.lower()
    for f in written_files:
        basename = f.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if basename and (basename.lower() in cmd_lower or f.lower() in cmd_lower):
            return True
    return False


@dataclass
class HarnessState:
    """Per-turn harness state, derived purely from observed tool calls.

    No field here is writable by the model — it is all ground truth accumulated
    by ``Harness.observe`` as tools execute.
    """

    # File paths written since the last verification (bash). Non-empty here means
    # "there is unverified code on disk".
    writes_since_verify: list[str] = field(default_factory=list)
    # Times the verify gate has force-continued this turn (resets to 0 on bash).
    verify_attempts: int = 0
    # Times the completion gate has force-continued this turn.
    completion_attempts: int = 0
    # Whether the plan gate has nudged already this turn (nudge at most once).
    plan_nudged: bool = False
    # Whether ANY write happened this turn (write_file/edit_file). Used by
    # GroundingGate to distinguish CODE mode (writes → check citations) from
    # ANALYZE mode (pure reads → skip, analysis is allowed to mention filenames).
    # Unlike writes_since_verify, this is NOT cleared by verification.
    has_wrote_this_turn: bool = False
    # Files the model has actually READ this turn (via read_file/grep/glob/list_dir).
    # The GroundingGate checks citations against this set: claiming "loader.py:81
    # does X" without loader.py in here is an ungrounded assertion.
    read_files: set[str] = field(default_factory=set)
    # Subset of read_files opened with read_file (content-level reads). The
    # GroundingGate trusts these for file-content citations; grep/list_dir/glob
    # only land in read_files (directory/path awareness), NOT here — seeing a
    # filename in `list_dir` output doesn't mean the model read the file's
    # contents, so a citation like "loader.py:81 does X" should require an actual
    # read_file of loader.py.
    content_read_files: set[str] = field(default_factory=set)
    # Times the grounding gate has force-continued this turn.
    grounding_attempts: int = 0
    # Paths the model TRIED to read_file but got "file not found". The
    # GroundingGate skips these in its ungrounded-citation check — the regex
    # can match path-like strings in prose (e.g. '.coderio/config.toml' in a
    # README analysis) that aren't real files. Once the agent has confirmed a
    # path doesn't exist, the gate stops forcing it to "read" it again.
    not_found_files: set[str] = field(default_factory=set)


@dataclass
class Harness:
    """The structural constraint layer. Stateless across turns except via state.

    ``enabled`` short-circuits every method to a no-op/passthrough, so the loop
    can always hold a Harness object and just flip this flag (or pass harness=None).
    """

    state: HarnessState
    todos: "TodoStore"
    enabled: bool = True
    # Optional: agent state tracker + stream for phase observability. When set,
    # observe()/check_termination() derive the current AgentState from ground
    # truth and fire on_phase_change. None = phase tracking off (back-compat).
    state_tracker: Any = None
    stream: Any = None

    # ------------------------------------------------------------------ observe
    def observe(self, name: str, args: dict, result: str) -> None:
        """Update internal state from a tool execution (called after every tool).

        - successful write tool  -> record path as an unverified write
        - read tool              -> record the path/pattern as "actually read"
        - bash (any result)      -> writes are now "verified attempt", clear + reset
        """
        if not self.enabled:
            return
        just_verified = False
        if name in WRITE_TOOLS and _is_success(result):
            path = str(args.get("path", ""))
            self.state.has_wrote_this_turn = True
            if path and path not in self.state.writes_since_verify:
                # Skip non-code files — VerifyGate exists to catch "wrote code
                # but didn't run it". Writing a .md doc, .json config, .yaml
                # manifest, or .txt note does NOT need pytest/bash verification.
                # The model reads it back to confirm formatting, which is the
                # appropriate verification for documentation. Forcing bash on
                # docs wastes rounds and triggers false "environment broken"
                # conclusions (the most common UX complaint).
                if _is_verifiable_code(path):
                    self.state.writes_since_verify.append(path)
        elif name in READ_TOOLS:
            # Record whatever path/pattern the read tool was aimed at. For grep
            # the relevant arg is `pattern`/`path`; for read_file/list_dir/glob
            # it is `path`/`file_path`/`pattern`. Store all candidates loosely —
            # the GroundingGate matches by basename substring, so a dir read
            # (list_dir "src/agent") still counts as having looked at files there.
            for key in ("path", "file_path", "pattern"):
                v = str(args.get(key, "")).strip()
                if v:
                    self.state.read_files.add(v)
            # read_file is the only tool that actually reads file CONTENTS. A
            # grep only matches a pattern, list_dir/glob return names — none of
            # those let the model truthfully cite a specific line's behavior, so
            # they don't ground a content-level citation.
            if name in CONTENT_READ_TOOLS:
                for key in ("path", "file_path"):
                    v = str(args.get(key, "")).strip()
                    if v:
                        # Normalize on write so later _was_read comparisons are
                        # case- and slash-insensitive (see _norm_path).
                        self.state.content_read_files.add(_norm_path(v))
                        # If the read returned "file not found", record it so the
                        # GroundingGate doesn't force re-reading a nonexistent path
                        # (regex false positives on prose path-like strings).
                        # Match "not found" (not just "file not found") because
                        # deepagents' ReadResult uses "File '...' not found"
                        # and the backend uses "file not found" — both
                        # contain "not found".
                        if result and "not found" in result.lower():
                            self.state.not_found_files.add(_norm_path(v))
        elif name == VERIFY_TOOL:
            # A bash call clears "unverified writes" ONLY if it plausibly runs
            # or tests the written code AND the run actually succeeded (exit 0).
            # Two failure modes the old code missed:
            #   (1) a bare `echo done` / `ls` — doesn't test anything → reject.
            #       (_command_verifies_written already handles this.)
            #   (2) `pytest` ran but FAILED (exit != 0) → the code is NOT verified.
            #       The old code treated "ran = verified", letting the agent claim
            #       completion after a failing test run. Now we parse exit_code:
            #       only exit 0 clears the unverified-writes list.
            command = str(args.get("command", ""))
            if _command_verifies_written(command, self.state.writes_since_verify):
                exit_code = _parse_exit_code(result)
                if exit_code is not None and exit_code != 0:
                    # Verification FAILED. Do NOT clear writes_since_verify — the
                    # agent must fix the code and re-run. Do NOT increment
                    # verify_attempts here: that counter is the _verify_gate's
                    # interception budget (how many times completion was refused).
                    # A failed bash run is a genuine verify attempt, not a
                    # completion attempt — it should NOT consume the gate budget.
                    # The gate still escalates after _MAX_GATE_ATTEMPTS
                    # interceptions regardless of whether the intervening bash
                    # calls passed or failed.
                    pass
                else:
                    # Either exit 0 (passed) or no exit_code marker at all (old
                    # providers/tools that don't report it — preserve back-compat
                    # by treating absence as a neutral pass, same as before).
                    self.state.writes_since_verify.clear()
                    self.state.verify_attempts = 0
                    just_verified = True
        # Derive + fire phase change after state is updated.
        self._track_phase(just_verified=just_verified, hint=name)

    def _track_phase(self, just_verified: bool = False, hint: str = "") -> None:
        """Derive the current AgentState from ground truth and fire a transition.

        No-op when state_tracker is None (phase tracking off). Debounce is inside
        AgentStateTracker.transition — repeated same-phase calls don't bloat the
        timeline.
        """
        if self.state_tracker is None:
            return
        phase = self.state_tracker.derive_phase(
            writes_since_verify=self.state.writes_since_verify,
            todos_exist=bool(self.todos.todos),
            just_verified=just_verified,
        )
        # Only notify the stream when the phase ACTUALLY changed (debounce) —
        # otherwise 10 read_file calls would fire 10 redundant on_phase_change.
        if self.state_tracker.transition(phase, hint=hint):
            if self.stream is not None and hasattr(self.stream, "on_phase_change"):
                self.stream.on_phase_change(str(phase), 0, hint)

    # ------------------------------------------------------- after_tool_call (plan gate)
    def after_tool_call(self, name: str, args: dict, result: str) -> str | None:
        """PlanGate (soft): if writing code with no task list, append a one-time nudge.

        Returns text to APPEND to the tool result (the tool still ran), or None.
        Soft = never blocks; only nudges. The hard check on todo completion lives
        in check_termination (CompletionGate).
        """
        if not self.enabled:
            return None
        if name not in WRITE_TOOLS:
            return None
        if self.state.plan_nudged:
            return None
        if self.todos.todos:  # already has a plan -> no nudge
            return None
        self.state.plan_nudged = True
        return (
            "\n[nudge] You're writing code but have no task list yet. For non-trivial "
            'work, call todo(action="add", ...) first to decompose the task into '
            "verifiable steps. (Trivial fixes like a typo can ignore this.)"
        )

    # ------------------------------------------------------ check_termination (hard gates)
    def check_termination(self, final_text: str) -> tuple[bool, str | None, str | None]:
        """Decide whether a "no tool_calls" response may actually end the turn.

        Returns ``(should_continue, inject_message, warn_message)``:
          * should_continue=True, inject set  -> loop must NOT return; append inject
            as a user message and keep going.
          * should_continue=False, warn set   -> loop returns normally, but stream
            must show the warning (escalation release).
          * should_continue=False, warn None  -> truly done, return silently.
        """
        if not self.enabled:
            return (False, None, None)

        # VerifyGate has priority: code written but never run is the core failure.
        cont, inject, warn = self._verify_gate()
        if cont or warn:
            return (cont, inject, warn)

        # CompletionGate: only meaningful when a non-trivial todo list exists.
        cont, inject, warn = self._completion_gate()
        if cont or warn:
            return (cont, inject, warn)

        # GroundingGate: only active in CODE mode (turn has writes). In ANALYZE
        # mode (pure reads, no writes), citing filenames from docs/README is
        # normal structural description, not a false code claim. Full-session
        # audit (105 sessions, 203 outputs): 98.2% of grounding triggers were
        # false positives on filename mentions in analysis — the gate never
        # caught a real "hallucinated code citation" in practice. Restricting it
        # to CODE mode eliminates the false positives while keeping the (rare)
        # protection for code-change scenarios.
        if self.state.has_wrote_this_turn:
            return self._grounding_gate(final_text)
        return (False, None, None)

    # --------------------------------------------------------------- VerifyGate
    def _verify_gate(self) -> tuple[bool, str | None, str | None]:
        """Hard gate: unverified writes may not silently end the turn."""
        if not self.state.writes_since_verify:
            return (False, None, None)

        attempt = self.state.verify_attempts
        self.state.verify_attempts += 1

        if attempt >= _MAX_GATE_ATTEMPTS:
            # Escalation exhausted: release, but loudly. Never silent.
            files = ", ".join(self.state.writes_since_verify)
            return (
                False,
                None,
                f"agent wrote code to [{files}] but never ran it to verify, "
                "then declared completion. Output is UNVERIFIED — please review.",
            )
        if attempt == 0:
            return (
                True,
                "[harness] You wrote code but haven't verified it. You MUST run it "
                "(use bash to execute/test/lint the files you changed) before "
                "declaring done. Do not summarize or claim completion — call bash now.",
                None,
            )
        # attempt == 1: second interception, name the files and tighten the screws.
        files = ", ".join(self.state.writes_since_verify)
        return (
            True,
            f"[harness] STILL no verification. Files written but not run: {files}. "
            "Run them with bash now. Do NOT reply with text — call bash.",
            None,
        )

    # ----------------------------------------------------------- CompletionGate
    def _completion_gate(self) -> tuple[bool, str | None, str | None]:
        """Hard gate: pending todos may not silently end the turn.

        Skipped entirely when there is no todo list (trivial-task exemption): a
        small Q&A or typo fix that never built a plan should not be blocked here.
        The PlanGate's soft nudge is the only pressure in that case.
        """
        if not self.todos.todos:
            return (False, None, None)
        pending = [t for t in self.todos.todos if t.status != "completed"]
        if not pending:
            return (False, None, None)

        attempt = self.state.completion_attempts
        self.state.completion_attempts += 1

        if attempt >= _MAX_GATE_ATTEMPTS:
            return (
                False,
                None,
                f"agent declared completion with {len(pending)} unfinished todo(s). "
                "Some planned work may be incomplete — please review the task list.",
            )
        return (
            True,
            f"[harness] Your task list has {len(pending)} unfinished item(s). Mark "
            'them complete via todo(action="update", status="completed") only if '
            "truly done, or finish the remaining work. Do not claim overall "
            "completion with pending todos.",
            None,
        )

    # ----------------------------------------------------------- GroundingGate
    def _grounding_gate(self, final_text: str) -> tuple[bool, str | None, str | None]:
        """Hard gate: an analysis that cites code it never read is ungrounded.

        Failure mode this catches: the model produces a confident-sounding review
        ("loader.py:81 does X", "the harness never reads config.harness") built on
        documentation, memory, or assumption — WITHOUT having opened the file. When
        source and docs diverge (the docs are stale), this silently produces wrong
        conclusions. This gate checks citations against the set of files actually
        read this turn (``state.read_files``, populated by observe()).

        Only fires when the model EXPLICITLY cites a file path (``foo.py``,
        ``src/x.py:42``) — a pure-conversation answer with no file references is
        left alone (it isn't claiming code-level facts).
        """
        cited = _cited_files(final_text)
        if not cited:
            return (False, None, None)
        # Filter out cited paths that the agent already TRIED to read but got
        # "file not found" — these aren't real code files, just regex false
        # positives on path-like strings in prose. The agent already attempted
        # and confirmed they don't exist; forcing another read wastes rounds.
        ungrounded = [c for c in cited if not self._was_read(c) and c not in self.state.not_found_files]
        if not ungrounded:
            return (False, None, None)

        attempt = self.state.grounding_attempts
        self.state.grounding_attempts += 1

        if attempt >= _MAX_GATE_ATTEMPTS:
            files = ", ".join(sorted(ungrounded))
            return (
                False,
                None,
                f"agent cited code in [{files}] without reading it this turn. "
                "Conclusions about that code may rest on stale docs or memory — "
                "please verify against the actual source.",
            )
        if attempt == 0:
            files = ", ".join(sorted(ungrounded))
            return (
                True,
                f"[harness] You cited {files} but did not read it this turn. A claim "
                "about code must be grounded in the actual source, not documentation "
                "or memory (docs go stale; source is ground truth). Read the cited "
                "file(s) with read_file now, then CONTINUE your analysis with the "
                "grounded details. Do not restate or 'correct' your previous output — "
                "just add what you find by reading the source.",
                None,
            )
        files = ", ".join(sorted(ungrounded))
        return (
            True,
            f"[harness] STILL unread: {files}. Read them with read_file before you "
            "make any claim about their contents. If the file doesn't exist, say so "
            "explicitly instead of asserting things about it.",
            None,
        )

    @staticmethod
    def _basename(path: str) -> str:
        """Last path component (basename), slash/backslash agnostic. 'a/b.py' -> 'b.py'.

        Operates on the NORMALIZED form (see _norm_path) so case-insensitive
        filesystems (NTFS, APFS default) treat 'Loop.py' == 'loop.py' correctly —
        otherwise the model citing 'loop.py:81' after reading 'Loop.py' would
        trip the GroundingGate into a re-read loop."""
        n = _norm_path(path)
        if "/" in n:
            n = n.rsplit("/", 1)[1]
        return n

    def _was_read(self, cited: str) -> bool:
        """Did the model actually READ the cited file's contents this turn?

        Only content-level reads (read_file) ground a citation about a file's
        internals — grep/list_dir/glob don't, because they never show the file's
        actual content (grep matches a pattern; list_dir/glob return names).

        Matching: basename OR full-path exact match against the files opened with
        read_file. The old loose substring match (``c in r or r in c``) was too
        permissive — it let ``grep pattern="loop"`` satisfy a citation of
        ``loop.py``, and ``list_dir("src")`` cover every file in src/.
        A bare ``:line`` suffix on the citation is stripped first.

        All comparisons go through _norm_path so 'Loop.py' == 'loop.py' and
        'a\\b.py' == 'a/b.py' (Windows path + case-insensitive filesystems).
        Without this, the model citing 'loop.py:81' after reading 'Loop.py'
        triggers a needless re-read — observed in real sessions as 'Loop.py'
        being read 5x in one turn.
        """
        c = cited.rsplit(":", 1)[0] if ":" in cited else cited  # drop :line
        c_norm = _norm_path(c)
        c_base = self._basename(c)
        for r in self.state.content_read_files:
            if not r:
                continue
            r_norm = _norm_path(r)
            r_base = self._basename(r)
            # Exact basename match (loader.py == loader.py) or full-path match
            # on the normalized form (covers src/agent/loop.py, slash style, case).
            if c_base == r_base or c_norm == r_norm:
                return True
        return False


def _cited_files(text: str) -> list[str]:
    """Extract file-path citations from prose. Returns deduped list (order kept).

    Matches patterns like ``loader.py``, ``src/agent/loop.py``, ``a.py:81``.
    Requires a code-ish extension so ordinary words ("step 2", "the loader") do
    not false-positive. De-dupes on basename+line so the same file cited twice
    with different line numbers only lists once for the user-facing message.

    EXCLUDES file paths inside markdown table rows (``| src/foo.py | ... |``):
    those are structural listings in a project map ("this file does X"), not
    code-level citations claiming specific behavior. Without this exclusion,
    a project-analysis table listing every module triggers the GroundingGate
    on files the model listed but didn't deep-read — defeating the purpose of
    the onboarding skill's first-pass overview.
    """
    # Strip markdown table rows before matching — a path inside | ... | is a
    # directory listing, not a citation. Only paths in prose/inline-code count.
    prose = re.sub(r"^\s*\|.*\|\s*$", "", text, flags=re.MULTILINE)
    seen: set[str] = set()
    out: list[str] = []
    for m in _CITED_FILE_RE.finditer(prose):
        path = m.group(0)
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out
