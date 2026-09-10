# 2026-09-10 macOS audit evidence

See the root `PROJECT_REVIEW_2026-09-10.md` for scope and interpretation.
Logs preserve command output with user/workspace/hostname prefixes replaced.
Trailing whitespace is normalized. No real API credentials are included.

Reproduce from a frozen development environment:

```sh
uv sync --frozen --extra dev
uv run python docs/audit/2026-09-10/probe.py
uv run python docs/audit/2026-09-10/mac_checks.py
uv run python docs/audit/2026-09-10/fetch_buffer.py
uv run python docs/audit/2026-09-10/seatbelt_probe.py
```

`probe.py` runs owned short-lived children and kills any surviving test PID.
`fetch_buffer.py` starts a real loopback HTTP server, explicitly configures it
as a process-local HTTP proxy, and sends no request to the public internet.
`seatbelt_probe.py` tests one deny rule, not a production sandbox profile.
`mac_checks.py` exercises the real shell and writes only temporary artifacts.

`baseline*` is main at b87aa06; `after*` contains the two fixes and four new
parameterized test cases. Each status file has a line per required command.
`coverage.log` deliberately retains an unsuccessful direct-python run (PATH
lacked python); use `uv run` as required by the contribution instructions.
The fresh wheel venv resolved dependencies independently of uv.lock.
