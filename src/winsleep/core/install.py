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
from pathlib import Path

log = logging.getLogger("winsleep.install")

DAEMON_TASK = "DeepSleepForWindows-Daemon"
AGENT_TASK = "DeepSleepForWindows-Agent"
SCHEDULE_TASK = "DeepSleepForWindows-Schedule"
TRAY_TASK = "DeepSleepForWindows-Tray"

#: The error code Windows gives when a task cannot find the file it
#: runs.  It is the usual failure of a task that points at a place
#: where the program is not any more.
_FILE_NOT_FOUND = 0x80070002



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


def program_command(command: str) -> tuple[Path, str]:
    """The executable and the arguments that run one of our sub commands.

    A frozen build is one executable with sub commands.  The path is
    **not** the path of the file that happens to be running: it is the
    copy the program keeps in its own folder.  That path never changes,
    so a task registered today still points at a program after the user
    has moved or deleted the file they first downloaded.

    Running from the source needs the command line entry point, because
    the modules are not scripts.
    """
    if getattr(sys, "frozen", False):
        from . import bootstrap

        executable = bootstrap.installed_exe()
        if not executable.exists():
            # The copy has not been made yet - a first run that never
            # installed anything.  The running file is the best answer
            # there is, and ``ensure_installed`` will put the copy
            # there before the tasks are registered.
            executable = Path(sys.executable)
        return executable, command
    return Path(sys.executable), f"-m winsleep.cli {command}"


def _module_args(command: str) -> str:
    """The command line that runs one of our sub commands."""
    executable, arguments = program_command(command)
    return f'"{executable}" {arguments}'


def task_command(command: str) -> str:
    """The command line a task runs.

    This is the same as what the tasks are created with, and it is
    used again when the program replaces itself - the tasks have to
    end up pointing at the new file, with the same sub command.
    """
    return _module_args(command)



def install_daemon_task() -> tuple[bool, str]:
    """Create the task that runs the daemon.

    The daemon is registered from XML rather than with the command line
    switches, because only the XML can say "start this again if it
    stops" - which is what keeps the background half from quietly
    disappearing.
    """
    from . import tasks

    executable, arguments = program_command("daemon")
    return tasks.register_task(
        DAEMON_TASK,
        executable,
        arguments,
        description="DeepSleep For Windows — 电源计划与规则",
        delay_seconds=20,
    )

def install_agent_task() -> tuple[bool, str]:
    """Create the task that runs the agent in the logged on session.

    The agent has to be in the session with the keyboard, so its
    principal is the user group with an interactive token.  No password
    is stored, and none is asked for - which is what makes the task
    possible to register from a program that is not running as that
    user.
    """
    from . import tasks

    executable, arguments = program_command("agent")
    return tasks.register_task(
        AGENT_TASK,
        executable,
        arguments,
        description="DeepSleep For Windows — 空闲时间与锁屏",
        delay_seconds=25,
    )

def install_tray_task() -> tuple[bool, str]:
    """Create the task that puts the icon in the notification area.

    It runs at logon in the user's session, because the icon needs a
    desktop to live on.
    """
    from . import tasks

    executable, arguments = program_command("tray")
    return tasks.register_task(
        TRAY_TASK,
        executable,
        arguments,
        description="DeepSleep For Windows — 托盘图标",
        delay_seconds=30,
    )

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


def _last_task_result(name: str) -> tuple[int | None, str]:
    """The result of the last run of a task, and its text.

    The number is what Windows reports: ``0`` means it worked.  The
    usual failure of a task that points at a file that is not there
    any more is ``0x80070002``.
    """
    result = _run(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"])
    if result.returncode != 0:
        return None, ""
    text = (result.stdout or "") + (result.stderr or "")
    code: int | None = None
    for line in text.splitlines():
        if "Last Task Result" in line or "上次任务结果" in line:
            _, _, value = line.partition(":")
            value = value.strip()
            try:
                code = int(value, 0)
            except ValueError:
                code = None
    return code, ""


def task_points_at(name: str, executable: str) -> bool:
    """True when the task runs the given file.

    A task holds the path of the program it starts.  When the program
    is moved, the task still points at the old place and every run
    fails with "the file was not found".  This is how that is noticed
    before the user is told the service started.
    """
    result = _run(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"])
    if result.returncode != 0:
        return False
    text = (result.stdout or "") + (result.stderr or "")
    wanted = str(executable).lower()
    for line in text.splitlines():
        if "Task To Run" not in line and "要运行的任务" not in line:
            continue
        _, _, value = line.partition(":")
        if wanted in value.lower():
            return True
    return False



def install_all() -> tuple[bool, str]:
    """Put the program in its own folder and register the tasks.

    The copy comes first.  The tasks name the copy, not the file the
    user downloaded, so that a task keeps working after the download
    has been moved or deleted - which is one of the ways a background
    half disappears without anything saying why.

    The tray task is the one that may fail without failing the whole
    installation: the daemon and the agent are what the program really
    needs.
    """
    from . import bootstrap

    ok, detail = bootstrap.ensure_installed()
    if not ok:
        # A copy that could not be made is worth saying out loud: the
        # tasks would point at a file that is not there.
        return False, f"程序副本没能放好：{detail}"

    ok, detail = install_daemon_task()
    if not ok:
        return False, detail
    ok, detail = install_agent_task()
    if not ok:
        return False, detail

    messages = ["后台服务已安装"]
    tray_ok, tray_detail = install_tray_task()
    if tray_ok:
        messages.append("托盘图标已加入开机启动")
    else:
        messages.append(f"开机自启没有装上：{tray_detail}")

    # A logon trigger waits for a logon that has already happened, so
    # the tasks are started here as well.  Otherwise the program is
    # installed and does nothing at all until the machine restarts.
    started_ok, started_detail = start_all()
    messages.append(started_detail if started_ok else f"没能启动：{started_detail}")

    return True, "，".join(messages)


def tasks_are_current() -> tuple[bool, str]:
    """True when the tasks point at the program that is running now.

    When the program was moved, the tasks still point at the old
    place and every start fails with "the file was not found".  This
    is what notices that, so the program can offer to fix it instead
    of saying the service started.
    """
    if not getattr(sys, "frozen", False):
        return True, ""
    from . import bootstrap

    executable = str(bootstrap.installed_exe()).lower()
    for name in (DAEMON_TASK, AGENT_TASK, TRAY_TASK):
        if not task_exists(name):
            continue
        if task_points_at(name, executable):
            continue
        return False, name
    return True, ""



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

#: The tray writes its own heartbeat, so "is the icon there?" can be
#: answered without guessing.
TRAY_HEARTBEAT = "tray.alive"

# How long a heartbeat stays good.  The agent writes it every five
# seconds, so twenty seconds is generous enough to survive a busy
# moment and short enough that a stopped agent is noticed right away.
HEARTBEAT_SECONDS = 20


def heartbeat_path():
    """The file the agent writes its heartbeat into."""
    from . import paths

    return paths.app_dir() / AGENT_HEARTBEAT


def write_heartbeat(name: str = AGENT_HEARTBEAT) -> None:
    """Say that one of the background halves is alive."""
    import time

    try:
        path = heartbeat_path().parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()), encoding="utf-8")
    except OSError as exc:
        log.debug("could not write the heartbeat: %s", exc)


def _heartbeat_is_fresh(name: str = AGENT_HEARTBEAT) -> bool:
    """True when a heartbeat file was written recently enough.

    *name* is the file to look at: the agent and the tray each have
    one, because they are separate processes that fail separately.
    """
    import time

    try:
        path = heartbeat_path().parent / name
        stamp = float(path.read_text(encoding="utf-8").strip())
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


def start_all() -> tuple[bool, str]:
    """Start every task right now.

    A task with a logon trigger does not run the moment it is
    registered - the logon it waits for has already happened.  Without
    this, installing would leave the machine with three tasks and
    nothing running until the next restart, and the icon in the
    notification area would be missing until then.
    """
    started = []
    failed = []
    for name in (DAEMON_TASK, AGENT_TASK, TRAY_TASK):
        if not task_exists(name):
            failed.append(f"{name} is not registered")
            continue
        result = _run(["schtasks", "/Run", "/TN", name])
        if result.returncode == 0:
            started.append(name)
        else:
            detail = (result.stderr or result.stdout or "").strip()
            failed.append(f"{name}: {detail or 'the task did not start'}")

    if failed and not started:
        return False, "; ".join(failed)

    message = f"{len(started)} 个任务已启动"
    if failed:
        message += "；" + "；".join(failed)
    return True, message


def tray_running() -> bool:
    """True when the icon in the notification area is there.

    The tray writes a heartbeat of its own, so this answers the
    question the user is really asking - "is the icon there?" - rather
    than "is a task registered?".
    """
    return _heartbeat_is_fresh(TRAY_HEARTBEAT)


def start_daemon() -> tuple[bool, str]:
    """Start the daemon task right now.

    ``schtasks /Run`` only says that the request was accepted.  The
    task then fails on its own when the file it points at is not
    there, and the program has to say so instead of telling the user
    it started.  The result of the task is read after it, and the
    daemon is asked whether it answers.
    """
    if not task_exists(DAEMON_TASK):
        return False, "the background service is not installed yet"
    result = _run(["schtasks", "/Run", "/TN", DAEMON_TASK])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or "the daemon did not start"
    return _confirm_daemon_started("the background service was started")


def _confirm_daemon_started(success_message: str) -> tuple[bool, str]:
    """Check that the daemon really came up, and say what happened.

    The task can be accepted and still fail - the usual reason is that
    the file it points at is gone, because the program was moved.  The
    result of the last run tells that apart: ``0x80070002`` means the
    file was not found.
    """
    import time

    # The daemon needs a moment to unpack itself and open its port.
    for _ in range(20):
        if daemon_running():
            return True, success_message
        time.sleep(0.25)

    code, detail = _last_task_result(DAEMON_TASK)
    if code is not None and code != 0:
        if code == _FILE_NOT_FOUND:
            return False, (
                "后台服务启动失败：找不到程序文件。"
                "多半是程序被移动过或者被删掉了，"
                "重新点一次「安装并启动」就会按现在的位置重建任务"
            )
        return False, f"后台服务启动失败（任务返回码 {code:#x}）{detail}"
    return False, "后台服务没有启动起来，详见日志"




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
    current, stale = tasks_are_current()
    return {
        "installed": service_installed(),
        "running": daemon_running(),
        "agent_running": agent_running(),
        "tray_installed": tray_installed(),
        "tray_running": tray_running(),
        # True when the tasks still point at a place where the program
        # is not any more.  The interface says so instead of leaving
        # the user wondering why the service does not start.
        "stale": not current,
        "stale_task": stale,
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
