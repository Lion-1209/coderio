from __future__ import annotations

import json
import os
import random
import string
import time
from pathlib import Path

from coderio.session.message import Message


def new_session_id() -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{stamp}-{suffix}"


def _resolve_save_dir(save_dir: str | Path) -> Path:
    p = Path(os.path.expanduser(str(save_dir)))
    p.mkdir(parents=True, exist_ok=True)
    return p


class Session:
    def __init__(self, path: Path, id: str, meta: dict, messages: list[Message]):
        self.path = path
        self.id = id
        self.meta = meta
        self.messages = messages

    @classmethod
    def create(cls, save_dir: str | Path, meta: dict) -> "Session":
        d = _resolve_save_dir(save_dir)
        sid = new_session_id()
        path = d / f"{sid}.jsonl"
        sess = cls(path=path, id=sid, meta=meta, messages=[])
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "meta", **meta}, ensure_ascii=False) + "\n")
        return sess

    def append(self, msg: Message) -> None:
        self.messages.append(msg)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> "Session":
        path = Path(path)
        sid = path.stem
        meta = {}
        messages = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get("type") == "meta":
                    meta = {k: v for k, v in d.items() if k != "type"}
                else:
                    messages.append(Message.from_dict(d))
        return cls(path=path, id=sid, meta=meta, messages=messages)

    @classmethod
    def load_by_id(cls, save_dir: str | Path, sid: str) -> "Session":
        d = _resolve_save_dir(save_dir)
        return cls.load(d / f"{sid}.jsonl")

    @staticmethod
    def list_recent(save_dir: str | Path, limit: int = 20) -> list[str]:
        d = _resolve_save_dir(save_dir)
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.stem for p in files[:limit]]
