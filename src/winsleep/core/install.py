"""Installing and removing WinSleep as a background service.

Windows has no built-in way to run a Python program as a service without
either writing a service wrapper in C or pulling in pywin32.  There is
a third way that fits this program well: **a scheduled task**.

``schtasks`` ships with Windows, it can run at startup, and it can run
the program under the SYSTEM account without a password prompt.  The
daemon needs very little - it changes the power plan and holds an
execution state - so running it as SYSTEM is not required and is in fact
unnecessary; a per-user task with the "run whether the user is logged on
or not" flag is enough and does not need any elevation.

Two tasks are used:

``DeepSleepForWindows-Daemon``
    Runs at startup as the logged-on user.  This is the background half.

``DeepSleepForWindows-Agent``
    Runs in the interactive session so it can read the real idle time.
    It is started on demand by the daemon rather than at startup, because
    it only makes sense once somebody is actually logged in.

Everything here shells out to ``schtasks`` and ``taskkill``.  No Win32.
"""

from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger("winsleep.install")

DAEMON_TASK = "DeepSleepForWindows-Daemon"
AGENT_TASK = "DeepSleepForWindows-Agent"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command and capture its output.

    The output is decoded with the local code page, because that is
    what these Windows tools write.  A command that is not there at all
    (no ``schtasks`` off Windows) comes back as a failed result rather
    than an exception.
    """
    from .winpower import _decode

    try:
        result = subprocess.run(args, capture_output=True, check=False)
    except (FileNotFoundError, PermissionError, OSError):
        return subprocess.CompletedProcess(args, returncode=127, stdout="", stderr="")

    return subprocess.CompletedProcess(
        args,
        returncode=result.returncode,
        stdout=_decode(result.stdout),
        stderr=_decode(result.stderr),
    )


def _python_exe() -> str:
    """The interpreter that should run the program."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return sys.executable


def _module_args(module: str) -> str:
    """The command line that runs one of our modules."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --module {module}'
    return f'"{sys.executable}" -m winsleep.{module}'


def install_daemon_task() -> tuple[bool, str]:
    """Create the task that starts the daemon at logon."""
    command = _module_args("cli")
    args = [
        "schtasks",
        "/Create",
        "/TN",
        DAEMON_TASK,
        "/TR",
        f"{command} daemon",
        "/SC",
        "ONLOGON",
        "/F",
    ]
    result = _run(args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "schtasks failed"
    return True, DAEMON_TASK


def install_agent_task() -> tuple[bool, str]:
    """Create the task that starts the agent in the interactive session.

    ``/IT`` makes it run in the interactive session, which is the whole
    point: the agent has to be in the same session as the keyboard.
    """
    command = _module_args("cli")
    args = [
        "schtasks",
        "/Create",
        "/TN",
        AGENT_TASK,
        "/TR",
        f"{command} agent",
        "/SC",
        "ONLOGON",
        "/IT",
        "/F",
    ]
    result = _run(args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "schtasks failed"
    return True, AGENT_TASK


def start_agent() -> tuple[bool, str]:
    """Start the agent task right now."""
    result = _run(["schtasks", "/Run", "/TN", AGENT_TASK])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "schtasks failed"
    return True, "the agent was started"


def stop_task(name: str) -> tuple[bool, str]:
    """Stop a running task."""
    result = _run(["schtasks", "/End", "/TN", name])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "schtasks failed"
    return True, f"{name} was stopped"


def remove_task(name: str) -> tuple[bool, str]:
    """Delete a task."""
    result = _run(["schtasks", "/Delete", "/TN", name, "/F"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "schtasks failed"
    return True, f"{name} was removed"


def task_exists(name: str) -> bool:
    """True when a task with that name is registered."""
    result = _run(["schtasks", "/Query", "/TN", name])
    return result.returncode == 0


def install_all() -> tuple[bool, str]:
    """Register both tasks."""
    ok, detail = install_daemon_task()
    if not ok:
        return False, detail
    ok, detail = install_agent_task()
    if not ok:
        return False, detail
    return True, "the tasks were registered"


def uninstall_all() -> tuple[bool, str]:
    """Remove both tasks and stop whatever they started."""
    messages = []
    for name in (DAEMON_TASK, AGENT_TASK):
        if task_exists(name):
            stop_task(name)
            remove_task(name)
            messages.append(f"{name} was removed")
    if not messages:
        return True, "nothing was installed"
    return True, ", ".join(messages)
