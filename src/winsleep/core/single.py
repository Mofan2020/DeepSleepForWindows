"""Only one copy of the window at a time.

The program stays in the notification area, and it can also be started
by double clicking the executable.  When that happens the second copy
has to find the first one and ask it to show its window, instead of
opening a second window and fighting over the same files.

The lock is a file in the program directory.  The process that holds it
is the one that is running; a copy that finds the lock taken does not
take it - it asks the other one to come forward (``windowlink.request_show``)
and, when nobody answers and the owner is gone, it takes the lock over.

**The owner is named by pid and by process start time.**  A pid alone is
not enough on Windows: numbers are reused quickly, and a lock left by a
killed copy then points at an unrelated process that happens to be
alive.  That is what made the program answer "there is already a window"
while there was no window on the screen at all.

**A dead owner makes the lock stale at once.**  The first version waited
a minute before looking at the pid, so a copy that had just crashed
blocked the program for a minute.  Waiting is only for a lock whose
content cannot be read at all.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

__all__ = ["SingleInstance", "clear_if_stale", "lock_path"]

log = logging.getLogger("winsleep.single")

#: How long a lock that names nobody is respected.  A lock that names a
#: live process is respected for as long as that process runs; this only
#: covers a file whose content cannot be read.
STALE_SECONDS = 60.0

#: How far two readings of a process start time may differ before they
#: are taken to be two different processes.
_START_TOLERANCE = 2.0


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
        self._started = _process_start_time(os.getpid())

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

        self._fd = fd
        self._write_owner(fd)

    def _write_owner(self, fd: int) -> None:
        """Put this process into the lock file.

        Two lines: the pid, and the start time of the process that wrote
        it.  A file written by an older version holds only the pid, and
        is still understood - it just cannot tell a reused pid apart.
        """
        text = str(os.getpid())
        if self._started is not None:
            text += f"\n{self._started:.3f}"
        try:
            os.write(fd, text.encode("ascii"))
            os.fsync(fd)
        except OSError:
            pass

    def owner(self) -> dict[str, float | int | None]:
        """What the lock file says: ``{"pid": ..., "started": ...}``.

        An empty dictionary means the file holds nothing readable, which
        is treated as "nobody knows who owns this".
        """
        try:
            text = self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return {}
        if not text:
            return {}
        parts = text.splitlines()
        try:
            pid = int(parts[0].strip())
        except ValueError:
            return {}
        started: float | None = None
        if len(parts) > 1:
            try:
                started = float(parts[1].strip())
            except ValueError:
                started = None
        return {"pid": pid, "started": started}

    def owner_alive(self) -> bool:
        """True when the process named in the lock is still running.

        A reused pid is not the owner: the start time has to match.  A
        lock written by an older version has no start time, and then the
        pid is all there is to go on.
        """
        owner = self.owner()
        pid = int(owner.get("pid", 0) or 0)
        if pid <= 0:
            return False
        if not _process_alive(pid):
            return False

        recorded = owner.get("started")
        if not isinstance(recorded, float):
            return True
        actual = _process_start_time(pid)
        if actual is None:
            # The start time cannot be read (no psutil, no rights).  The
            # pid is alive, and that is the best answer available.
            return True
        return abs(actual - recorded) <= _START_TOLERANCE

    def _stale(self) -> bool:
        """True when nobody holds the lock.

        A named owner is asked first, so a dead owner is stale at once
        instead of a minute later.  Only a lock with nothing readable in
        it falls back to the age rule.
        """
        if not self._path.exists():
            return True

        if self.owner():
            return not self.owner_alive()

        try:
            age = time.time() - self._path.stat().st_mtime
        except OSError:
            return True
        return age > STALE_SECONDS

    def take_over(self) -> bool:
        """Take the lock away from an owner that is gone.

        Returns True when this process holds the lock afterwards.  A live
        owner is never displaced: two windows writing the same settings is
        worse than one window that will not come forward.
        """
        if self._fd is not None:
            return True
        if self._path.exists() and self.owner_alive():
            return False
        try:
            self._path.unlink()
        except OSError:
            pass
        self.acquire()
        return self.is_first

    def release(self) -> None:
        """Give the lock back.

        Only a lock this process still owns is removed.  When another copy
        has taken it over in the meantime, deleting the file would hand a
        second window to the next double click.
        """
        if self._fd is None:
            return
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

        owner_pid = int(self.owner().get("pid", 0) or 0)
        if owner_pid and owner_pid != os.getpid():
            return
        try:
            self._path.unlink()
        except OSError:
            pass


def clear_if_stale() -> bool:
    """Remove the lock when nobody holds it.  True when it was removed.

    The daemon runs whether or not a window does, so it cannot tell by
    itself whether a lock is in use.  It used to delete the file on every
    start - which is a live window's lock, and then a double click opened
    a second window.  Asking the lock instead of assuming is the fix.
    """
    instance = SingleInstance()
    if not instance._path.exists():
        return False
    if instance.owner_alive():
        return False
    try:
        instance._path.unlink()
    except OSError:
        return False
    return True


def _process_start_time(pid: int) -> float | None:
    """When the process with that number started, or None.

    ``psutil`` is the only portable way to ask.  Without it the pid is
    all there is, and the caller falls back to that.
    """
    if pid <= 0:
        return None
    try:
        import psutil
    except ImportError:
        return None
    try:
        return float(psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001 - psutil raises many things
        return None


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
