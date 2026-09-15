"""TuiPermissionGate must fail closed when the tui can't ask (P2-N4).

The 2026-09-14 audit found `_ask` returning True when the wired tui object
lacked `request_confirmation` — an assembly defect silently granting every
destructive tool. The bare PermissionGate contract is "can't ask = deny";
the TUI gate must honor the same contract.
"""

from __future__ import annotations

from coderio.cli.repl import TuiPermissionGate
from coderio.tools.permission import PermissionMode


def test_ask_denies_when_tui_lacks_confirmation_interface():
    """A tui object without request_confirmation is an assembly defect —
    deny instead of silently granting."""

    class _TuiWithoutInterface:
        pass

    gate = TuiPermissionGate(PermissionMode.CONFIRM, _TuiWithoutInterface())
    assert gate._ask("write_file", {"file_path": "/x.py", "content": "x"}) is False


def test_ask_delegates_when_tui_has_confirmation_interface():
    """Happy path unchanged: the gate delegates to the tui's confirmation
    and returns its verdict verbatim (True / False / 'reason' passthrough)."""

    class _Tui:
        def __init__(self, verdict):
            self.verdict = verdict
            self.calls = []

        def request_confirmation(self, tool_name, args, detail=None):
            self.calls.append(tool_name)
            return self.verdict

    for verdict in (True, False, "deny: user pressed Esc"):
        tui = _Tui(verdict)
        gate = TuiPermissionGate(PermissionMode.CONFIRM, tui)
        assert gate._ask("edit_file", {"file_path": "/x.py"}) == verdict
        assert tui.calls == ["edit_file"]
