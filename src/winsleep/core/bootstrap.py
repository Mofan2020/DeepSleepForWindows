"""A copy of the program that stays where it is.

The program is one file, and a one-file build unpacks itself into a
temporary directory every time it starts.  That is fine for a program
the user opens and closes, and it is a problem for one that has to keep
running in the background:

*   The scheduled tasks name a path.  If the user downloaded the program
    into ``Downloads`` and later moved or deleted it, the tasks point at
    nothing and the background half is simply gone.
*   Two copies of the program in two places are two different programs
    as far as Windows is concerned, and only one of them has the tasks.

So the program keeps **one** copy of itself in a folder of its own and
runs from there.  The folder is the same for every run, which makes the
path in the tasks stable.  What is in the folder is checked against what
is running, with a SHA-256 of the file, so replacing the program means
the copy is replaced as well.

The folder is under ``%LOCALAPPDATA%`` because that is the one place a
program may write to without administrator rights.  Copying the program
there is therefore something the plain user can do, and installing the
scheduled tasks is the only step that needs to be elevated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
from pathlib import Path

__all__ = [
    "MANIFEST_NAME",
    "ensure_installed",
    "fingerprint",
    "installed_dir",
    "installed_exe",
    "is_installed_copy",
    "manifest_path",
    "read_manifest",
]

log = logging.getLogger("winsleep.bootstrap")

#: The name of the file that says which version is in the folder.
MANIFEST_NAME = "manifest.json"

#: The name of the program itself.
EXE_NAME = "DeepSleepForWindows.exe"


def _local_app_data() -> Path:
    """``%LOCALAPPDATA%``, or a sensible place off Windows."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local)
    return Path.home() / ".local" / "share"


def installed_dir() -> Path:
    """The folder the program keeps its own copy in.

    A test or a developer can point this somewhere else with
    ``WINSLEEP_PROGRAM_DIR``, which keeps the tests away from the real
    folder.
    """
    override = os.environ.get("WINSLEEP_PROGRAM_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return _local_app_data() / "DeepSleepForWindows" / "app"


def installed_exe() -> Path:
    """The program inside the folder of its own."""
    return installed_dir() / EXE_NAME


def manifest_path() -> Path:
    """The file that says which version is in the folder."""
    return installed_dir() / MANIFEST_NAME


def fingerprint(path: Path) -> str:
    """The SHA-256 of a file, as lower case hex.

    A file that cannot be read has no fingerprint.  An empty string is
    never equal to a real one, so a missing file is always "different".
    """
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        log.debug("could not read %s: %s", path, exc)
        return ""
    return digest.hexdigest()


def read_manifest() -> dict[str, object]:
    """What the folder says about itself.

    An unreadable or missing manifest is an empty one, which makes the
    caller copy the program again - the safe direction.
    """
    try:
        data = json.loads(manifest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_manifest(version: str, digest: str) -> None:
    try:
        manifest_path().write_text(
            json.dumps(
                {"version": version, "sha256": digest, "file": EXE_NAME},
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("could not write the manifest: %s", exc)


def is_installed_copy() -> bool:
    """True when this process is the copy inside the folder."""
    if not getattr(sys, "frozen", False):
        return False
    try:
        return Path(sys.executable).resolve() == installed_exe().resolve()
    except OSError:
        return False


def ensure_installed() -> tuple[bool, str]:
    """Make the copy in the folder the same as the running program.

    Returns whether the folder is usable and a line saying what
    happened.  A run from the source has nothing to copy, so it says so
    and gets out of the way.
    """
    if not getattr(sys, "frozen", False):
        return True, "running from the source, nothing to copy"

    running = Path(sys.executable)
    try:
        target = installed_exe()
        if running.resolve() == target.resolve():
            # This is the copy already.  Nothing to do.
            return True, "running from the copy"
    except OSError:
        pass

    mine = fingerprint(running)
    if not mine:
        return False, f"the program could not be read: {running}"

    theirs = fingerprint(installed_exe()) if installed_exe().exists() else ""
    if theirs == mine:
        # The file is there and it is the same one.  The manifest may
        # still be missing, because a copy made by an older version did
        # not write one - put it back.
        if not manifest_path().exists():
            from ..version import __version__

            _write_manifest(__version__, mine)
        return True, "the copy is already up to date"

    # The copy is missing or it is another version.  Put this one there.
    try:
        installed_dir().mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"the folder could not be made: {exc}"

    try:
        # ``copy2`` keeps the modification time, which makes the file
        # look its age.  A plain copy is enough here.
        shutil.copyfile(running, installed_exe())
    except OSError as exc:
        return False, f"the copy could not be written: {exc}"

    # Read the copy back.  A copy that was cut short - a full disk, a
    # file that is still being written - must not be taken for a good
    # one, and the only way to know is to look.
    written = fingerprint(installed_exe())
    if written != mine:
        return False, "the copy does not match the program"

    from ..version import __version__

    _write_manifest(__version__, mine)
    log.info("the program was copied to %s", installed_dir())
    return True, f"copied to {installed_dir()}"
