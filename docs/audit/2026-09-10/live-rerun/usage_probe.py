from __future__ import annotations
import getpass, json
from pathlib import Path
import httpx
from langchain_openai import ChatOpenAI

key = getpass.getpass("StepFun key (hidden): ")
records = []


class Stream(httpx.SyncByteStream):
    def __init__(self, stream):
        self.stream = stream
        self.buffer = b""

    def __iter__(self):
        for chunk in self.stream:
            self.buffer += chunk
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if line.startswith(b"data: "):
                    try:
                        d = json.loads(line[6:])
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if d.get("usage"):
                        records.append(d["usage"])
            yield chunk

    def close(self):
        self.stream.close()


class Transport(httpx.HTTPTransport):
    def handle_request(self, request):
        response = super().handle_request(request)
        response.stream = Stream(response.stream)
        return response


with httpx.Client(transport=Transport(), timeout=45) as client:
    model = ChatOpenAI(
        model="step-3.7-flash",
        base_url="https://api.stepfun.com/v1",
        api_key=key,
        max_tokens=64,
        http_client=client,
        streaming=True,
        max_retries=0,
    )
    result = model.invoke("Reply OK only.")
    evidence = {
        "raw_usage_records": records,
        "langchain_usage_metadata": result.usage_metadata,
        "model_response_text": result.content,
    }
    Path(__file__).with_suffix(".json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2).replace(key, "[REDACTED]")
    )
    print("raw usage records", len(records), "aggregated", result.usage_metadata)
