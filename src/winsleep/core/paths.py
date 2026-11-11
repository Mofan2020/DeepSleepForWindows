"""Where WinSleep keeps its files.

Everything lives under one directory so that uninstalling is a single
delete.  The location depends on how the program was started:

* frozen (the PyInstaller build) -> ``%ProgramData%\\DeepSleepForWindows``
* from source -> ``%LOCALAPPDATA%\\DeepSleepForWindows``

``%ProgramData%`` is used for the frozen build because the daemon runs as
a service and needs a location every user can read.  The source layout
uses the local app data directory so a developer run never touches the
machine-wide copy.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "app_dir",
    "config_path",
    "log_path",
    "socket_path",
    "state_path",
    "is_frozen",
]


def is_frozen() -> bool:
    """True when running from the PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


#: The directory that was chosen, once it has been chosen.  Looking
#: for it means writing a probe file, and the answer cannot change
#: while this process runs - so it is worked out once.
_CHOSEN: Path | None = None


def app_dir() -> Path:
    """The directory holding the config, the log and the runtime state.

    The machine-wide place is preferred, because the daemon runs as
    ``SYSTEM`` and the window runs as the user: both have to look in
    the same place, and only a machine-wide directory is the same place
    for two different accounts.

    The catch is that a machine-wide directory can be owned by the
    administrators, and then the window - which no longer asks for
    administrator rights - cannot write the settings into it.  So the
    directory is checked, and a directory that cannot be written to is
    left alone in favour of the user's own.
    """
    global _CHOSEN
    if _CHOSEN is not None:
        return _CHOSEN

    override = os.environ.get("WINSLEEP_HOME")
    if override:
        _CHOSEN = Path(override).expanduser().resolve()
        return _CHOSEN

    if is_frozen():
        program_data = os.environ.get("ProgramData")
        if program_data:
            candidate = Path(program_data) / "DeepSleepForWindows"
            if _is_writable(candidate):
                _CHOSEN = candidate
                return _CHOSEN

    local = os.environ.get("LOCALAPPDATA")
    if local:
        _CHOSEN = Path(local) / "DeepSleepForWindows"
        return _CHOSEN

    _CHOSEN = Path.home() / ".deepsleep-for-windows"
    return _CHOSEN


def _is_writable(directory: Path) -> bool:
    """True when this process may write into that directory.

    A directory that does not exist yet counts as writable when its
    parent can be written to, because making it is the next thing that
    happens.  A directory that cannot be reached at all is not
    writable.

    The test writes a file rather than looking at permissions: what
    matters is whether the write works, and the permissions of a
    machine-wide directory are not something to reason about from the
    outside.
    """
    probe = directory / ".writable"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
    except OSError:
        return False
    try:
        probe.unlink(missing_ok=True)
    except OSError:
        pass
    return True


def config_path() -> Path:
    """The JSON file holding the settings and the rules."""
    return app_dir() / "config.json"


def log_path() -> Path:
    """The rotating log file."""
    return app_dir() / "winsleep.log"


def state_path() -> Path:
    """Runtime state the daemon and the agent exchange."""
    return app_dir() / "state.json"


def socket_path() -> Path:
    """The local socket the daemon listens on.

    On Windows this is a TCP port on the loopback interface rather than a
    named pipe: the daemon runs in session 0 and the agent runs in the
    user's session, and a loopback port crosses that boundary without any
    extra privileges.  The port number is written into the state file so
    the agent can find it.
    """
    return app_dir() / "winsleep.port"
