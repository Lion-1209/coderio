"""Live entry points must use the current engine with real tools."""

from __future__ import annotations

import importlib.util
import shlex
import sys
from pathlib import Path
from types import ModuleType

import pytest
from langchain_core.messages import AIMessage

from tests.agent.conftest import make_model


def load_script(name: str, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("STEP_KEY", "fake-test-key")
    monkeypatch.setenv("CODERIO_PROVIDER", "stepfun")
    path = Path(__file__).resolve().parents[2] / "scripts" / name
    spec = importlib.util.spec_from_file_location("live_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("STEP_KEY")
    return module


def write_message(filename: str, content: str, tc_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": tc_id,
                "name": "write_file",
                "args": {
                    "file_path": "/" + filename,
                    "content": content,
                },
            }
        ],
    )


def execute_message(filename: str, tc_id: str) -> AIMessage:
    executable = shlex.quote(sys.executable.replace("\\", "/"))
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": tc_id,
                "name": "execute",
                "args": {
                    "command": f"{executable} {filename}",
                },
            }
        ],
    )


@pytest.mark.parametrize(
    "function,filename,content",
    [
        ("test_verify_gate_fires", "hello.py", "print('hello-harness')"),
        ("test_verify_gate_passes", "greet.py", "print('greetings')"),
        ("test_harness_disabled", "skip.py", "print('x')"),
    ],
)
def test_harness_live_uses_real_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    function: str,
    filename: str,
    content: str,
) -> None:
    script = load_script("verify_harness_live.py", monkeypatch)
    script.MODEL = make_model(
        write_message(filename, content, "w"), execute_message(filename, "e"), AIMessage(content="Done")
    )
    getattr(script, function)(tmp_path)
    assert (tmp_path / filename).read_text() == content


def test_deepagent_live_uses_real_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = load_script("verify_deepagent_live.py", monkeypatch)
    script.MODEL = make_model(
        write_message("marker.py", "print('deepagent-ok')", "w1"),
        execute_message("marker.py", "e1"),
        AIMessage(content="Done"),
        write_message("calc.py", "print(1+1)", "w2"),
        execute_message("calc.py", "e2"),
        AIMessage(content="Done"),
    )
    assert script.main() == 0
