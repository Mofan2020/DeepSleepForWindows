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


def app_dir() -> Path:
    """The directory holding the config, the log and the runtime state."""
    override = os.environ.get("WINSLEEP_HOME")
    if override:
        return Path(override).expanduser().resolve()

    if is_frozen():
        program_data = os.environ.get("ProgramData")
        if program_data:
            return Path(program_data) / "DeepSleepForWindows"

    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "DeepSleepForWindows"

    return Path.home() / ".deepsleep-for-windows"


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
