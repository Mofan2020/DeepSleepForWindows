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
SCHEDULE_TASK = "DeepSleepForWindows-Schedule"
TRAY_TASK = "DeepSleepForWindows-Tray"


def _decode(raw: bytes) -> str:
    """Turn the raw output of a command into text.

    Windows writes what these tools print in the local code page, which
    is not UTF-8 on a Chinese machine.  The text is only used for
    error messages, so anything that cannot be decoded is replaced
    rather than raising.
    """
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command and capture its output.

    The output is decoded with the local code page, because that is
    what these Windows tools write.  A command that is not there at all
    (no ``schtasks`` off Windows) comes back as a failed result rather
    than an exception.
    """
    creationflags = 0
    if sys.platform == "win32":
        creationflags = 0x08000000  # subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            args, capture_output=True, check=False, creationflags=creationflags
        )
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


def _module_args(command: str) -> str:
    """The command line that runs one of our sub commands.

    A frozen build is one executable with sub commands, so the command
    is the executable followed by the sub command.  Running from source
    needs the command line entry point, because the modules are not
    scripts.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {command}'
    return f'"{sys.executable}" -m winsleep.cli {command}'


def install_daemon_task() -> tuple[bool, str]:
    """Create the task that starts the daemon at logon."""
    command = _module_args("daemon")
    args = [
        "schtasks",
        "/Create",
        "/TN",
        DAEMON_TASK,
        "/TR",
        command,
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
    command = _module_args("agent")
    args = [
        "schtasks",
        "/Create",
        "/TN",
        AGENT_TASK,
        "/TR",
        command,
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


def install_tray_task() -> tuple[bool, str]:
    """Create the task that starts the program at every logon.

    The task runs in the interactive session, because the icon in the
    notification area needs a desktop to live on.  It is a separate
    task from the daemon, so the daemon keeps working even when the
    window is not there.
    """
    command = _module_args("tray")
    args = [
        "schtasks",
        "/Create",
        "/TN",
        TRAY_TASK,
        "/TR",
        command,
        "/SC",
        "ONLOGON",
        "/IT",
        "/F",
    ]
    result = _run(args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "schtasks failed"
    return True, TRAY_TASK


def start_tray() -> tuple[bool, str]:
    """Start the notification area icon right now."""
    if not task_exists(TRAY_TASK):
        return False, "the tray task is not installed yet"
    result = _run(["schtasks", "/Run", "/TN", TRAY_TASK])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "the tray did not start"
    return True, "the tray was started"


def start_agent() -> tuple[bool, str]:
    """Start the agent task right now."""
    result = _run(["schtasks", "/Run", "/TN", AGENT_TASK])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "schtasks failed"
    return True, "the agent was started"


def kill_processes(sub_command: str) -> int:
    """Kill the processes that run one of our sub commands.

    Ending the scheduled task is not enough: the program is a single
    file, and the process that the task started can outlive it.  The
    processes are found by their command line and stopped directly.
    """
    try:
        import psutil
    except ImportError:
        return 0

    killed = 0
    try:
        for process in psutil.process_iter(["cmdline", "pid"]):
            try:
                cmdline = process.info.get("cmdline") or []
                pid = process.info.get("pid")
            except Exception:  # noqa: BLE001 - psutil raises many things
                continue
            if not cmdline or pid is None:
                continue
            # The last part of the command line is the sub command.
            if not any(part.lower() == sub_command for part in cmdline[-1:]):
                continue
            # Only our own program is stopped.
            if not any("DeepSleep" in part for part in cmdline):
                continue
            try:
                process.terminate()
                killed += 1
            except Exception:  # noqa: BLE001 - it may already be gone
                continue
    except Exception as exc:  # noqa: BLE001
        log.warning("could not stop the processes: %s", exc)
    return killed


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
    """Register the tasks.

    The tray task is the one that may fail: an interactive task needs
    the password of the user when it is created from a command line,
    and some machines refuse it.  The other two are what the program
    really needs, so a failure of the tray does not fail the whole
    installation.
    """
    ok, detail = install_daemon_task()
    if not ok:
        return False, detail
    ok, detail = install_agent_task()
    if not ok:
        return False, detail

    messages = ["the tasks were registered"]
    tray_ok, tray_detail = install_tray_task()
    if tray_ok:
        messages.append(f"{TRAY_TASK} was registered")
    else:
        messages.append(f"开机自启没有装上：{tray_detail}")
    return True, ", ".join(messages)


def uninstall_all() -> tuple[bool, str]:
    """Remove both tasks and stop whatever they started."""
    messages = []
    for name in (DAEMON_TASK, AGENT_TASK, TRAY_TASK):
        if task_exists(name):
            stop_task(name)
            remove_task(name)
            messages.append(f"{name} was removed")

    # The processes are stopped as well.  Removing the task does not
    # stop a process that is already running.
    for command in ("daemon", "agent", "tray"):
        killed = kill_processes(command)
        if killed:
            messages.append(f"{killed} {command} process(es) were stopped")

    try:
        heartbeat_path().unlink(missing_ok=True)
    except OSError:
        pass

    if not messages:
        return True, "nothing was installed"
    return True, ", ".join(messages)


# ------------------------------------------------------------------ service


# The agent writes the time it was last alive into this file.  It is
# the only proof that the agent is running, because the agent has no
# port of its own.
AGENT_HEARTBEAT = "agent.alive"

# How long a heartbeat stays good.  The agent writes it every five
# seconds, so twenty seconds is generous enough to survive a busy
# moment and short enough that a stopped agent is noticed right away.
HEARTBEAT_SECONDS = 20


def heartbeat_path():
    """The file the agent writes its heartbeat into."""
    from . import paths

    return paths.app_dir() / AGENT_HEARTBEAT


def write_heartbeat() -> None:
    """Say that the agent is alive."""
    import time

    try:
        path = heartbeat_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()), encoding="utf-8")
    except OSError as exc:
        log.debug("could not write the heartbeat: %s", exc)


def _heartbeat_is_fresh() -> bool:
    """True when the agent wrote its heartbeat recently enough."""
    import time

    try:
        stamp = float(heartbeat_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return (time.time() - stamp) < HEARTBEAT_SECONDS


def _daemon_answers() -> bool:
    """True when the daemon answers on its port."""
    import json
    import socket

    from . import paths

    try:
        port = int(paths.socket_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
            conn.sendall(json.dumps({"kind": "ping"}).encode("utf-8") + b"\n")
            conn.settimeout(2)
            reply = conn.recv(4096)
    except OSError:
        return False

    if not reply:
        return False
    try:
        return bool(json.loads(reply.decode("utf-8").splitlines()[0]).get("ok"))
    except (ValueError, json.JSONDecodeError):
        return False


def daemon_running() -> bool:
    """True when the daemon is running right now.

    The daemon listens on a local port, so the honest way to ask is to
    talk to it.  Looking for a process with "daemon" in its command
    line gave the wrong answer: the word can be anywhere, and it said
    yes while nothing was running.
    """
    return _daemon_answers()


def agent_running() -> bool:
    """True when the agent is running right now.

    The agent writes a heartbeat file while it is alive.  The file is
    only there when the agent is running, so there is nothing to guess
    about.
    """
    return _heartbeat_is_fresh()


def service_installed() -> bool:
    """True when the background tasks are registered."""
    return task_exists(DAEMON_TASK)


def tray_installed() -> bool:
    """True when the task that starts the program at logon is there."""
    return task_exists(TRAY_TASK)


def start_daemon() -> tuple[bool, str]:
    """Start the daemon task right now."""
    if not task_exists(DAEMON_TASK):
        return False, "the background service is not installed yet"
    result = _run(["schtasks", "/Run", "/TN", DAEMON_TASK])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "the daemon did not start"
    return True, "the background service was started"


def stop_daemon() -> tuple[bool, str]:
    """Stop the daemon and the agent."""
    messages = []
    for name in (DAEMON_TASK, AGENT_TASK):
        if task_exists(name):
            stop_task(name)
            messages.append(f"{name} was stopped")

    # The processes themselves are stopped as well.  Ending the task
    # is not enough: the program is a single file, and the process
    # can outlive the task that started it.
    for command in ("daemon", "agent"):
        killed = kill_processes(command)
        if killed:
            messages.append(f"{killed} {command} process(es) were stopped")

    # The heartbeat is taken away as well.  Without this the window
    # would say the agent is still running for a while after it was
    # stopped.
    try:
        heartbeat_path().unlink(missing_ok=True)
    except OSError as exc:
        log.debug("could not remove the heartbeat: %s", exc)

    if not messages:
        return True, "nothing was running"
    return True, ", ".join(messages)


def service_status() -> dict[str, object]:
    """What the interface needs to draw the state of the service."""
    return {
        "installed": service_installed(),
        "running": daemon_running(),
        "agent_running": agent_running(),
        "tray_installed": tray_installed(),
    }


# ----------------------------------------------------------------- schedule


def schedule_sleep(clock: str, date: str = "") -> tuple[bool, str]:
    """Put the machine to sleep at a given time.

    The scheduled task runs this program again with the ``sleep``
    command, so the machine is put to sleep by the same code that the
    rest of the program uses.  *clock* is ``HH:MM`` and *date* is
    ``YYYY/MM/DD``; without a date the next occurrence is used.
    """
    if not task_exists(DAEMON_TASK):
        return False, "the background service is not installed yet"

    command = _module_args("sleep")
    args = [
        "schtasks",
        "/Create",
        "/TN",
        SCHEDULE_TASK,
        "/TR",
        command,
        "/SC",
        "ONCE",
        "/ST",
        clock,
        "/F",
    ]
    if date:
        args += ["/SD", date]
    result = _run(args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "the schedule could not be set"
    return True, f"the machine will sleep at {clock}"


def cancel_scheduled_sleep() -> tuple[bool, str]:
    """Cancel the scheduled sleep."""
    if not task_exists(SCHEDULE_TASK):
        return True, "nothing was scheduled"
    ok, detail = remove_task(SCHEDULE_TASK)
    if not ok:
        return False, detail
    return True, "the scheduled sleep was cancelled"
