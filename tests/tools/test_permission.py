import pytest

from coderio.tools.permission import PermissionGate, PermissionMode


class _AlwaysAllow(PermissionGate):
    def __init__(self, mode):
        super().__init__(mode)

    def _ask(self, tool_name, args):
        return True


def test_readonly_tools_always_allowed():
    gate = _AlwaysAllow("plan")
    for t in ("read_file", "glob", "grep", "todo"):
        assert gate.check(t, {}) is True


def test_plan_mode_blocks_write_tools():
    gate = _AlwaysAllow("plan")
    for t in ("write_file", "edit_file", "bash", "web_fetch"):
        assert gate.check(t, {}) is False


def test_auto_mode_allows_all():
    gate = _AlwaysAllow("auto")
    assert gate.check("bash", {}) is True
    assert gate.check("write_file", {}) is True


def test_confirm_mode_asks():
    gate = _AlwaysAllow("confirm")
    assert gate.check("write_file", {}) is True


def test_invalid_mode():
    with pytest.raises(ValueError):
        PermissionMode("bogus")
