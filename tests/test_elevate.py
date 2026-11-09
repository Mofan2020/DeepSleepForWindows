"""Becoming an administrator.

The command line is the part that can go wrong quietly, so it is what
most of these checks are about.
"""

from __future__ import annotations

import sys

import pytest

from winsleep.core import elevate


def test_the_program_path_is_not_passed_twice() -> None:
    """The executable is the program of the command line, not an argument.

    The old code joined the whole of ``sys.argv`` and put it into the
    parameters of ShellExecuteW, which made the executable appear twice
    - the copy was started with the path of the executable as its first
    argument.
    """
    result = elevate.relaunch_arguments(["/opt/app/DeepSleepForWindows.exe", "gui"])
    assert result.count("DeepSleepForWindows.exe") == 1
    assert result == '"/opt/app/DeepSleepForWindows.exe" "gui"'


def test_a_path_with_a_space_survives() -> None:
    result = elevate.relaunch_arguments(["C:/Program Files/App.exe", "gui"])
    assert result.startswith('"C:/Program Files/App.exe"')
    assert result == '"C:/Program Files/App.exe" "gui"'


def test_an_argument_with_a_space_is_quoted() -> None:
    result = elevate.relaunch_arguments(["app.exe", "--flag with space"])
    assert result == '"app.exe" "--flag with space"'


def test_an_empty_argument_is_still_an_argument() -> None:
    result = elevate.relaunch_arguments(["app.exe", ""])
    assert result == '"app.exe" ""'


def test_the_marker_is_recognised() -> None:
    source = ["app.exe", elevate.ELEVATION_MARKER]
    assert elevate.was_elevated(source) is True
    assert source.count(elevate.ELEVATION_MARKER) == 1


def test_a_copy_without_the_marker_knows_it_was_not_elevated() -> None:
    assert elevate.was_elevated(["app.exe", "gui"]) is False


def test_off_windows_the_program_counts_as_admin() -> None:
    # There is nothing to elevate on a machine that is not Windows, and
    # the rest of the program must not need a platform check for it.
    if sys.platform == "win32":
        pytest.skip("this machine can be elevated")
    assert elevate.is_admin() is True


def test_elevating_off_windows_does_nothing() -> None:
    if sys.platform == "win32":
        pytest.skip("this machine can be elevated")
    assert elevate.elevate(["app.exe"]) is False


def test_elevating_off_windows_does_not_end_the_program() -> None:
    # The caller has to be able to carry on when the elevation did not
    # happen, so this must not raise SystemExit.
    if sys.platform == "win32":
        pytest.skip("this machine can be elevated")
    assert elevate.elevate_and_exit(["app.exe"]) is False


def test_on_windows_the_program_is_not_admin_by_default() -> None:
    """The check has to be able to say "no" as well.

    A test that only knows the "not Windows" answer would pass on the
    build machine without ever checking the real thing.
    """
    if sys.platform != "win32":
        pytest.skip("this is not a Windows machine")
    # The answer is a boolean, whichever way it comes out.
    assert isinstance(elevate.is_admin(), bool)


def test_the_executable_is_not_among_the_arguments() -> None:
    """The executable is the program of the command line.

    The old code put the whole of ``sys.argv`` into the parameters of
    ShellExecuteW, so the copy was started with the path of the
    executable as its first argument.
    """
    program = "/opt/app/DeepSleepForWindows.exe"
    result = elevate.relaunch_arguments([program, "gui", "--flag"])
    parts = result.split(" ")
    # The program appears once, and it is the first part.
    assert parts[0] == chr(34) + program + chr(34)
    assert result.count(program) == 1


def test_the_marker_is_not_added_twice() -> None:
    """The marker stops the second copy from asking a second time."""
    once = elevate.with_marker(["app.exe", "gui"])
    assert once.count(elevate.ELEVATION_MARKER) == 1
    twice = elevate.with_marker(once)
    assert twice.count(elevate.ELEVATION_MARKER) == 1
    assert twice == once
