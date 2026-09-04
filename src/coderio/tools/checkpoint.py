"""File-write checkpoints: an in-memory undo stack behind ``/undo``.

Every agent file WRITE through the structured tools (write_file / edit_file /
multi_edit) snapshots the target's pre-write state here first, so a bad edit
is one ``/undo`` away from gone. This is trust-building infrastructure: users
who know any write can be reverted let the agent touch more files.

Scope and honest limits (mirrors command_policy's documented-boundary style):
- Every STRUCTURED write through the production backend (write_file / edit_file
  / delete / multi_edit, whichever tool invoked them — hooks live in the
  backend subclass, below all tools) is covered. Shell redirects
  (``echo x > f``), bash-side edits, and note/other tools bypass this —
  string-level interception can't see them; OS-level isolation is the layer
  for that class. deepagents' internal context-management files
  (large_tool_results/, conversation_history/) are excluded on purpose.
- Process-lifetime memory only: checkpoints do not survive restarts, and they
  DELIBERATELY survive /clear — reverting file damage should not depend on
  chat history still existing.
- ONE instance per workspace: two coderio processes share no checkpoint
  state — instance A's /undo restores the pre-A content over anything
  instance B wrote in between, with no cross-process detection
  (2026-09-04 audit P1-12). Git is the real safety net for multi-writer
  scenarios.
- Files larger than the byte budget are skipped (logged, not snapshotted):
  they can't fit, and keeping them would push out every older snapshot.

Storage: prior content as raw bytes (faithful for non-UTF8 too) plus an
existed flag. Undo pops the newest snapshot: restores bytes, or deletes the
file when it was created by the operation. Bounded at MAX_ENTRIES and
MAX_TOTAL_BYTES (oldest evicted) so a long session can't grow memory without
bound — and the NEWEST snapshot is never evicted: it guards the most recent
write, and dropping it made /undo silently restore an older state while
presenting as a normal undo (2026-09-04 audit P1-12).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

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
        errors (the subsequent write will surface its own error anyway).

        Directories are refused: deepagents' delete rmtree's them, and this
        stack can only hold single-file bytes. A dir recorded as existed=False
        would make undo claim "deleted agent-created file" while nothing was
        restored — an honest no-op beats a lying snapshot (2026-08-27 review R3).
        """
        p = Path(path)
        try:
            if p.is_dir():
                return
            if p.is_file() and p.stat().st_size > self._max_total_bytes:
                # Oversized: snapshotting it would blow the whole budget (and
                # pre-2026-09-04, eviction then dropped it TOO — /undo "undid"
                # the latest write by restoring an older one). Skipping is the
                # honest option: the write proceeds, /undo won't cover it.
                _log.warning(
                    "file %s (%d bytes) exceeds the checkpoint budget (%d bytes) — "
                    "skipping snapshot; /undo will NOT cover this write",
                    p,
                    p.stat().st_size,
                    self._max_total_bytes,
                )
                return
            existed = p.is_file()
            content = p.read_bytes() if existed else None
        except OSError:
            return
        self._stack.append(_Snapshot(path=p.resolve(), existed=existed, content=content))
        self._evict_overflow()

    def discard_if_unchanged(self, path: str | Path) -> None:
        """Pop the newest snapshot when the operation it guarded never landed.

        Called by the backend hooks when a write/edit/delete FAILED: if the
        disk still matches the snapshot's pre-state, the failed call changed
        nothing and the snapshot guards nothing — keeping it would turn the
        next /undo into a false "restored" while the user's real damage stays
        put (2026-08-27 adversarial review Y1). A partial write (disk now
        differs from the pre-state) KEEPS the snapshot — that is real damage
        worth undoing.
        """
        if not self._stack:
            return
        snap = self._stack[-1]
        try:
            if snap.path != Path(path).resolve():
                return
            if snap.existed:
                unchanged = snap.path.is_file() and snap.path.read_bytes() == (snap.content or b"")
            else:
                unchanged = not snap.path.exists()
        except OSError:
            return
        if unchanged:
            self._stack.pop()

    def _evict_overflow(self) -> None:
        # The NEWEST snapshot always survives (2026-09-04 audit P1-12): it
        # guards the most recent write, and the old bottom-up eviction could
        # drop it too — /undo then restored an OLDER state (or reported an
        # empty stack) while presenting as a normal undo of the latest write.
        while len(self._stack) > max(1, self._max_entries):
            self._stack.pop(0)
        total = sum(len(s.content) for s in self._stack if s.content is not None)
        while total > self._max_total_bytes and len(self._stack) > 1:
            dropped = self._stack.pop(0)
            total -= len(dropped.content) if dropped.content is not None else 0

    def undo(self) -> UndoResult | None:
        """Revert the most recent snapshotted write. Returns None when the
        stack is empty (nothing to undo — callers say so explicitly)."""
        if not self._stack:
            return None
        snap = self._stack.pop()
        # A created-file entry whose path has since become a DIRECTORY can
        # never be unlinked — re-pushing it on failure would wedge every
        # later /undo on the same entry. The entry stays popped; the caller
        # reports the error (snapshot() already refuses dirs, so this only
        # covers dir-created-after-snapshot races).
        if not snap.existed and snap.path.is_dir():
            raise OSError(f"cannot undo creation of {snap.path}: it is now a directory")
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
