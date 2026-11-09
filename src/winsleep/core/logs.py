"""Reading, trimming and clearing the log file.

The program writes one log file, and every part of it - the daemon, the
agent, the window - writes into the same one.  That makes the file the
right place to look when the user wants to know what the machine was
doing, but it also means the file grows on its own.

Three things live here:

*   Reading the file, optionally only the part that is new since the
    last read, so the window can show the log as it grows instead of
    re-reading the whole file every second.
*   Trimming it: by the number of lines it keeps, by the age of the
    lines in it, or both.
*   Clearing it.

The trimming is done by rewriting the file rather than by moving it
aside.  The daemon, the agent and the window all hold the file open
through a rotating handler, and on Windows a file that has been renamed
cannot be written to any more - the handle would keep pointing at the
old name.  Rewriting in place keeps every handle valid.

Everything here is plain Python and works off Windows, so it is unit
tested on any machine.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

__all__ = [
    "LogTailer",
    "clear_log",
    "read_log",
    "trim_log",
    "prune_rotated",
]


def _read_text(path: Path) -> str:
    """The whole log file as text, or an empty string when it is missing."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_log(path: Path, max_lines: int = 0) -> list[str]:
    """The lines of the log file, the last *max_lines* of them.

    A *max_lines* of zero or less means "all of them".  A file that
    cannot be read gives an empty list rather than an error: the window
    has to draw something even when the file is missing.
    """
    text = _read_text(path)
    if not text:
        return []
    lines = text.splitlines()
    if max_lines > 0 and len(lines) > max_lines:
        return lines[-max_lines:]
    return lines


def trim_log(path: Path, max_lines: int = 0, max_age_days: float = 0.0) -> dict[str, Any]:
    """Drop the old lines from the log file.

    *max_lines* keeps only that many of the newest lines.  *max_age_days*
    drops the lines that are older than that many days - the age comes
    from the timestamp at the start of each line, because the log
    format puts the time first.

    A value of zero or less means that limit is not used.

    Returns how many lines were there, how many are left, and whether
    anything was changed.
    """
    text = _read_text(path)
    if not text:
        return {"ok": True, "before": 0, "after": 0, "removed": 0, "changed": False}

    lines = text.splitlines()
    before = len(lines)

    kept = lines
    if max_age_days > 0:
        cutoff = time.time() - (max_age_days * 86400.0)
        kept = [line for line in kept if _line_age_ok(line, cutoff)]

    if max_lines > 0 and len(kept) > max_lines:
        kept = kept[-max_lines:]

    after = len(kept)
    if after == before:
        return {"ok": True, "before": before, "after": after, "removed": 0, "changed": False}

    _write_in_place(path, "\n".join(kept) + ("\n" if kept else ""))
    return {
        "ok": True,
        "before": before,
        "after": after,
        "removed": before - after,
        "changed": True,
    }


def _line_age_ok(line: str, cutoff: float) -> bool:
    """True when the line is newer than *cutoff*.

    The log lines start with the time the log formatter wrote, for
    example ``2026-11-09 08:12:33``.  A line whose time cannot be read
    is kept: dropping a line because its timestamp is odd would lose
    information the user may want.
    """
    stamp = _parse_timestamp(line)
    if stamp is None:
        return True
    return stamp >= cutoff


def _parse_timestamp(line: str) -> float | None:
    """The time a log line was written, or ``None`` when it is unknown."""
    text = line.strip()
    if len(text) < 19:
        return None
    try:
        parsed = time.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return time.mktime(parsed)


def _write_in_place(path: Path, text: str) -> None:
    """Write the file in place, so the open handles stay valid.

    The file is truncated and rewritten rather than replaced.  On
    Windows a file that has been renamed cannot be written to any more
    - the handle would keep pointing at the old name - and the daemon,
    the agent and the window all hold this file open through a rotating
    handler.  A crash halfway through leaves a partly written log, which
    is a log, not a loss.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="replace") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def clear_log(path: Path) -> dict[str, Any]:
    """Empty the log file.

    The file is truncated rather than deleted, so the handles the
    daemon and the agent hold stay valid and the log keeps being
    written into the same file.
    """
    before = len(read_log(path))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8"):
            pass
    except OSError as exc:
        return {"ok": False, "removed": 0, "message": str(exc)}
    return {"ok": True, "removed": before, "message": f"已清除 {before} 行日志"}


class LogTailer:
    """Follows the log file and hands out the lines that are new.

    The window asks the tailer for the new lines every second.  The
    tailer remembers where it was, so the answer is only the lines that
    appeared since the last time.

    The position is a byte offset into the file.  When the file has
    been shortened - the user cleared it, or it was trimmed - the
    offset points past the end and the tailer starts again from the
    beginning.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._offset = 0

    def reset(self) -> None:
        """Forget where the tailer was, so the next read starts over."""
        self._offset = 0

    def read_new(self, max_lines: int = 0) -> list[str]:
        """The lines that were written since the last call."""
        try:
            size = self._path.stat().st_size
        except OSError:
            return []

        if size < self._offset:
            # The file was truncated or replaced.  Start from the top.
            self._offset = 0

        if size == self._offset:
            return []

        try:
            with open(self._path, encoding="utf-8", errors="replace") as handle:
                handle.seek(self._offset)
                text = handle.read()
                self._offset = handle.tell()
        except OSError:
            return []

        if not text:
            return []

        # The last line may still be incomplete - the writer has not
        # finished it yet.  It is kept for the next round instead of
        # being shown half written.
        lines = text.splitlines()
        if not text.endswith("\n") and lines:
            cut = text.rfind("\n") + 1
            self._offset -= len(text) - cut
            lines = lines[:-1]
        if max_lines > 0 and len(lines) > max_lines:
            lines = lines[-max_lines:]
        return lines


def prune_rotated(path: Path, keep: int = 3) -> int:
    """Remove the rotated log files that are not worth keeping.

    The rotating handler writes ``winsleep.log.1`` and
    ``winsleep.log.2`` beside the log.  When the user asks for a small
    number of lines to be kept, the rotated files have to go too, or
    the disk keeps filling up with old copies.
    """
    removed = 0
    parent = path.parent
    if not parent.is_dir():
        return 0

    candidates = sorted(parent.glob(path.name + ".*"))
    for index, candidate in enumerate(candidates):
        # ``.1`` is the newest, so everything past *keep* is old.
        if index >= max(0, keep):
            try:
                candidate.unlink()
                removed += 1
            except OSError:
                continue
    return removed
