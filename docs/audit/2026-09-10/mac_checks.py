from __future__ import annotations
import tempfile, tomllib, io, os
from pathlib import Path
from rich.console import Console
from coderio.config.loader import _from_dict
from coderio.cli.repl import build_gate, _sandbox_boundary
from coderio.agent.hooks import HookRunner
from coderio.agent.deep_loop import make_shell_backend
from coderio.tools.checkpoint import DEFAULT_CHECKPOINT
from coderio.tools.command_policy import CommandPolicy

with tempfile.TemporaryDirectory(prefix="coderio-mac-check-") as td:
    root = Path(td)
    cfg = _from_dict(
        tomllib.loads(
            '[tools]\nsandbox_mode="write"\nauto_allow_if_sandboxed=true\npermission_mode="confirm"\n[[hooks]]\nevent="PreToolUse"\ncommand="cat > hook-input.json; echo audit-denied >&2; exit 2"'
        )
    )
    output = io.StringIO()
    gate = build_gate(cfg, console=Console(file=output))
    print("boundary", _sandbox_boundary("write"), "auto_allow", gate._auto_allow_execute, "warning", output.getvalue())
    backend = make_shell_backend(root_dir=root, sandbox_mode="write")
    print("degraded output", backend.execute("printf audit-ok").output)
    runner = HookRunner(cfg.hooks, project_dir=td, session_id="audit", permission_mode="confirm")
    print("hook", runner.fire("PreToolUse", {"tool_name": "write_file", "tool_input": {"path": "a.py"}}))
    print("hook IO", (root / "hook-input.json").read_text())
    DEFAULT_CHECKPOINT.clear()
    b = make_shell_backend(root_dir=root)
    print("write", b.write("/MixedCase.py", "original"))
    print("case insensitive", (root / "mixedcase.py").exists())
    print("edit", b.edit("/mixedcase.py", "original", "modified"))
    print("undo", DEFAULT_CHECKPOINT.undo(), "content", (root / "MixedCase.py").read_text())
    print("undo creation", DEFAULT_CHECKPOINT.undo(), "exists", (root / "MixedCase.py").exists())
    for cmd in ["rm -rf ${HOME}", "diskutil eraseDisk APFS name disk9", "Stop-Computer -Force", "echo safe"]:
        print("policy", cmd, CommandPolicy().check_command(cmd))
