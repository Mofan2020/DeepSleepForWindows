"""Updating the program from a GitHub release.

The program is one file, and the file is the program.  Updating it
means replacing that file - but the file is running, and the scheduled
tasks point at it.  Three things have to happen in the right order:

1.  The new version is downloaded and checked.
2.  The background service is stopped, so that nothing is holding the
    file open.
3.  The file is replaced, the tasks are pointed at the new file, and
    the program starts again.

Step 3 cannot be done by the running program: once it exits there is
no code left to do it.  So the work is handed to a small script in the
temporary directory, the same way the macOS version does it.  The
script waits for the program to exit, replaces the file, fixes the
tasks and starts the program again.

Where the program lives is **not** remembered in the settings.  The
executable knows its own path, and that is the truth: the user may
have moved it, and the settings would still name the old place.

Every step that touches the machine writes what it did to a result
file.  The program reads that file the next time it starts, so the
user learns whether the update worked even if they had to restart the
computer in between.

Everything here is plain Python and works off Windows, so the parts
that can be checked anywhere are unit tested.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

__all__ = [
    "RELEASE_OWNER",
    "RELEASE_REPO",
    "ASSET_NAME",
    "EXECUTABLE_NAME",
    "current_version",
    "executable_path",
    "is_newer",
    "parse_version",
    "read_result",
    "release_url",
    "updater_script",
    "write_result",
]

log = logging.getLogger("winsleep.update")

#: Where the releases live.
RELEASE_OWNER = "Mofan2020"
RELEASE_REPO = "DeepSleepForWindows"

#: The name of the asset in the release.
ASSET_NAME = "DeepSleepForWindows-Windows-x64.zip"

#: The name of the executable inside the zip.
EXECUTABLE_NAME = "DeepSleepForWindows.exe"

#: The tag of a release, for example ``v0.3.1``.
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(tag: str) -> tuple[int, int, int] | None:
    """The version of a release tag, or ``None`` when it cannot be read.

    A tag that cannot be read is not a version.  Treating it as one
    would make the program compare nonsense and possibly install a
    release that is older than the one it is running.
    """
    match = _VERSION_RE.match((tag or "").strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def current_version() -> str:
    """The version of the running program."""
    from ..version import __version__

    return __version__


def is_newer(tag: str, than: str | None = None) -> bool:
    """True when the tag names a version newer than the given one.

    A tag that cannot be read is never newer.  That is the safe
    direction: the worst case is that no update is offered, while the
    other direction would install an older version over a newer one.
    """
    wanted = parse_version(tag)
    if wanted is None:
        return False
    other = parse_version(than or current_version())
    if other is None:
        return False
    return wanted > other


def release_url(tag: str | None = None) -> str:
    """The address of a release, or of the list of releases."""
    if tag:
        return f"https://github.com/{RELEASE_OWNER}/{RELEASE_REPO}/releases/tag/{tag}"
    return f"https://github.com/{RELEASE_OWNER}/{RELEASE_REPO}/releases/latest"


def executable_path() -> Path:
    """The file this program is running from.

    In a frozen build that is the executable itself.  Running from
    source there is no single file, so the path of the Python
    interpreter is used instead - the program cannot replace itself
    then, and says so.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(sys.executable)


def is_frozen() -> bool:
    """True when the program is a single file that can replace itself."""
    return bool(getattr(sys, "frozen", False))


# --------------------------------------------------------------- the result


def result_path() -> Path:
    """The file the updater writes what it did into."""
    from . import paths

    return paths.app_dir() / "update-result.json"


def write_result(status: str, version: str, message: str = "") -> None:
    """Write what the update did, so the next start can tell the user."""
    target = result_path()
    payload = {
        "status": status,
        "version": version,
        "message": message,
        "at": int(_now()),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("the update result could not be written: %s", exc)


def read_result() -> dict[str, Any]:
    """What the last update did, or an empty dictionary."""
    target = result_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def clear_result() -> None:
    """Take the result of the last update away, so it is not shown twice."""
    try:
        result_path().unlink()
    except OSError:
        pass


def _now() -> float:
    import time

    return time.time()


# ------------------------------------------------------------- the archive


def extract_executable(archive: Path, target_dir: Path) -> Path:
    """Take the executable out of the release archive.

    The zip holds exactly one file, but the program does not trust
    that: it looks for the executable by name and refuses anything
    else.  A zip that holds a path with ``..`` in it would write
    outside the directory, so the member name is checked before
    anything is written.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.namelist()
        wanted = [name for name in members if Path(name).name == EXECUTABLE_NAME]
        if len(wanted) != 1:
            raise ValueError(
                f"the archive holds {len(wanted)} files named {EXECUTABLE_NAME}, expected one"
            )
        name = wanted[0]
        if name.startswith("/") or ".." in Path(name).parts:
            raise ValueError("the archive holds a file outside its directory")
        destination = target_dir / EXECUTABLE_NAME
        with bundle.open(name) as source, open(destination, "wb") as out:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    return destination


# ------------------------------------------------------------ the updater


def updater_script(
    target: str,
    source: str,
    work_directory: str,
    pid: int,
    result_path: str,
    version: str,
    tasks: list[tuple[str, str]] | None = None,
    result_dir: str = "",
) -> str:
    """The script that replaces the program while it is running.

    It is written to the temporary directory, because the directory
    it replaces is the one it would disappear with.

    The steps are the ones that make the replacement work on a machine
    that runs the program as a background service:

    *   wait for the program to exit,
    *   stop the scheduled tasks, so that nothing holds the file open
        and nothing starts the old file again,
    *   move the old file aside instead of deleting it, so a failure
        can be undone,
    *   put the new file in place, and move the old one back when that
        fails,
    *   point the scheduled tasks at the new file,
    *   start the program again.

    The result is written to a file, because the program that would
    report it is the one that just died.

    *tasks* is a list of pairs: the name of the task and the command
    line it runs.  The command line has the sub command in it - the
    daemon runs the daemon, the agent runs the agent - so it cannot be
    worked out from the name alone.
    """
    pairs = tasks or []
    change_lines = "\n".join(
        f'schtasks /Change /TN "{name}" /TR "{command}" >nul 2>&1'
        for name, command in pairs
    )
    first_task = pairs[0][0] if pairs else ""
    return f"""@echo off
rem Written by DeepSleep For Windows: it replaces the program after the
rem program has closed itself, and starts it again.
setlocal EnableDelayedExpansion

rem The working directory is removed at the end, so the script must
rem not stand in it - a directory cannot be removed while something
rem is inside it.
cd /d "%TEMP%"

set "TARGET={target}"
set "SOURCE={source}"
set "OLD=%TARGET%.old"
set "RESULT={result_path}"
set "RESULTDIR={result_dir}"
set "VERSION={version}"

rem The directory of the result may be gone - the program makes it when
rem it starts, and this runs after the program closed.
if not exist "%RESULTDIR%" mkdir "%RESULTDIR%" >nul 2>&1

rem The result of the last run must not be mistaken for this one.
del /q "%RESULT%" >nul 2>&1

rem Wait for the program to close.  Replacing a file that is being
rem run gives an unpredictable result.
set /a WAIT=0
:waitloop
tasklist /fi "PID eq {pid}" /nh 2>nul | find "{pid}" >nul
if errorlevel 1 goto waited
set /a WAIT+=1
if !WAIT! GEQ 150 goto waited
ping -n 2 127.0.0.1 >nul
goto waitloop
:waited

rem Stop the tasks, so that nothing is holding the file open and
rem nothing starts the old file while it is being replaced.
schtasks /End /TN "{first_task}" >nul 2>&1

rem Move the old file aside rather than deleting it.  If the new file
rem cannot be put in place, the old one goes back and the program is
rem still there.
del /q "%OLD%" >nul 2>&1
move /y "%TARGET%" "%OLD%" >nul 2>&1
if not exist "%OLD%" goto move_failed

copy /y "%SOURCE%" "%TARGET%" >nul 2>&1
if not exist "%TARGET%" goto put_failed

rem The new file is in place.  The tasks have to point at it, and the
rem program has to start again.
{change_lines}
schtasks /Run /TN "{first_task}" >nul 2>&1

rem The result is written before the working directory is removed: the
rem result has to survive, and the directory may hold the place the
rem script itself runs from.
call :write_result "success" "updated to %VERSION%"

del /q "%OLD%" >nul 2>&1
rd /s /q "{work_directory}" >nul 2>&1
exit /b 0

:put_failed
rem The new file did not fit.  Put the old one back - a program that
rem does not run is worse than one that is a version behind.
move /y "%OLD%" "%TARGET%" >nul 2>&1
call :write_result "failure" "the new version did not fit; the old one was put back"
exit /b 1

:move_failed
call :write_result "failure" "the old version could not be moved; administrator rights may be needed"
exit /b 1

rem Write what the update did into the result file.  The message is
rem written on its own line, because the braces of the JSON confuse
rem the command shell.
:write_result
>"%RESULT%" echo {{"status":"%~1","version":"%VERSION%","message":"%~2"}}
exit /b 0
"""


def write_updater_script(
    target: Path,
    source: Path,
    work_directory: Path,
    pid: int,
    version: str,
    tasks: list[tuple[str, str]] | None = None,
) -> Path:
    """Write the updater script and return where it was written.

    The script lives in the temporary directory of the user, not next
    to the program: the program is what gets replaced, and the script
    has to survive that.
    """
    work_directory.mkdir(parents=True, exist_ok=True)
    script = work_directory / "updater.cmd"
    script.write_text(
        updater_script(
            target=str(target),
            source=str(source),
            work_directory=str(work_directory),
            pid=pid,
            result_path=str(result_path()),
            version=version,
            tasks=tasks,
            result_dir=str(result_path().parent),
        ),
        encoding="utf-8",
    )
    return script


def start_updater(script: Path) -> bool:
    """Start the updater script and let it outlive this process.

    The script waits for this program to exit, so it has to be a
    separate process that does not die with it.
    """
    if sys.platform != "win32":
        return False
    try:
        import subprocess

        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            ["cmd", "/c", "start", "", "/b", str(script)],
            creationflags=flags,
            close_fds=True,
        )
    except Exception as exc:  # noqa: BLE001 - the update has to say it failed
        log.warning("the updater could not be started: %s", exc)
        return False
    return True


def work_directory() -> Path:
    """A directory of its own for this update."""
    import tempfile

    return Path(tempfile.gettempdir()) / f"winsleep-update-{os.getpid()}"
