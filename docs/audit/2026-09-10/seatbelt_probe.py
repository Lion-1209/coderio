from __future__ import annotations
import tempfile, subprocess, sys, json
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="coderio-seatbelt-") as td:
    root = Path(td).resolve()
    blocked = root / "blocked"
    blocked.mkdir()
    profile = "(version 1)(allow default)(deny file-write* (subpath " + json.dumps(str(blocked)) + "))"
    code = (
        "from pathlib import Path; p=Path("
        + repr(str(root))
        + '); (p/"allowed").write_text("ok");\ntry: (p/"blocked"/"denied").write_text("bad")\nexcept PermissionError: print("allowed write succeeded; blocked write denied")\nelse: raise RuntimeError("deny failed")'
    )
    r = subprocess.run(
        ["/usr/bin/sandbox-exec", "-p", profile, sys.executable, "-c", code], capture_output=True, text=True, timeout=10
    )
    print("returncode", r.returncode)
    print(r.stdout)
    print(r.stderr)
