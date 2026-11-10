"""Becoming an administrator.

Two things need administrator rights: registering the scheduled tasks,
and changing the settings of a power plan.  The window asks the daemon
for the second one, but the first one it has to do itself.

The way to get administrator rights on Windows is to start the program
again with the ``runas`` verb, which makes Windows ask the user.  That is
what this module does, in one place, so that every place that needs it
does it the same way.

The command line is put together carefully.  The program is started by
double clicking, so ``sys.argv`` holds the path of the executable and
nothing else - and the path may contain spaces, which is why every
argument is quoted.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger("winsleep.elevate")

__all__ = [
    "ELEVATION_MARKER",
    "elevate",
    "elevate_and_exit",
    "is_admin",
    "relaunch_arguments",
    "was_elevated",
    "with_marker",
]

#: The flag that says "this second instance was started to get
#: administrator rights".  Without it an elevated instance would ask
#: for elevation again, and the user would get the prompt forever.
ELEVATION_MARKER = "--winsleep-elevated"


def is_admin() -> bool:
    """True when the process already has administrator rights.

    On a machine that is not Windows there is nothing to elevate, so
    the answer is always yes.  That keeps the rest of the program free
    of platform checks.
    """
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 - the call can fail on a locked down machine
        return False


def relaunch_arguments(argv: list[str] | None = None) -> str:
    """The command line the elevated copy has to be started with.

    The path of the program comes first, then the arguments that were
    given, without the program path itself.  Every part is quoted, so a
    path with a space in it survives.
    """
    source = list(argv if argv is not None else sys.argv)
    program = source[0] if source else sys.executable
    parts = [_quote(program)]
    parts.extend(_quote(part) for part in source[1:])
    return " ".join(parts)


def _quote(text: str) -> str:
    """Quote one argument so Windows passes it on as it is.

    Every part is quoted, not only the ones that need it.  Quoting an
    argument that does not need it is harmless, and not quoting one
    that does is not - so the rule is the simple one.
    """
    return '"' + text.replace('"', '\\"') + '"'


def elevate(argv: list[str] | None = None) -> bool:
    """Start the program again with administrator rights.

    Returns whether the elevated copy was started.  The caller is
    expected to exit afterwards - the copy is a second process.
    """
    if sys.platform != "win32":
        return False
    try:
        executable = sys.executable
        # In a frozen build, sys.executable is the .exe path.
        # We need to pass the marker so the elevated copy knows not to
        # ask for elevation again.
        marker = ELEVATION_MARKER
        source = list(argv if argv is not None else sys.argv)
        if marker not in source:
            # Insert marker after the executable path
            source = [source[0], marker] + source[1:]
        arguments = " ".join(_quote(part) for part in source[1:])
        log.info("elevating: executable=%s arguments=%s", executable, arguments)
        # SW_SHOWNORMAL = 1.  The window has to be visible, because
        # the user has to see it.
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, arguments, None, 1
        )
    except Exception as exc:  # noqa: BLE001 - a refused prompt is not a crash
        log.error("elevation failed: %s", exc)
        return False
    # ShellExecuteW returns a value above 32 when it worked.  Anything
    # below that is an error, and 5 is "access denied" - which is what
    # the user gets when they click "no" on the prompt.
    result_code = int(result)
    if result_code <= 32:
        log.error("elevation failed with code %d", result_code)
        return False
    log.info("elevation succeeded")
    return True


def elevate_and_exit(argv: list[str] | None = None) -> bool:
    """Start the elevated copy and end this one.

    The marker is added to the command line so the elevated copy knows
    that it does not have to ask again.
    """
    source = list(argv if argv is not None else sys.argv)
    started = elevate(with_marker(source))
    if started:
        sys.exit(0)
    return False


def with_marker(argv: list[str]) -> list[str]:
    """The command line with the marker in it.

    The marker is only added when it is not there yet - a copy that
    was started to get administrator rights must not ask a second
    time.
    """
    source = list(argv)
    if ELEVATION_MARKER in source:
        return source
    if not source:
        return [ELEVATION_MARKER]
    return [source[0], ELEVATION_MARKER, *source[1:]]


def was_elevated(argv: list[str] | None = None) -> bool:
    """True when this copy was started to get administrator rights."""
    source = argv if argv is not None else sys.argv
    return ELEVATION_MARKER in source
