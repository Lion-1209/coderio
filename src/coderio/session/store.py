from __future__ import annotations

import json
import os
import random
import string
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from coderio.session.message import Message, text_of_content

# In-process mutex for appends: the file lock below is BEST-EFFORT (2s
# timeout, then an unlocked fall-through). Same-process threads hammering
# one session exhausted that timeout on fast machines and dropped writes
# (CI 2026-08-28: 46/60 survived on windows-latest). The critical section
# is a single write call, so an unbounded in-process lock cannot deadlock.
# The file lock remains for the cross-process case (two coderio instances
# appending to one jsonl).
_APPEND_MUTEX = threading.Lock()


@contextmanager
def _locked_append(path: str | Path, timeout: float = 2.0) -> Iterator[object]:
    """Open a file for append with a best-effort cross-platform exclusive lock.

    Prevents interleaved writes when two processes append to the same session
    jsonl simultaneously (e.g. two coderio instances resuming the same session).
    On POSIX: fcntl.flock (LOCK_EX). On Windows: msvcrt.locking. If the lock
    isn't acquired within ``timeout`` seconds (or the platform lock module is
    unavailable), falls through to an unlocked write — locking is a safety net,
    not a hard gate, so the agent never blocks indefinitely on session I/O.

    KNOWN BOUNDARY (runtime audit 2026-09-04, Windows): the fail-open
    fall-through write can itself fail while another process holds byte [0,1)
    AND the file is empty — an append to an empty file starts at offset 0,
    inside the locked range, so flush/close raises PermissionError and that
    one write is lost. Production callers are unaffected: ``Session.create``
    writes the meta line first, so a session file is never 0 bytes by the time
    a second process appends. The window only opens for a same-path empty-file
    race (two instances creating the same brand-new session id in the same
    millisecond — timestamp + 4 random chars makes this negligible). Fixing it
    for real needs a lock byte outside any possible write — impossible on an
    empty file — or retry-on-PermissionError at the caller.
    """
    p = Path(path)
    f = open(p, "a", encoding="utf-8")
    lock_acquired = False
    deadline = time.monotonic() + timeout
    try:
        if os.name == "nt":
            try:
                import msvcrt

                # Lock byte [0,1) as the cross-process mutex. msvcrt.locking
                # locks at the CURRENT position, and an "a"-mode handle starts
                # at the open-time EOF — a holder that appends grows the file,
                # so a later opener locked a DIFFERENT byte and both "held"
                # the lock (2026-09-04 audit P0-5, reproduced experimentally).
                # seek(0) pins the lock to a fixed range: mutual exclusion
                # survives EOF drift, and "a"-mode writes still land at the
                # (never-locked) end of file.
                f.seek(0)
                while time.monotonic() < deadline:
                    try:
                        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                        lock_acquired = True
                        break
                    except OSError:
                        time.sleep(0.05)
            except (ImportError, OSError):
                pass  # msvcrt unavailable — best-effort unlocked write
        else:
            try:
                import fcntl

                while time.monotonic() < deadline:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        lock_acquired = True
                        break
                    except OSError:
                        time.sleep(0.05)
            except (ImportError, OSError):
                pass
        yield f
    finally:
        if lock_acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    # The lock lives at [0,1) — seek back there to unlock the
                    # range actually locked (the old seek(0)-after-locking-at-
                    # EOF combo unlocked a range that was never held and
                    # swallowed the error; the real lock only cleared at close).
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        f.close()


def new_session_id() -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{stamp}-{suffix}"


def _resolve_save_dir(save_dir: str | Path) -> Path:
    p = Path(os.path.expanduser(str(save_dir)))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _truncate_at_last_summary(messages: list[Message]) -> list[Message]:
    """Drop conversation messages superseded by the last context_summary.

    When compaction runs, a ``kind="context_summary"`` system message is appended
    to the session. The conversation messages (user/assistant/tool) that came
    BEFORE it have been folded into that summary and are no longer needed —
    re-loading them would bloat the context the compaction just shrank.

    This function finds the LAST context_summary in the list and keeps:
      - ALL system messages (phase_timeline, the summary itself, etc.) regardless
        of position — observability timelines survive compaction.
      - all user/assistant/tool messages AT OR AFTER the last summary.

    Messages before the last summary that are user/assistant/tool are dropped.
    If there is no summary, the list is returned unchanged (no compaction
    happened, nothing to truncate).
    """
    last_summary_idx = -1
    for i, m in enumerate(messages):
        if m.role == "system" and m.kind == "context_summary":
            last_summary_idx = i
    if last_summary_idx < 0:
        return messages  # no compaction in this session
    kept: list[Message] = []
    for i, m in enumerate(messages):
        if i < last_summary_idx:
            # Before the last summary: keep only system messages (timelines).
            if m.role == "system":
                kept.append(m)
        else:
            # At or after the last summary: keep everything.
            kept.append(m)
    return kept


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
        with _locked_append(path) as f:
            f.write(json.dumps({"type": "meta", **meta}, ensure_ascii=False) + "\n")
        return sess

    def append(self, msg: Message) -> None:
        self.messages.append(msg)
        with _APPEND_MUTEX, _locked_append(self.path) as f:
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
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    # Tolerate a trailing corrupted/partial line (crash mid-write,
                    # power loss, Ctrl+C during append). Skip it rather than making
                    # the entire session un-resumable.
                    continue
                if d.get("type") == "meta":
                    meta = {k: v for k, v in d.items() if k != "type"}
                else:
                    messages.append(Message.from_dict(d))
        # Compaction truncation: if the session contains one or more
        # context_summary system messages, drop the conversation messages
        # (user/assistant/tool) that PRECEDE the LAST summary — they have been
        # superseded by it and re-loading them would bloat the context the
        # compaction just shrank. System messages (phase_timeline) are kept
        # regardless so observability timelines survive compaction.
        messages = _truncate_at_last_summary(messages)
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

    @staticmethod
    def summaries(save_dir: str | Path, limit: int = 20) -> list[dict]:
        """Lightweight session previews for the resume picker (Claude-Code style).

        Returns dicts: {id, first_user, message_count, mtime}. Reads only the
        meta line + first user message + counts lines — does NOT load every
        Message into memory (a session can have hundreds). The picker shows
        `first_user` so the user recognizes a session by what they asked, not by
        an opaque id like '20260703-093941-b9f7'.
        """
        from datetime import datetime

        d = _resolve_save_dir(save_dir)
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for p in files[:limit]:
            first_user = ""
            count = 0
            model = ""
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") == "meta":
                        model = rec.get("model", "") or model
                        continue
                    # system-role messages (phase_timeline / context_summary) are
                    # observability metadata, not conversation — don't count them
                    # in message_count and don't surface them as first_user.
                    if rec.get("role") == "system":
                        continue
                    count += 1  # user/assistant/tool line is a real message
                    if rec.get("role") == "user" and not first_user:
                        # content may be str or a multimodal block list —
                        # text_of_content collapses it to the text parts.
                        first_user = text_of_content(rec.get("content", "")).strip().replace("\n", " ")
            out.append(
                {
                    "id": p.stem,
                    "first_user": first_user[:80],  # cap for the picker row
                    "message_count": count,
                    "model": model,
                    "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                }
            )
        return out
