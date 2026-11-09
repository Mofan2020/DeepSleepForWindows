"""Only one copy of the window at a time.

The program stays in the notification area, and it can also be started
by double clicking the executable.  When that happens the second copy
has to find the first one and ask it to show its window, instead of
opening a second window and fighting over the same files.

The lock is a file in the program directory.  The process that holds it
is the one that is running; a copy that finds the lock taken does not
take it, and asks the other one to come forward.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

__all__ = ["SingleInstance", "lock_path"]

log = logging.getLogger("winsleep.single")

#: How long a lock that nobody owns any more is respected.  A process
#: that was killed leaves its lock behind, and the program would then
#: refuse to start forever.
STALE_SECONDS = 60.0


def lock_path() -> Path:
    """The file that holds the lock."""
    from . import paths

    return paths.app_dir() / "window.lock"


class SingleInstance:
    """The lock that says "there is already a window".

    It is used as a context manager:

        with SingleInstance() as mine:
            if not mine.is_first:
                # Another copy is running.
                ...
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or lock_path()
        self._fd: int | None = None

    @property
    def is_first(self) -> bool:
        """True when this process is the only copy."""
        return self._fd is not None

    def __enter__(self) -> SingleInstance:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    def acquire(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if not self._stale():
                return
            # The lock belongs to a process that is gone.  Take it over.
            try:
                self._path.unlink()
            except OSError:
                return
            try:
                fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except OSError:
                return
        except OSError:
            return

        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        except OSError:
            pass
        self._fd = fd

    def _stale(self) -> bool:
        """True when the lock is old enough that nobody owns it."""
        try:
            age = time.time() - self._path.stat().st_mtime
        except OSError:
            return True
        if age <= STALE_SECONDS:
            return False
        # The file is old, but the process that wrote it may still be
        # running - it just has not touched the file for a while.  So
        # the process is asked whether it is alive.
        pid = self._owner()
        if pid and _process_alive(pid):
            return False
        return True

    def _owner(self) -> int:
        """The process that holds the lock."""
        try:
            text = self._path.read_text(encoding="utf-8").strip()
            return int(text)
        except (OSError, ValueError):
            return 0

    def release(self) -> None:
        """Give the lock back."""
        if self._fd is None:
            return
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        try:
            self._path.unlink()
        except OSError:
            pass


def _process_alive(pid: int) -> bool:
    """True when a process with that number is running."""
    if pid <= 0:
        return False
    try:
        import psutil

        return psutil.pid_exists(pid)
    except ImportError:
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
