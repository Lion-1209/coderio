"""File-write checkpoints: an in-memory undo stack behind ``/undo``.

Every agent file WRITE through the structured tools (write_file / edit_file /
multi_edit) snapshots the target's pre-write state here first, so a bad edit
is one ``/undo`` away from gone. This is trust-building infrastructure: users
who know any write can be reverted let the agent touch more files.

Scope and honest limits (mirrors command_policy's documented-boundary style):
- Only the three structured write tools are covered. Shell redirects
  (``echo x > f``), bash-side edits, and note/other tools bypass this —
  string-level interception can't see them; OS-level isolation is the layer
  for that class.
- Process-lifetime memory only: checkpoints do not survive restarts, and they
  DELIBERATELY survive /clear — reverting file damage should not depend on
  chat history still existing.

Storage: prior content as raw bytes (faithful for non-UTF8 too) plus an
existed flag. Undo pops the newest snapshot: restores bytes, or deletes the
file when it was created by the operation. Bounded at MAX_ENTRIES and
MAX_TOTAL_BYTES (oldest evicted) so a long session can't grow memory without
bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_ENTRIES = 50
MAX_TOTAL_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class _Snapshot:
    path: Path  # resolved absolute
    existed: bool
    content: bytes | None  # None iff existed is False


@dataclass(frozen=True)
class UndoResult:
    """What one /undo actually did — surfaced to the user verbatim."""

    path: Path
    restored: bool  # True = prior content written back; False = created-file deleted


class FileCheckpoint:
    def __init__(self, *, max_entries: int = MAX_ENTRIES, max_total_bytes: int = MAX_TOTAL_BYTES) -> None:
        self._stack: list[_Snapshot] = []
        self._max_entries = max_entries
        self._max_total_bytes = max_total_bytes

    def __len__(self) -> int:
        return len(self._stack)

    def snapshot(self, path: str | Path) -> None:
        """Capture ``path``'s current state BEFORE an overwrite. No-op on read
        errors (the subsequent write will surface its own error anyway)."""
        p = Path(path)
        try:
            existed = p.is_file()
            content = p.read_bytes() if existed else None
        except OSError:
            return
        self._stack.append(_Snapshot(path=p.resolve(), existed=existed, content=content))
        self._evict_overflow()

    def _evict_overflow(self) -> None:
        while len(self._stack) > self._max_entries:
            self._stack.pop(0)
        total = sum(len(s.content) for s in self._stack if s.content is not None)
        while total > self._max_total_bytes and self._stack:
            dropped = self._stack.pop(0)
            total -= len(dropped.content) if dropped.content is not None else 0

    def undo(self) -> UndoResult | None:
        """Revert the most recent snapshotted write. Returns None when the
        stack is empty (nothing to undo — callers say so explicitly)."""
        if not self._stack:
            return None
        snap = self._stack.pop()
        try:
            if snap.existed:
                # mkdir-parents: the agent may have removed/recreated dirs since;
                # restoring content implies restoring its home.
                snap.path.parent.mkdir(parents=True, exist_ok=True)
                snap.path.write_bytes(snap.content or b"")
            else:
                # The operation CREATED this file — undo removes it. missing_ok
                # covers the already-deleted-by-hand case.
                snap.path.unlink(missing_ok=True)
        except OSError:
            # Put it back so a later /undo retry (e.g. after a lock clears)
            # can still reach this state — silently losing undo depth would be
            # the worst outcome of a transient failure.
            self._stack.append(snap)
            raise
        return UndoResult(path=snap.path, restored=snap.existed)

    def clear(self) -> None:
        self._stack.clear()


DEFAULT_CHECKPOINT = FileCheckpoint()
