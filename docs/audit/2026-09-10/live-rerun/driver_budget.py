from __future__ import annotations
import getpass, json, os, sys, time, runpy, contextlib, traceback
from pathlib import Path
import httpx

out = Path(__file__).parent / ("production-budget-" + str(sys.version_info.minor))
out.mkdir(exist_ok=True)
key = getpass.getpass("StepFun key (hidden): ")


class SafeLog:
    def __init__(self, f):
        self.f = f

    def write(self, s):
        return self.f.write(s.replace(key, "[REDACTED]"))

    def flush(self):
        self.f.flush()


from langchain_core.rate_limiters import InMemoryRateLimiter

limiter = InMemoryRateLimiter(requests_per_second=0.1, check_every_n_seconds=0.1, max_bucket_size=1)
for script in ["verify_deepagent_live.py"]:
    with (
        (out / (script + ".log")).open("w") as f,
        contextlib.redirect_stdout(SafeLog(f)),
        contextlib.redirect_stderr(SafeLog(f)),
    ):
        os.environ.update(
            STEP_KEY=key,
            CODERIO_PROVIDER="stepfun",
            CODERIO_MODEL="step-3.7-flash",
            CODERIO_BASE_URL="https://api.stepfun.com",
        )
        try:
            ns = runpy.run_path(str(Path.cwd() / "scripts" / script))
        finally:
            os.environ.pop("STEP_KEY", None)
        from coderio.agent.stream import NullStream

        events = []

        class AuditStream(NullStream):
            def on_tool_start(self, name, args, **kwargs):
                events.append({"event": "tool_start", "name": name, "args": args})

            def on_tool_end(self, name, result):
                events.append({"event": "tool_end", "name": name, "result": str(result)[:2000]})

            def on_harness_continue(self, reason):
                events.append({"event": "harness_continue", "reason": reason})

            def on_harness_warn(self, message):
                events.append({"event": "harness_warn", "message": message})

            def add_usage(self, usage):
                events.append({"event": "usage", "value": usage})

        ns["main"].__globals__["MODEL"].rate_limiter = limiter
        original = ns["run_deep_agent"]

        def observed(*args, **kwargs):
            start = time.monotonic()
            from dataclasses import replace

            args = (args[0], replace(args[1], recursion_limit=200), *args[2:])
            events.append({"event": "diagnostic_override", "recursion_limit": 200})
            try:
                return original(*args, **kwargs, stream=AuditStream())
            finally:
                events.append({"event": "turn_end", "seconds": round(time.monotonic() - start, 2)})

        ns["main"].__globals__["run_deep_agent"] = observed
        try:
            result = ns["main"]()
            print("SCRIPT_EXIT", result)
        except Exception:
            traceback.print_exc()
            print("SCRIPT_EXIT", 1)
    (out / (script + ".events.json")).write_text(
        json.dumps(events, ensure_ascii=False, indent=2, default=str).replace(key, "[REDACTED]")
    )
    print(script, "finished; see sanitized log", flush=True)
