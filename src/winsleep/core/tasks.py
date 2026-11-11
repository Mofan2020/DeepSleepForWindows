"""The scheduled tasks, written as XML.

The command line tool that creates a task, ``schtasks /Create``, can do
very little: it cannot say "start this again if it stops", and it cannot
say "do not stop this when the machine goes onto the battery".  Both of
those are exactly what a background half needs - without them the daemon
is simply gone one day and nobody knows why.

The full set of settings is in the task's XML, so the tasks are written
as XML and registered from a file.  That is a documented format with a
published schema, not text written for people to read, so nothing here
depends on the language of the machine.

Two kinds of task live here:

*   **The daemon** runs as ``SYSTEM``, whether anybody is logged on or
    not.  It owns the power plan, and it has to keep doing that while
    the screen is locked.
*   **The agent and the tray** run in the session of the person who is
    logged on, because they need a keyboard and a desktop.  They use
    ``InteractiveToken``, which needs no password - the token of the
    logged on user is used as it is.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

__all__ = [
    "AGENT_TASK",
    "DAEMON_TASK",
    "SCHEDULE_TASK",
    "TRAY_TASK",
    "build_task_xml",
    "describe_task_kind",
    "register_task",
]

log = logging.getLogger("winsleep.tasks")

DAEMON_TASK = "DeepSleepForWindows-Daemon"
AGENT_TASK = "DeepSleepForWindows-Agent"
TRAY_TASK = "DeepSleepForWindows-Tray"
SCHEDULE_TASK = "DeepSleepForWindows-Schedule"

#: The account that owns the machine, as a security identifier.  Using
#: the identifier rather than a name keeps this working on a machine
#: whose language this program does not know.
SYSTEM_SID = "S-1-5-18"

#: ``BUILTIN\\Users``.  Anybody who is logged on may run an interactive
#: task that is assigned to this group.
USERS_SID = "S-1-5-32-545"

#: How long to wait before starting a task again after it stopped, and
#: how many times to do that.  A daemon that crashes on a bad power plan
#: has to come back, and three tries is enough to get past a moment of
#: trouble without spinning forever.
RESTART_INTERVAL = "PT1M"
RESTART_COUNT = 10

#: A task kind is either system wide or bound to the person who is
#: logged on.  The two need different principals and different settings.
KIND_SYSTEM = "system"
KIND_INTERACTIVE = "interactive"


def describe_task_kind(name: str) -> str:
    """Which kind a task is.

    The daemon works whether anybody is logged on, so it is the system
    kind.  The agent and the tray belong to a session and are the
    interactive kind.
    """
    if name == DAEMON_TASK:
        return KIND_SYSTEM
    return KIND_INTERACTIVE


def _principal(kind: str) -> str:
    """The ``<Principals>`` block for a task kind."""
    if kind == KIND_SYSTEM:
        return (
            "  <Principals>\n"
            '    <Principal id="Author">\n'
            f"      <UserId>{SYSTEM_SID}</UserId>\n"
            "      <RunLevel>HighestAvailable</RunLevel>\n"
            "    </Principal>\n"
            "  </Principals>\n"
        )
    return (
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <GroupId>{USERS_SID}</GroupId>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
    )


def _logon_type(kind: str) -> str:
    """How the task's account is obtained."""
    if kind == KIND_SYSTEM:
        # The daemon is started by the scheduler itself, in session 0.
        return "ServiceAccount"
    # The person who is logged on, using the token they already have.
    # No password is stored, and none is asked for when the task is
    # created - which is what makes the task possible to register from
    # a program that is not running as that person.
    return "InteractiveToken"


def _triggers(kind: str, delay_seconds: int) -> str:
    """When the task starts.

    Both kinds start at logon.  A short delay gives the rest of the
    logon a chance to finish - a daemon that starts too early finds the
    power service not ready yet.
    """
    if kind == KIND_SYSTEM:
        # The daemon also has to start when the machine boots without
        # anybody logging on, so it gets a boot trigger as well.
        return (
            "  <Triggers>\n"
            "    <BootTrigger>\n"
            "      <Enabled>true</Enabled>\n"
            f"      <Delay>{_iso_seconds(delay_seconds)}</Delay>\n"
            "    </BootTrigger>\n"
            "    <LogonTrigger>\n"
            "      <Enabled>true</Enabled>\n"
            f"      <Delay>{_iso_seconds(delay_seconds)}</Delay>\n"
            "    </LogonTrigger>\n"
            "  </Triggers>\n"
        )
    return (
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        f"      <Delay>{_iso_seconds(delay_seconds)}</Delay>\n"
        "    </LogonTrigger>\n"
        "  </Triggers>\n"
    )


def _iso_seconds(seconds: int) -> str:
    """A number of seconds as an ISO 8601 duration, like ``PT30S``."""
    if seconds <= 0:
        return "PT0S"
    return f"PT{int(seconds)}S"


def _settings(kind: str) -> str:
    """The ``<Settings>`` block.

    Everything that makes a background program stay in the background
    is here:

    *   ``RestartOnFailure`` - start it again if it stops.  Without
        this, one crash is the end of it, and the machine quietly stops
        being managed.
    *   ``ExecutionTimeLimit`` of zero - never stop it for taking too
        long.  The default limit is three days, and a daemon that is
        killed every three days looks like one that disappears.
    *   ``DisallowStartIfOnBatteries`` and ``StopIfGoingOnBatteries``
        off - a laptop on the battery is still a machine that wants to
        be managed.
    *   ``StartWhenAvailable`` - if the machine was off when the task
        should have started, start it as soon as it can.
    *   ``MultipleInstancesPolicy`` of ``IgnoreNew`` - a second copy is
        never started on top of a first one.
    """
    return (
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>false</AllowHardTerminate>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
        "    <IdleSettings>\n"
        "      <StopOnIdleEnd>false</StopOnIdleEnd>\n"
        "      <RestartOnIdle>false</RestartOnIdle>\n"
        "    </IdleSettings>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n"
        "    <WakeToRun>false</WakeToRun>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        f"    <RestartOnFailure>\n"
        f"      <Interval>{RESTART_INTERVAL}</Interval>\n"
        f"      <Count>{RESTART_COUNT}</Count>\n"
        f"    </RestartOnFailure>\n"
        "    <Priority>7</Priority>\n"
        "  </Settings>\n"
    )


def _quote_xml(text: str) -> str:
    """Escape text that goes inside an XML element."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_task_xml(
    name: str,
    executable: Path,
    arguments: str,
    *,
    description: str = "",
    delay_seconds: int = 0,
    kind: str | None = None,
) -> str:
    """The XML for one task.

    *name* is only used for the description; the task's name is given
    when it is registered.  The executable and the arguments are what
    the task runs, so they have to be exactly the command line the
    program needs.
    """
    task_kind = kind or describe_task_kind(name)
    description = description or f"DeepSleep For Windows — {name}"
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.4" '
        'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        f"    <Description>{_quote_xml(description)}</Description>\n"
        "  </RegistrationInfo>\n"
        f"{_triggers(task_kind, delay_seconds)}"
        f"{_principal(task_kind)}"
        f"{_settings(task_kind)}"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{_quote_xml(str(executable))}</Command>\n"
        f"      <Arguments>{_quote_xml(arguments)}</Arguments>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def _run_xml(name: str, xml: str) -> subprocess.CompletedProcess[str]:
    """Register a task from XML.

    The file has to be UTF-16 with a byte order mark, because that is
    what the task scheduler reads.  It is written to a temporary file
    and removed afterwards, so a failed registration leaves nothing
    behind.
    """
    handle, path_text = tempfile.mkstemp(suffix=".xml", prefix="winsleep-task-")
    os.close(handle)
    path = Path(path_text)
    creationflags = 0
    if sys.platform == "win32":
        creationflags = 0x08000000  # CREATE_NO_WINDOW
    try:
        path.write_text(xml, encoding="utf-16")
        result = subprocess.run(
            ["schtasks", "/Create", "/XML", str(path), "/TN", name, "/F"],
            capture_output=True,
            check=False,
            creationflags=creationflags,
        )
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        _decode(result.stdout),
        _decode(result.stderr),
    )


def register_task(
    name: str,
    executable: Path,
    arguments: str,
    *,
    description: str = "",
    delay_seconds: int = 0,
) -> tuple[bool, str]:
    """Register one task and say whether it worked.

    A failure carries the text the scheduler wrote, because a task that
    did not get registered is otherwise only noticeable as a background
    half that never starts.
    """
    xml = build_task_xml(
        name,
        executable,
        arguments,
        description=description or f"DeepSleep For Windows — {name}",
        delay_seconds=delay_seconds,
    )
    result = _run_xml(name, xml)
    if result.returncode != 0:
        detail = _decode(result.stderr) or _decode(result.stdout) or "schtasks failed"
        return False, detail.strip()
    return True, name


def _decode(raw: bytes | str | None) -> str:
    """Turn what a Windows tool wrote into text.

    The tools write in the code page of the machine, which is not
    necessarily UTF-8 - and on a Chinese machine it is not.  Each
    candidate is tried in turn and the first one that decodes cleanly
    wins; the last resort keeps the readable parts.
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for codec in ("utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")
