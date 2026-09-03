"""deepagents-backed production agent engine for coderio.

deepagents provides context management (offload + summarization), a
filesystem/shell backend, subagents, and task planning.

What coderio ADDS on top via middleware:
  - HarnessMiddleware: the "wrote code but never verified → block done" hard
    constraint (4 gates). deepagents trusts the agent; coderio does not.
  - PermissionMiddleware: 4-tier access system (plan/confirm/auto_edit/full).

This module is the sole production engine, invoked by the TUI.

Streaming: uses THREE stream modes in parallel —
  - 'messages': AIMessageChunk → on_token / on_thinking (token-by-token stream)
  - 'updates':  complete messages → on_tool_start / on_tool_end / usage
  - 'custom':   harness signals → on_harness_continue / on_harness_warn
Using only 'updates' degrades on_token to
whole-text dumps and misses token streaming entirely.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from coderio.agent.harness_middleware import HarnessMiddleware
from coderio.agent.hooks import HookRunner
from coderio.agent.stream import NullStream
from coderio.session import Message
from coderio.session.store import Session

_log = logging.getLogger(__name__)


def _content_to_text(content: Any) -> str:
    """Normalize content (str or list of Anthropic blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return str(content) if content else ""


def _extract_thinking(content: Any) -> str:
    """Extract thinking-block text from Anthropic-style content (for the UI)."""
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            parts.append(block.get("thinking", "") or block.get("text", ""))
    return "".join(parts)


def _shell_backend_cls():
    """LocalShellBackend subclass that decodes subprocess output lossibly on Windows.

    deepagents' LocalShellBackend uses subprocess.run(text=True), which decodes
    stdout/stderr as strict UTF-8. On a non-UTF-8 Windows locale (e.g. GBK/CP936),
    shell commands emitting localized bytes (chcp banners, error messages, CJK
    output) raise UnicodeDecodeError and crash the agent. This subclass reads
    bytes and decodes with errors='replace' so the agent keeps running.

    IMPORTANT: must SUBCLASS LocalShellBackend (not just wrap it) so that
    deepagents' isinstance(backend, SandboxBackendProtocol) check passes —
    composite.py:560 gates the `execute` tool on this check. A composition
    wrapper with __getattr__ proxying fails the isinstance test and the agent
    gets "backend doesn't support command execution" errors.

    REGRESSION FIX (2026-08-10 sandbox analysis): the previous override read
    `self._root_dir` for cwd, but FilesystemBackend stores the root as
    `self.cwd` (not `_root_dir`). The getattr fallback meant `workspace_root`
    config and the `workdir` arg silently had NO effect on shell execution —
    commands always ran in Path.cwd(). Now reads `self.cwd` (matching upstream
    local_shell.py:335). Also restores stdin=DEVNULL, max_output_bytes
    truncation, and env=self._env that the old override dropped.
    """

    # Subclass created lazily at instantiation time (deepagents is optional).
    # We can't subclass at module-load time because LocalShellBackend may not
    # be importable. This factory builds a real subclass per instance.
    from deepagents.backends import LocalShellBackend

    # deepagents' own context management offloads oversized tool
    # results and conversation history to <root>/large_tool_results/
    # and <root>/conversation_history/ THROUGH backend.write/edit —
    # from the backend they look like ordinary writes. Snapshotting
    # them poisons /undo: the next undo deletes an offload file the
    # conversation still references, while the user-visible damage
    # they wanted to revert stays put (2026-08-27 adversarial review
    # R2, reproduced on the real graph). Internal scratch, not user
    # data — never checkpoint them.
    _internal_prefixes = ("large_tool_results/", "conversation_history/")

    def _internal_artifact_path(file_path) -> bool:
        p = str(file_path).replace("\\", "/").lstrip("/")
        return p.startswith(_internal_prefixes)

    def _checkpoint_snapshot(backend, fp):
        """Snapshot pre-write state; returns a result-checker the
        caller runs on the backend result, or None (internal path or
        resolution error — nothing was snapshotted).

        deepagents backends REPORT failures as result objects
        (WriteResult(error=...)) instead of raising, so an
        except-handler can't see a failed call: without the
        result-check, a failed edit leaves a ghost snapshot that
        turns the next /undo into a false "restored" while nothing
        changed (2026-08-27 adversarial review Y1).
        discard_if_unchanged keeps the snapshot when the disk DID
        change (partial write) — that's real damage worth undoing."""
        from coderio.tools.checkpoint import DEFAULT_CHECKPOINT

        if _internal_artifact_path(fp):
            return None
        try:
            DEFAULT_CHECKPOINT.snapshot(backend._resolve_path(fp))
        except (OSError, RuntimeError):
            return None  # resolution error → the super() call reports it

        def _check(result):
            if getattr(result, "error", None):
                try:
                    DEFAULT_CHECKPOINT.discard_if_unchanged(backend._resolve_path(fp))
                except (OSError, RuntimeError):
                    pass  # conservative: keep the snapshot
            return result

        return _check

    class _Sub(LocalShellBackend):
        """Real subclass overriding execute for Windows GBK safety.

        Overrides the upstream execute (local_shell.py:238-384) to read
        bytes and decode with errors='replace'. Preserves upstream's
        cwd=self.cwd, env=self._env, stdin=DEVNULL, max_output_bytes
        truncation, and timeout semantics that the prior coderio
        override had dropped.

        When sandbox_mode is "job" or "write", delegates to the sandbox
        module (win_sandbox / linux_sandbox) for OS-level isolation
        instead of plain subprocess.run. See config ToolsConfig.sandbox_mode.
        """

        # Set per-instance via __init__ below. Default "off" keeps the
        # legacy subprocess path for existing users.
        _sandbox_mode: str = "off"
        # Network policy + filesystem config forwarded to the sandbox
        # runner. Set per-instance so the shell backend can pass them
        # without changing LocalShellBackend's own __init__ signature.
        _network_allowed: bool = True
        _fs_config = None
        # Explicit bash path ([tools].bash_shell config, empty = auto-detect).
        # Windows NEEDS this: shell=True on win32 routes to COMSPEC
        # (cmd.exe), but the system prompt tells the model it's talking
        # to Git Bash — cmd.exe mangles single-quoted args, so
        # python -c 'print(42)' silently returns EMPTY output with
        # exit 0 (2026-08-14 report P0-4: the model thinks the command
        # succeeded and cannot self-correct).
        _bash_shell: str = ""

        # PRODUCTION CHECKPOINT HOOK (2026-08-26 review P0): the
        # /undo feature snapshotted only coderio's own write tools,
        # but the production engine's write_file/edit_file/delete are
        # deepagents' (coderio's are _SKIPped in _build_extra_tools)
        # — so every production write bypassed the checkpoint and
        # /undo was a no-op on all real paths. Fix at the BACKEND
        # layer (below every tool): every STRUCTURED write through
        # write/edit/delete snapshots the resolved disk file first,
        # whichever tool invoked it. Honest scope: shell redirects
        # (echo x > f, sed -i) still bypass this — see
        # tools/checkpoint.py for that documented boundary — and
        # deepagents' internal offload paths are excluded (R2 below).
        def write(self, file_path, content):
            check = _checkpoint_snapshot(self, file_path)
            result = super().write(file_path, content)
            return check(result) if check else result

        def edit(self, file_path, old_string, new_string, replace_all=False):  # noqa: FBT002
            check = _checkpoint_snapshot(self, file_path)
            result = super().edit(file_path, old_string, new_string, replace_all)
            return check(result) if check else result

        def delete(self, file_path):
            check = _checkpoint_snapshot(self, file_path)
            result = super().delete(file_path)
            return check(result) if check else result

        def _resolve_bash(self) -> str | None:
            """Find a bash executable, or None to fall back to shell=True.

            Cached at module level after the first successful probe —
            the probe does filesystem checks we don't want per-command.
            """
            if sys.platform != "win32":
                return None  # POSIX shell=True is /bin/sh -c — already correct
            cached = _Sub._bash_cache
            if cached is not None:
                return cached or None
            try:
                from coderio.tools.bash import detect_shell

                path = detect_shell(getattr(self, "_bash_shell", "") or "")
                _Sub._bash_cache = path
                return path
            except FileNotFoundError:
                _log.warning(
                    "bash not found (Git Bash not installed?) — falling back to "
                    "cmd.exe. Single-quoted args and POSIX syntax will misbehave; "
                    "install Git Bash or set [tools].bash_shell."
                )
                _Sub._bash_cache = ""
                return None

        _bash_cache: str | None = None

        def execute(self, command: str, *, timeout: int | None = None):  # noqa: ANN201, ARG002
            import subprocess

            from deepagents.backends.protocol import ExecuteResponse

            # Sandbox path: delegate to win_sandbox / linux_sandbox when
            # configured. The sandbox module handles cwd/env/timeout/
            # truncation internally, so we skip the subprocess block below.
            mode = getattr(self, "_sandbox_mode", "off")
            if mode in ("job", "write"):
                from coderio.tools.sandbox_runner import run_with_sandbox

                cwd_val = getattr(self, "cwd", None) or Path.cwd()
                cwd_str = str(cwd_val)
                # Workspace existence check: CreateProcessAsUserW (Win) and
                # bwrap (Linux) both fail opaquely when cwd doesn't exist
                # (Win: "CreateProcessAsUserW failed err=0"; bwrap: cryptic
                # mount error). A clear error here lets the model understand
                # the root cause (misconfigured workspace_root) and surface
                # it to the user, instead of a chain of mystery failures.
                # We check self.cwd (a Path) rather than the resolved string
                # so symlinked paths still pass.
                if not Path(cwd_str).is_dir():
                    return ExecuteResponse(
                        output=(
                            f"Error: workspace_root points to a non-existent "
                            f"directory: {cwd_str}. Update [tools].workspace_root "
                            f"in config.toml to point to your project directory, "
                            f"or leave it empty to use the current working directory."
                        ),
                        exit_code=1,
                    )
                exit_code, output = run_with_sandbox(
                    command,
                    cwd=cwd_str,
                    mode=mode,
                    timeout=timeout or 120,
                    env=getattr(self, "_env", None),
                    network_allowed=getattr(self, "_network_allowed", True),
                    fs_config=getattr(self, "_fs_config", None),
                )
                return ExecuteResponse(output=output, exit_code=exit_code)

            # Plain subprocess path (sandbox_mode="off" or sandbox failure).
            cwd = str(getattr(self, "cwd", None) or Path.cwd())
            effective_timeout = timeout if timeout is not None else getattr(self, "_default_timeout", 120)
            max_output = getattr(self, "_max_output_bytes", 100_000)
            env = getattr(self, "_env", None)
            # Windows: run through Git Bash explicitly ([bash, '-c', cmd])
            # instead of shell=True→cmd.exe. cmd.exe doesn't process single
            # quotes, so `python -c 'print(42)'` returned EMPTY output with
            # exit 0 — the model believed broken commands succeeded
            # (2026-08-14 report P0-4). POSIX keeps shell=True (/bin/sh -c,
            # semantically identical to bash -c for our purposes).
            bash_path = self._resolve_bash()
            run_args = [bash_path, "-c", command] if bash_path else command
            try:
                proc = subprocess.run(
                    run_args,
                    shell=(bash_path is None),
                    capture_output=True,
                    cwd=cwd,
                    timeout=effective_timeout,
                    text=False,  # bytes — decode ourselves (Windows GBK safety)
                    stdin=subprocess.DEVNULL,  # prevent stdin-reading cmds from hanging
                    env=env,
                )
                stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
                stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                return ExecuteResponse(output=f"Command timed out after {effective_timeout}s", exit_code=124)
            except Exception as e:  # noqa: BLE001
                return ExecuteResponse(output=f"Execution error: {e}", exit_code=1)
            output = stdout
            if stderr:
                output += f"\n[stderr]\n{stderr}"
            # Truncate oversized output (restores upstream behavior that
            # the old override dropped — without this, a `find /` or
            # verbose build log can OOM the agent's context window).
            if len(output) > max_output:
                output = output[:max_output] + f"\n\n... Output truncated at {max_output} bytes."
            return ExecuteResponse(output=output, exit_code=exit_code)

    return _Sub


_SHELL_BACKEND_CLS = None


def make_shell_backend(
    root_dir,
    virtual_mode=True,
    inherit_env=True,
    sandbox_mode: str = "off",
    network_allowed: bool = True,
    fs_config=None,
    bash_shell: str = "",
    **kwargs,
):
    """Module-level factory for the production shell backend (P2-2: was
    _WinLocalShellBackend.__new__). Same lazy subclass build, same
    per-instance config attributes — a plain function instead of a class
    whose __new__ did all the work. Extra kwargs pass through to
    LocalShellBackend.__init__ (e.g. max_output_bytes)."""
    global _SHELL_BACKEND_CLS
    if _SHELL_BACKEND_CLS is None:
        _SHELL_BACKEND_CLS = _shell_backend_cls()
    inst = _SHELL_BACKEND_CLS(root_dir=root_dir, virtual_mode=virtual_mode, inherit_env=inherit_env, **kwargs)
    inst._sandbox_mode = sandbox_mode
    inst._network_allowed = network_allowed
    inst._fs_config = fs_config
    inst._bash_shell = bash_shell
    return inst


def _resolve_system_prompt(system_prompt, skill_store, active_skills, workdir=None):
    """Build coderio's system prompt, adapting tool names for deepagents."""
    if system_prompt is not None:
        return system_prompt
    from coderio.agent.prompts import ActiveSkills, build_system_prompt
    from coderio.skills.store import SkillStore

    store = skill_store or SkillStore()
    active = active_skills or ActiveSkills()
    sp = build_system_prompt(store, active)
    # deepagents' shell tool is named 'execute', not 'bash'. The system prompt
    # references 'bash' (coderio's original name); translate the standalone word
    # so the model calls the right tool. Word-boundary regex is robust to prose
    # The rewrite regex lives in the taxonomy registry as
    # translate_bash_prose — the single copy shared by this call site and
    # harness_middleware (P2-2, audit 2026-09-02).
    from coderio.tools.taxonomy import translate_bash_prose

    sp = translate_bash_prose(sp)
    # Project instruction files (AGENTS.md / CLAUDE.md) — user conventions for
    # THIS repo, appended after coderio's own instructions (2026-08-28 audit:
    # feature gap; multi-agent users already maintain these files).
    from coderio.agent.project_instructions import instruction_boundary, instructions_block

    launch = workdir or Path.cwd()

    # Nearest-wins: walk up from the LAUNCH dir (workdir), stopping at the
    # boundary — a monorepo subpackage's AGENTS.md beats the root's, and
    # nothing above the boundary can leak in (2026-08-28 adversarial review
    # #3; 2026-09-02 audit: passing None let the walk ascend past the launch
    # dir, leaking a PARENT directory's AGENTS.md). Boundary priority
    # (instruction_boundary): configured coderio project root -> enclosing
    # git root (plain repos get their root AGENTS.md too) -> launch dir.
    sp += instructions_block(
        search_from=workdir or Path.cwd(),
        stop_at=instruction_boundary(launch),
    )
    return sp


def _build_extra_tools(tools, skill_store, active_skills, anchor_dir=None):
    """Collect coderio tools not already provided by deepagents.

    ANCHOR PARITY for multi_edit (seam probe T4): deepagents' own write/edit
    tools resolve relative paths against the backend root_dir; MultiEditTool
    used to resolve against process cwd — a launch whose cwd differs from the
    workspace (subdirectory + workspace_root) wrote the SAME relative input
    into two different files depending on which tool the model picked.
    Rewire every MultiEditTool to the workspace BEFORE conversion so one
    anchor governs all structured edits.
    """
    if anchor_dir is not None:
        from coderio.tools.multi_edit import MultiEditTool

        for t in tools or []:
            if isinstance(t, MultiEditTool):
                t.anchor = anchor_dir
    from coderio.tools.base import to_langchain_tool as _adapt
    from coderio.tools.taxonomy import LEGACY_ENGINE_TOOLS

    # These are CODERIO's own tool names (bash, todo, list_dir — the old ReAct
    # names), NOT deepagents' names (execute, write_todos, ls). We skip them
    # because deepagents already provides equivalents with its own naming.
    # Name translation between the two namespaces lives in tools/taxonomy.py
    # (to_harness_name / translate_bash_prose).
    _SKIP = LEGACY_ENGINE_TOOLS  # tools/taxonomy.py — one registry (audit A2)
    extra: list = []
    if tools:
        for t in tools:
            # MCP tools arrive as langchain BaseTool/StructuredTool (they have
            # `invoke` but not coderio Tool's plain `run`). Pass through
            # without re-adapting — their names are prefixed (e.g.
            # "filesystem_read_file") so they never collide with _SKIP entries.
            if hasattr(t, "invoke") and not hasattr(t, "args_schema"):
                extra.append(t)
                continue
            name = getattr(t, "name", "")
            if name in _SKIP:
                continue
            schema = getattr(t, "args_schema", None)
            if schema is not None:
                extra.append(_adapt(t, schema))
    if skill_store is not None and active_skills is not None:
        from coderio.agent.skill_tool import ActivateSkillTool, DeactivateSkillTool

        act = ActivateSkillTool(skill_store, active_skills)
        deact = DeactivateSkillTool(active_skills)
        extra.append(_adapt(act, act.args_schema))
        extra.append(_adapt(deact, deact.args_schema))
    return extra


def _readonly_subagent_middleware(command_policy=None, hook_runner=None) -> list:
    """Assemble the READ-ONLY middleware stack shared by the research subagent
    and every user-defined custom subagent (hooks → PermissionMiddleware(PLAN)
    → CommandReviewMiddleware). One function so the two can never drift: if a
    custom agent got a weaker stack, task(subagent_type=...) would be a
    privilege-escalation primitive dressed as a feature.

    Permission + CommandReview are execution-time enforcement on top of the
    model-visibility whitelist. The whitelist filters what the model SEES,
    these middlewares gate what actually RUNS. The hardcoded PLAN gate means a
    FULL/auto caller cannot upgrade the subagent to write access, and neither
    can anything written in a custom .md system prompt.
    """
    from coderio.agent._deepagents_compat import make_research_subagent_middleware

    middleware = make_research_subagent_middleware()
    if hook_runner is not None and hook_runner.specs:
        from coderio.agent.hooks import HooksMiddleware

        middleware.insert(0, HooksMiddleware(hook_runner))
    from coderio.agent.permission_middleware import PermissionMiddleware
    from coderio.tools.permission import PermissionGate, PermissionMode

    middleware.append(PermissionMiddleware(PermissionGate(PermissionMode.PLAN)))
    from coderio.agent.command_review import CommandReviewMiddleware
    from coderio.tools.command_policy import CommandPolicy

    policy = command_policy or CommandPolicy.default()
    middleware.append(CommandReviewMiddleware(policy))
    return middleware


def _build_research_subagent(command_policy=None, hook_runner=None):
    """Return the research subagent spec (read-only, physically isolated).

    Tool exclusion uses the compat layer (_deepagents_compat) so that a
    deepagents API change degrades gracefully instead of crashing.

    HooksMiddleware (2026-08-14 v3 audit #12): without it, ``task()``-delegated
    work bypassed the user's PreToolUse/PostToolUse hooks entirely. The runner
    is shared with the main agent (stateless across fire() calls).
    """
    return {
        "name": "research",
        "description": (
            "Read-only research and analysis agent. Use for: exploring an "
            "unfamiliar codebase section, finding where a feature is "
            "implemented, summarizing a file's purpose, or gathering "
            "evidence to ground an analysis. This agent can read files "
            "and search but CANNOT write or execute — it returns findings "
            "as text. Use it when you need to read many files without "
            "cluttering your own context."
        ),
        "system_prompt": (
            "You are a research subagent. Your job is to investigate the "
            "codebase and return clear, grounded findings.\n\n"
            "Rules:\n"
            "- Read the relevant files thoroughly before concluding.\n"
            "- Quote specific lines/functions as evidence.\n"
            "- Separate what you verified by reading from what you infer.\n"
            "- Be concise: return only the findings the caller needs, not "
            "a full retelling of every file you read.\n"
            "- If you can't find something, say so explicitly.\n"
            "- The calling agent only sees your final message, not your "
            "intermediate tool calls — make sure your answer is complete."
        ),
        "middleware": _readonly_subagent_middleware(command_policy, hook_runner),
    }


def _drop_trusted_name_collisions(custom_specs: list[dict], trusted_specs: list[dict]) -> list[dict]:
    """Defense-in-depth name filter applied at WIRING time (see call site).

    deepagents builds {name: spec} LAST-WINS and custom specs sit at the END
    of the subagents list — if discovery's reserved-name drop ever regressed,
    a repo file named research.md would silently REPLACE the trusted spec.
    This second filter makes a single-layer regression non-escalating
    (adversarial-review recommendation). Case-insensitive to mirror the
    discovery-layer rule even though the engine matches exactly.
    """
    trusted_lower = {s["name"].lower() for s in trusted_specs}
    return [s for s in custom_specs if s["name"].lower() not in trusted_lower]


def _build_custom_subagent(agent, command_policy=None, hook_runner=None):
    """Wrap a discovered CustomAgent (.coderio/agents/*.md) as a subagent spec.

    Persona comes from the file; the SECURITY STACK always comes from
    _readonly_subagent_middleware — custom definitions customize WHO the agent
    pretends to be, never WHAT it can do.
    """
    description = agent.description or (
        f"Custom read-only research agent ({agent.source_layer} layer, "
        f".coderio/agents/{agent.name}.md). Reads and searches only."
    )
    return {
        "name": agent.name,
        "description": description,
        "system_prompt": agent.system_prompt,
        "middleware": _readonly_subagent_middleware(command_policy, hook_runner),
    }


def _build_general_purpose_subagent(gate, command_policy, stream=None, hook_runner=None):
    """Return the general-purpose subagent spec with coderio's security middleware.

    SECURITY FIX (2026-08-14 report P0-2): deepagents auto-injects a
    ``general-purpose`` subagent when the caller doesn't provide one
    (graph.py:711-770), with a HARDCODED middleware list that does NOT include
    coderio's PermissionMiddleware / CommandReviewMiddleware / HarnessMiddleware.
    That subagent gets execute + write_file + edit_file via FilesystemMiddleware
    and shares the main backend — so ``task(subagent_type="general-purpose")``
    bypassed ALL of coderio's security layers. In PLAN mode (nominally
    read-only), a prompt-injected model could delegate arbitrary shell + file
    writes to this subagent unchecked.

    Fix: provide an EXPLICIT spec with the same name ("general-purpose") —
    deepagents skips its auto-injection when a same-named spec exists — and
    inject coderio's middleware stack so every tool call inside the subagent
    goes through the same permission gate + command review as the main agent.

    2026-08-14 v2 audit follow-up: also inject HarnessMiddleware — WITHOUT it,
    the model could delegate "write the code" to this subagent and then claim
    completion in the main agent; the subagent's writes were invisible to the
    main VerifyGate. The subagent gets its OWN Harness instance (harness state
    is per-agent, not shared — the main gate can't see subagent tool calls
    anyway), whose force-continue applies inside the subagent's loop. This
    makes the subagent itself refuse to say "done" with unverified writes.

    2026-08-14 v3 audit #12: also inject HooksMiddleware (outermost, same as
    the main agent) — without it, task()-delegated work bypassed the user's
    PreToolUse/PostToolUse hooks. The runner is shared (stateless fire()).
    """
    from coderio.agent.command_review import CommandReviewMiddleware
    from coderio.agent.harness_middleware import HarnessMiddleware
    from coderio.tools.command_policy import CommandPolicy

    middleware = []
    # Hooks OUTERMOST (same order as the main agent): a hook deny happens
    # before any other layer — including this subagent's permission prompts.
    if hook_runner is not None and hook_runner.specs:
        from coderio.agent.hooks import HooksMiddleware

        middleware.append(HooksMiddleware(hook_runner))
    # Harness next (same order as the main agent — see run_deep_agent): it
    # observes every tool call the permission/command layers let through.
    middleware.append(HarnessMiddleware(stream=stream))
    if gate is not None:
        from coderio.agent.permission_middleware import PermissionMiddleware

        middleware.append(PermissionMiddleware(gate))
    policy = command_policy or CommandPolicy.default()
    middleware.append(CommandReviewMiddleware(policy, gate=gate))
    return {
        "name": "general-purpose",
        "description": (
            "General-purpose subagent with full tool access. coderio's "
            "permission gate and command review apply to every tool call "
            "inside this agent, same as the main agent."
        ),
        "system_prompt": (
            "You are a general-purpose subagent. Complete the task you were "
            "given and return a clear, complete result. The calling agent "
            "only sees your final message, not intermediate tool calls."
        ),
        "middleware": middleware,
    }


def _build_inputs(checkpointer, user_input: str | list[dict[str, Any]], session: Session) -> dict:
    """Build the messages input for the agent stream.

    With a checkpointer: only pass the new user message (deepagents restores
    prior state from sqlite). Without: pass full conversation history.
    """
    if checkpointer is not None:
        # langchain declares list[str | dict] while we carry list[dict[str, Any]];
        # list invariance flags the narrower list even though every element
        # satisfies the wider union at runtime.
        return {"messages": [HumanMessage(content=user_input)]}  # type: ignore[arg-type]
    return {"messages": _build_history_messages(session.messages)}


def _run_stream(agent, inputs, thread_id, recursion_limit, stream, session, seen_ids, turn_writes, should_abort=None):
    """Drive the deepagents graph with three stream modes. Returns final text.

    ``should_abort``: optional zero-arg callable, polled between stream chunks
    (cost: one call per chunk). Returning True raises InterruptedError so the
    TUI's Esc/interrupt actually stops the ENGINE mid-turn — before this was
    wired up, the TUI's is_interrupted flag had no engine-side consumer and
    interrupting relied entirely on worker.cancel() semantics (audit
    2026-08-28, finding C3: graceful interrupt was dead code)."""
    config = {
        "recursion_limit": recursion_limit,
        "configurable": {"thread_id": thread_id},
    }
    tc_args: dict[str, tuple] = {}  # tool_call_id → (name, args) for file path tracking
    final_text = ""
    # Explicit next() loop, not `for`: the pause gate must run BEFORE pulling
    # the next chunk. A for-loop checks the gate after the pull, so a parked
    # pause would still let the upstream generator run its next body slice —
    # not real backpressure.
    chunk_iter = iter(agent.stream(inputs, config=config, stream_mode=["messages", "updates", "custom"]))
    while True:
        if should_abort is not None and should_abort():
            raise InterruptedError("interrupted by user")
        try:
            mode, event = next(chunk_iter)
        except StopIteration:
            break
        if mode == "messages":
            _handle_messages_mode(event, stream, session)
        elif mode == "updates":
            final_text = _handle_updates_mode(event, stream, session, seen_ids, turn_writes, tc_args) or final_text
        elif mode == "custom":
            _handle_custom_mode(event, stream)
    return final_text


@dataclass
class TurnSpec:
    """One agent turn's configuration (P2-2: replaces run_deep_agent's 15 kwargs).

    Everything except the per-turn payload (user_input, session, stream) lives
    here as plain data: build_middleware / build_backend / build_subagents
    consume it. Fields are SNAPSHOTS taken at construction — when a runtime
    swaps an underlying object between turns (e.g. the TUI's /model replaces
    the model, /clear replaces the session), rebuild the spec; do not cache it
    across turns.
    """

    model: Any  # a langchain BaseChatModel
    gate: Any = None  # PermissionGate; None = no permission middleware
    skill_store: Any = None  # SkillStore (for build_system_prompt); None = empty store
    active_skills: Any = None  # ActiveSkills (for build_system_prompt); None = empty
    tools: list | None = None  # extra coderio tools beyond deepagents' FS/shell set
    system_prompt: str | None = None  # None = coderio's build_system_prompt
    workdir: str | Path | None = None  # root dir for the shell backend; None = CWD
    harness_enabled: bool = True  # False disables the verification harness
    recursion_limit: int = 200  # harness force-continues + middleware each consume budget
    command_policy: Any = None  # None = CommandPolicy.default() (blacklist active)
    sandbox_mode: str = "off"  # "off" | "job" | "write" — see win_sandbox/linux_sandbox
    network_allowed: bool = True
    fs_config: Any = None
    bash_shell: str = ""  # explicit bash path ([tools].bash_shell); empty = auto-detect
    hooks: list | None = None  # HookSpec list (agent/hooks.py)


def build_middleware(spec: TurnSpec, stream, hook_runner, plan_artifact) -> list:
    """Main-agent middleware stack, outermost first.

    HooksMiddleware OUTERMOST: PreToolUse can deny before the permission
    prompt appears, and observes the exact args the rest of the chain sees.
    Harness next: it observes every tool call the permission/command layers
    let through.
    """
    middleware: list[Any] = []
    if hook_runner.specs:
        from coderio.agent.hooks import HooksMiddleware

        middleware.append(HooksMiddleware(hook_runner))

    middleware.append(
        HarnessMiddleware(
            stream=stream,
            enabled=spec.harness_enabled,
            todos=plan_artifact.store if plan_artifact is not None else None,
            plan_artifact=plan_artifact,
        )
    )
    # Planning middleware (write_todos tool): deepagents 0.7.6 REMOVED it from
    # the default graph (graph.py only mentions TodoListMiddleware in a stale
    # comment) — without re-adding it, the model's write_todos calls fail with
    # "not a valid tool" and the plan.md artifact's agent→file direction is
    # dead while the system prompt still teaches the tool (2026-08-26 review).
    from langchain.agents.middleware import TodoListMiddleware

    middleware.append(TodoListMiddleware(system_prompt=""))
    if spec.gate is not None:
        from coderio.agent.permission_middleware import PermissionMiddleware

        middleware.append(PermissionMiddleware(spec.gate))
    # Command-content review: always active (even in FULL mode). Blocks rm -rf /,
    # mkfs, fork bombs, etc. before they reach subprocess.run(shell=True).
    # This is NOT a real OS sandbox — see command_policy.py for limitations.
    # The gate reference lets the whitelist (if enabled) degrade based on mode
    # (FULL allows, others prompt) rather than hard-blocking unknown commands.
    from coderio.agent.command_review import CommandReviewMiddleware
    from coderio.tools.command_policy import CommandPolicy

    policy = spec.command_policy or CommandPolicy.default()
    middleware.append(CommandReviewMiddleware(policy, gate=spec.gate))
    return middleware


def build_backend(spec: TurnSpec):
    """Shell backend rooted at the workspace (spec.workdir or CWD)."""
    return make_shell_backend(
        root_dir=str(spec.workdir or Path.cwd()),
        virtual_mode=True,
        inherit_env=True,
        sandbox_mode=spec.sandbox_mode,
        network_allowed=spec.network_allowed,
        fs_config=spec.fs_config,
        bash_shell=spec.bash_shell,
    )


def build_subagents(spec: TurnSpec, stream, hook_runner, project_dir: str) -> list[dict]:
    """Trusted (research / general-purpose) + discovered custom subagent specs.

    Custom user/project subagents (.coderio/agents/*.md). Layer dirs joined
    HERE (not inside discovery) mirroring load_skill_store's caller-joins
    convention — passing the bare project root would glob every root *.md
    into an agent definition (same trap the custom-commands audit caught).

    ANCHOR PARITY (seam SA-4): skills/config/trust all anchor at
    _find_project_dir(search_from) which WALKS UP to the project root;
    agents used to anchor at the literal runtime dir (workdir or cwd), so a
    launch from a repo subdirectory silently loaded zero project-layer
    agents while project-layer skills still loaded — the exact
    discovery-vs-loading scope asymmetry of incident #3. Walk up with the
    same rule; workspace_root stays the starting point when set.
    """
    from coderio.agent.custom_agents import discover_custom_agents
    from coderio.config.loader import _find_project_dir

    trusted_specs = [
        _build_research_subagent(command_policy=spec.command_policy, hook_runner=hook_runner),
        _build_general_purpose_subagent(spec.gate, spec.command_policy, stream=stream, hook_runner=hook_runner),
    ]
    custom_specs = [
        _build_custom_subagent(ca, command_policy=spec.command_policy, hook_runner=hook_runner)
        for ca in discover_custom_agents(
            project_dir=_find_project_dir(project_dir) / ".coderio" / "agents",
            user_dir=Path.home() / ".coderio" / "agents",
        ).values()
    ]
    # Defense in depth at wiring time — see _drop_trusted_name_collisions.
    custom_specs = _drop_trusted_name_collisions(custom_specs, trusted_specs)
    return [*trusted_specs, *custom_specs]


def _apply_turn_hooks(hook_runner, user_input, model, session, stream):
    """Fire SessionStart (once per session, lazy — covers resume) and
    UserPromptSubmit turn-level hooks.

    Returns (user_input, early_text). early_text is not None when a hook
    blocked the prompt: the rejected user + assistant messages are already
    appended to the session, and the caller returns early_text as the turn
    result.
    """
    if hook_runner.has_event("SessionStart") and session.id not in HookRunner._sessions_started:
        HookRunner._sessions_started.add(session.id)
        ss = hook_runner.fire("SessionStart", {"source": "startup", "model": getattr(model, "model_name", "")})
        if ss.context:
            user_input = f"{user_input}\n\n[hook context]\n{ss.context}"

    # UserPromptSubmit: BEFORE session.append so a block leaves the session
    # clean, and injected context lands in both the persisted message and the
    # model input.
    if hook_runner.has_event("UserPromptSubmit"):
        prompt_text = user_input if isinstance(user_input, str) else str(user_input)
        ups = hook_runner.fire("UserPromptSubmit", {"prompt": prompt_text})
        if ups.blocked:
            session.append(Message.user(prompt_text))
            session.append(Message.assistant(f"Prompt rejected by hook: {ups.reason}"))
            if hasattr(stream, "on_finish"):
                stream.on_finish()
            return prompt_text, f"Prompt rejected by hook: {ups.reason}"
        if ups.context:
            user_input = f"{prompt_text}\n\n[hook context]\n{ups.context}"
    return user_input, None


def _prepare_plan_artifact(harness_enabled: bool, project_dir: str, user_input):
    """Plan artifact (.coderio/plan.md, S5): the harness's todo list mirrored
    to an editable file. Anchor walks up like skills/config/trust (SA-4
    lesson — a literal runtime dir would miss project plans when launched
    from a subdirectory). The SAME TodoStore instance feeds both the
    middleware mirror and the artifact, so write_todos → materialize and
    turn-start adopt stay in sync. Subagent HarnessMiddleware gets neither:
    the plan has exactly one owner. Adoption runs BEFORE the hooks/session
    append so the injected note lands inside this turn's user message.

    If the user edited plan.md between turns, their version already replaced
    the todo store — append a note telling the model so it doesn't keep
    executing a stale plan. Returns (plan_artifact, user_input).
    """
    from coderio.agent.plan_artifact import AdoptionNote, PlanArtifact
    from coderio.config.loader import _find_project_dir
    from coderio.tools.todo import TodoStore

    plan_artifact = None
    if harness_enabled:
        plan_artifact = PlanArtifact(
            anchor=_find_project_dir(project_dir) / ".coderio",
            store=TodoStore(),
        )
        adopted = plan_artifact.adopt_if_edited()
        if adopted:
            note = AdoptionNote(count=adopted, path=plan_artifact.path).render()
            if isinstance(user_input, str):
                user_input = f"{user_input}\n\n{note}"
            else:
                # Multimodal content blocks: append as an extra text block so
                # attached images survive (same concern as the hook context).
                user_input = [*user_input, {"type": "text", "text": note}]
    return plan_artifact, user_input


def _handle_interrupt(checkpointer, thread_id, stream, turn_writes) -> None:
    """Esc/interrupt cleanup: drop dangling checkpoint state + surface writes.

    The graph may have stopped right after a model turn emitted tool_calls but
    BEFORE the tool node completed — resuming from that checkpoint next turn
    would replay a dangling tool_calls state (provider 400 or surprise
    re-execution). Drop the thread's checkpoint: the next turn falls back to
    full session history, which is always consistent (audit finding #9).
    """
    if checkpointer is not None:
        try:
            checkpointer.delete_thread(thread_id)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            _log.warning("checkpointer.delete_thread failed after interrupt")
    # Interrupt must still surface the file-change summary (audit #10):
    # the user needs to know what the agent already changed.
    if hasattr(stream, "on_turn_end"):
        stream.on_turn_end(turn_writes)


def _close_checkpointer_conn(conn) -> None:
    """Close the checkpointer's sqlite conn (leaked handles block file
    deletion on Windows)."""
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: S110
            pass


def _finish_turn(hook_runner, stream, session, final_text: str, turn_writes: list[str]) -> str:
    """Stop hook + stream teardown + assistant-message persistence."""
    # Stop event (notification-only v1): the harness owns force-continue, so a
    # user Stop hook observes the turn end but cannot extend it. stdout is
    # ignored; exit 2 is logged — blocking here would fight the harness gates.
    if hook_runner.has_event("Stop"):
        try:
            hook_runner.fire("Stop", {"last_assistant_message": final_text[:2000]})
        except Exception as e:  # noqa: BLE001 — never let a hook break turn completion
            _log.warning("Stop hook failed (ignored): %s", e)
    if hasattr(stream, "on_finish"):
        stream.on_finish()
    if hasattr(stream, "on_turn_end"):
        stream.on_turn_end(turn_writes)
    if final_text and not _final_already_persisted(session):
        session.append(Message.assistant(final_text))
    return final_text


def run_deep_agent(
    user_input: str | list[dict[str, Any]],
    spec: TurnSpec,
    session: Session,
    stream=None,
) -> str:
    """Run a deepagents-backed agent turn (coderio's production engine).

    Builds a create_deep_agent from ``spec`` with coderio's middleware stack
    (hooks + harness + permission + command review), a Windows-safe shell
    backend, subagents, and coderio's extra tools. Streams events to coderio's
    StreamHandler protocol via three stream modes. Returns the final assistant
    text (also persisted to the session).

    Args:
        user_input: the user's message (str or multimodal content-block list).
        spec: turn configuration — model, permission gate, tools, workdir,
            sandbox, command policy, hooks (see TurnSpec field docs).
        session: coderio Session (messages persisted here).
        stream: coderio StreamHandler (NullStream if None).
    """
    stream = stream or NullStream()
    from deepagents import create_deep_agent

    from coderio.agent._deepagents_compat import neutralize_base_prompt

    # Neutralize deepagents' BASE_AGENT_PROMPT via compat layer (graceful
    # degradation if the internal API changes).
    neutralize_base_prompt()

    project_dir = str(Path(spec.workdir).resolve() if spec.workdir else Path.cwd())
    # --- User hooks (agent/hooks.py): turn-level events fire here, tool-level
    # events ride HooksMiddleware inside build_middleware. All fail-open
    # except explicit exit 2.
    hook_runner = HookRunner(
        spec.hooks or [],
        project_dir=project_dir,
        session_id=session.id,
        permission_mode=getattr(spec.gate, "mode", "") if spec.gate is not None else "",
    )
    user_input, rejected = _apply_turn_hooks(hook_runner, user_input, spec.model, session, stream)
    if rejected is not None:
        return rejected

    plan_artifact, user_input = _prepare_plan_artifact(spec.harness_enabled, project_dir, user_input)
    session.append(Message.user(user_input))

    sp = _resolve_system_prompt(spec.system_prompt, spec.skill_store, spec.active_skills, workdir=spec.workdir)
    middleware = build_middleware(spec, stream, hook_runner, plan_artifact)
    backend = build_backend(spec)
    subagents = build_subagents(spec, stream, hook_runner, project_dir)
    extra_lc_tools = _build_extra_tools(spec.tools, spec.skill_store, spec.active_skills, anchor_dir=project_dir)

    build_kwargs: dict[str, Any] = {
        "model": spec.model,
        "middleware": middleware,
        "backend": backend,
        "subagents": subagents,
    }
    if sp:
        build_kwargs["system_prompt"] = sp
    if extra_lc_tools:
        build_kwargs["tools"] = extra_lc_tools

    # --- Checkpointer: persist graph state across turns (sqlite). Without one,
    # each run_deep_agent call starts from scratch and we'd re-pass the full
    # history every turn; with one, we only pass the NEW user message (see
    # _try_create_checkpointer for the degradation path).
    thread_id = session.id
    checkpointer, _db_conn = _try_create_checkpointer(session)
    if checkpointer is not None:
        build_kwargs["checkpointer"] = checkpointer

    _seen_tool_calls: set[str] = set()
    _turn_writes: list[str] = []
    try:
        agent = create_deep_agent(**build_kwargs)
        inputs = _build_inputs(checkpointer, user_input, session)
        if hasattr(stream, "on_step_start"):
            stream.on_step_start()
        # Bridge the stream handler's interrupt flag into the stream loop:
        # Esc sets it, the loop raises InterruptedError at the next chunk.
        abort_hook = getattr(stream, "is_interrupted", None)
        abort = (lambda: bool(abort_hook())) if callable(abort_hook) else None
        final_text = _run_stream(
            agent, inputs, thread_id, spec.recursion_limit, stream, session, _seen_tool_calls, _turn_writes, abort
        )
    except InterruptedError:
        _handle_interrupt(checkpointer, thread_id, stream, _turn_writes)
        raise
    finally:
        _close_checkpointer_conn(_db_conn)

    return _finish_turn(hook_runner, stream, session, final_text, _turn_writes)


def _final_already_persisted(session: Session) -> bool:
    """Check if the last message is already an assistant text (avoid double-append).

    _handle_updates_mode persists AIMessages as they arrive; the final text may
    already be the last session message. We avoid appending a duplicate.
    """
    msgs = session.messages
    if not msgs:
        return False
    last = msgs[-1]
    return last.role == "assistant" and not getattr(last, "tool_calls", None)


def _try_create_checkpointer(session: Session):
    """Create a SqliteSaver for graph state persistence.

    Returns (checkpointer, conn) or (None, None). The caller MUST close the
    conn after use to avoid leaking file handles (especially on Windows where
    open sqlite files can't be deleted).

    Stores checkpoint state alongside the session jsonl (same directory,
    {session_id}.sqlite).
    """
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        db_path = session.path.parent / f"{session.id}.sqlite"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
    except ImportError:
        _log.warning("langgraph-checkpoint-sqlite not installed — no graph state persistence")
        return None, None
    except Exception as e:
        # Silent None was an audit finding (2026-08-28): without a checkpointer
        # every turn replays the FULL session history — a linearly growing
        # cost the user can't see. Say why it degraded.
        _log.warning("checkpointer unavailable (%s: %s) — turns will replay full history", type(e).__name__, e)
        return None, None
    # conn is now open. If SqliteSaver() or setup() fails, close it.
    try:
        checkpointer = SqliteSaver(conn)
        checkpointer.setup()
        return checkpointer, conn
    except Exception as e:
        _log.warning("checkpointer setup failed (%s) — turns will replay full history", e)
        try:
            conn.close()
        except Exception:  # noqa: S110
            pass
        return None, None


def _build_history_messages(session_messages: list) -> list:
    """Convert session messages to langchain messages for deepagents input.

    Unlike _to_langchain_messages (which prepends a SystemMessage), this only
    returns user/assistant/tool messages — the system prompt is injected
    separately by create_deep_agent's system_prompt parameter.

    Drops phase_timeline system messages (observability metadata). Keeps
    context_summary system messages (they carry compacted history the model
    needs). The current turn's user message is the LAST element (already
    appended to session before this call).
    """
    msgs: list = []
    for m in session_messages:
        if m.role == "user":
            msgs.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            tcs = None
            if m.tool_calls:
                tcs = [{"name": tc.name, "args": tc.args, "id": tc.id, "type": "tool_call"} for tc in m.tool_calls]
            msgs.append(AIMessage(content=m.content, tool_calls=tcs or []))
        elif m.role == "tool":
            msgs.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id or ""))
        elif m.role == "system":
            # Phase timelines are metadata — never shown to the model.
            # Context summaries ARE shown (that's their purpose).
            if getattr(m, "kind", None) != "phase_timeline":
                msgs.append(HumanMessage(content=m.content))
    return msgs


def _handle_messages_mode(event, stream, session) -> None:
    """Process 'messages' mode: token-by-token streaming.

    event is (AIMessageChunk, metadata). We extract text → on_token and
    thinking blocks → on_thinking. We do NOT persist here — complete messages
    are persisted in the 'updates' mode handler.
    """
    if not isinstance(event, tuple) or len(event) != 2:
        return
    chunk, metadata = event
    if not isinstance(chunk, AIMessage):
        return
    # Only stream from the model node (not tool results).
    node = metadata.get("langgraph_node", "") if isinstance(metadata, dict) else ""
    if node and node not in ("model", ""):
        return

    raw = getattr(chunk, "content", "")
    # Thinking blocks (Anthropic).
    thinking = _extract_thinking(raw)
    if thinking and hasattr(stream, "on_thinking"):
        stream.on_thinking(thinking)
    # Text content.
    text = _content_to_text(raw)
    if text and hasattr(stream, "on_token"):
        stream.on_token(text)


def _handle_updates_mode(event, stream, session, seen_ids: set, turn_writes: list, tc_args: dict) -> str:
    """Process 'updates' mode: complete messages (tool calls, tool results, final text).

    Returns the final assistant text if this event carries it (for the caller to
    return). Persists messages to session.
    """
    if not isinstance(event, dict):
        return ""
    final_text = ""
    for _node, payload in event.items():
        if not isinstance(payload, dict):
            continue
        msgs = payload.get("messages", [])
        for m in msgs:
            final_text = _emit_message(m, stream, session, seen_ids, turn_writes, tc_args) or final_text
    return final_text


def _handle_custom_mode(event, stream) -> None:
    """Process 'custom' mode: harness signals (continue / warn).

    event is the dict passed to runtime.stream_writer by HarnessMiddleware.
    """
    if not isinstance(event, dict):
        return
    etype = event.get("type")
    if etype == "harness_continue" and hasattr(stream, "on_harness_continue"):
        stream.on_harness_continue(event.get("reason", ""))
    elif etype == "harness_warn" and hasattr(stream, "on_harness_warn"):
        stream.on_harness_warn(event.get("message", ""))


def _emit_message(m, stream, session, seen_ids: set, turn_writes: list, tc_args: dict | None = None) -> str:
    """Map a complete langchain message to stream callbacks + session persistence.

    Returns the assistant text if this is a final (no tool_calls) AIMessage.
    """
    if isinstance(m, AIMessage):
        text = _content_to_text(getattr(m, "content", ""))
        tool_calls = getattr(m, "tool_calls", None) or []
        # Usage metadata lives on the AIMessage object (not in the updates
        # payload dict). Extract it here so the status bar can show live token
        # consumption. Without this, add_usage is never called (the old code
        # looked for it in payload.get("usage_metadata") which is always empty).
        usage = getattr(m, "usage_metadata", None)
        if usage and hasattr(stream, "add_usage"):
            stream.add_usage(usage)
        if tool_calls:
            from coderio.session import ToolCall

            tcs = []
            for tc in tool_calls:
                name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                args = dict(tc.get("args", {})) if isinstance(tc, dict) else dict(getattr(tc, "args", {}))
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                if tc_id not in seen_ids:
                    if hasattr(stream, "on_tool_start"):
                        stream.on_tool_start(name, args)
                    seen_ids.add(tc_id)
                # Intercept write_todos: deepagents replaces the whole list each
                # call, so args["todos"] is the current full todo list. Push it
                # to the UI for the live todo panel.
                if name == "write_todos" and "todos" in args and hasattr(stream, "on_todos_update"):
                    stream.on_todos_update(args["todos"])
                tcs.append(ToolCall(id=tc_id, name=name, args=args))
                # Remember args by tool_call_id so ToolMessage handler can
                # extract file paths for the turn-end summary.
                if tc_args is not None:
                    tc_args[tc_id] = (name, args)
            session.append(Message.assistant(text, tool_calls=tcs))
            return ""
        elif text:
            session.append(Message.assistant(text))
            return text
    elif isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None):
        name = getattr(m, "name", "tool")
        content = _content_to_text(getattr(m, "content", ""))
        if hasattr(stream, "on_tool_end"):
            stream.on_tool_end(name, content)
        session.append(Message.tool_result(m.tool_call_id, name, content))
        # Track file writes for the turn-end summary. Use tc_args to get the
        # actual file path (ToolMessage only has content, not args).
        if name in ("write_file", "edit_file") and not content.startswith(("Error", "Permission denied")):
            file_path = ""
            if tc_args is not None:
                _stored = tc_args.get(m.tool_call_id)
                if _stored:
                    _sname, _sargs = _stored
                    file_path = str(_sargs.get("file_path", _sargs.get("path", "")))
            turn_writes.append(file_path or name)
    return ""
