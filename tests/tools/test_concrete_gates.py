from coderio.tools.permission import (
    AutoPermissionGate,
    RichPromptPermissionGate,
)


def test_rich_prompt_gate_asks_and_uses_answer():
    def _yes(name, args):
        return True

    gate = RichPromptPermissionGate(prompt_fn=_yes)
    assert gate.mode == "confirm"
    assert gate.check("bash", {"command": "ls"}) is True

    def _no(name, args):
        return False

    gate_no = RichPromptPermissionGate(prompt_fn=_no)
    assert gate_no.check("write_file", {"path": "x"}) is False
    assert gate_no.check("read_file", {"path": "x"}) is True


def test_auto_permission_gate_allows_all():
    gate = AutoPermissionGate()
    assert gate.check("bash", {}) is True
    assert gate.check("write_file", {}) is True
    assert gate.mode == "auto"
