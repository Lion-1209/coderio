from __future__ import annotations
import getpass, os, time, sys, contextlib
from pathlib import Path
import pytest
from coderio.config.models import Config, ModelConfig
from coderio.llm.factory import build_chat_model

key = getpass.getpass("StepFun key (hidden): ")
out = Path(__file__).parent / "perf-final.log"
os.environ["CODERIO_PERF_TESTS"] = "1"
os.environ["ANTHROPIC_API_KEY"] = "collection-marker-only"
os.environ.pop("Z_API_KEY", None)


class Plugin:
    def pytest_collection_modifyitems(self, items):
        def real_model():
            os.environ["OPENAI_API_KEY"] = key
            try:
                model = build_chat_model(Config(model=ModelConfig(provider_id="stepfun_api", default="step-3.7-flash")))
            finally:
                os.environ.pop("OPENAI_API_KEY", None)
            assert model.openai_api_key.get_secret_value() == key, "factory selected a different credential"
            return model

        for item in items:
            item.module._make_model = real_model

    def pytest_runtest_setup(self, item):
        time.sleep(12)


class SafeLog:
    def __init__(self, f):
        self.f = f

    def write(self, s):
        return self.f.write(s.replace(key, "[REDACTED]"))

    def flush(self):
        self.f.flush()

    def isatty(self):
        return False


with out.open("w") as f, contextlib.redirect_stdout(SafeLog(f)), contextlib.redirect_stderr(SafeLog(f)):
    print("Actual factory: Config ModelConfig(provider_id=stepfun_api, default=step-3.7-flash).")
    print(
        "Only test model configuration is injected; assertions, engine and tools unchanged. Pacing before tests excluded from timing."
    )
    result = pytest.main(["tests/agent/test_perf_baseline.py", "-v", "-s"], plugins=[Plugin()])
    print("PYTEST_EXIT", result)
print("perf finished", result)
