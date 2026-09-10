from __future__ import annotations
import os, sys, time, tempfile, subprocess, shlex, signal, socket
from pathlib import Path
from coderio.agent.deep_loop import make_shell_backend
from coderio.cli.credentials import write_credentials, read_credentials
from coderio.tools.web_fetch import WebFetchTool

with tempfile.TemporaryDirectory(prefix="coderio-audit-") as folder:
    root = Path(folder)
    for mode in ("off", "job", "write"):
        pidfile = root / ("pid-" + mode)
        code = (
            "import os,time; from pathlib import Path; Path("
            + repr(str(pidfile))
            + ").write_text(str(os.getpid())); time.sleep(20)"
        )
        command = shlex.quote(sys.executable) + " -c " + shlex.quote(code) + " & wait"
        backend = make_shell_backend(root_dir=root, sandbox_mode=mode)
        start = time.monotonic()
        result = backend.execute(command, timeout=1)
        pid = int(pidfile.read_text())
        state = subprocess.run(
            ["/bin/ps", "-o", "pid=,ppid=,pgid=,stat=,comm=", "-p", str(pid)], capture_output=True, text=True
        )
        print(
            mode,
            "elapsed",
            round(time.monotonic() - start, 2),
            "exit",
            result.exit_code,
            "output",
            result.output,
            "ps",
            state.stdout.strip(),
            flush=True,
        )
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    p = root / "credentials"
    write_credentials({"audit": "FAKE-NOT-A-KEY"}, p)
    print("credential mode", oct(p.stat().st_mode & 0o777))
    p.write_bytes(b'[audit]\nkey="FAKE-NOT-A-KEY"\nBROKEN')
    read_credentials(p)
    print("backup mode", oct(p.with_suffix(".corrupt").stat().st_mode & 0o777))
    for url in [
        "http://127.0.0.1:1",
        "http://10.0.0.1",
        "http://[::1]",
        "http://[fe80::1%lo0]",
        "http://100.100.200.200",
        "http://" + socket.gethostname(),
    ]:
        print(url, WebFetchTool().run(url, timeout=1), flush=True)
