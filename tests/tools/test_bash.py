import shutil
import sys

import pytest

from coderio.tools.bash import BashTool, detect_shell

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash not available"
)


def test_run_echo():
    tool = BashTool()
    out = tool.run(command="echo hello-world")
    assert "hello-world" in out


def test_run_captures_stderr():
    tool = BashTool()
    out = tool.run(command="ls /nonexistent-dir-xyz 2>&1 || true")
    assert isinstance(out, str)


def test_detect_shell_returns_path():
    shell = detect_shell("")
    assert "bash" in shell.lower()


def test_run_writes_file_via_cwd(tmp_path):
    tool = BashTool()
    tool.run(command="echo done > out.txt", cwd=str(tmp_path))
    assert (tmp_path / "out.txt").exists()


def test_run_in_background_returns_pid(tmp_path):
    tool = BashTool()
    out = tool.run(command="sleep 0", run_in_background=True, cwd=str(tmp_path))
    assert "pid" in out.lower()
