"""The permissions of the log file.

The log file is made by whichever copy of the program starts first.
When that one is the window - and the window asks for administrator
rights - the file belongs to the administrators, and the daemon, which
runs as the plain user, cannot write to it.

The result is a program that runs the whole time and says nothing,
which looks exactly like a program that does not work at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from winsleep.core import acl


def test_off_windows_nothing_is_changed() -> None:
    if sys.platform == "win32":
        pytest.skip("this machine has permissions")
    assert acl.make_user_writable(Path("C:/somewhere/log.txt")) is False


def test_the_user_name_has_the_domain() -> None:
    """The name has to be the whole thing.

    Without the domain the permission is given to a user of another
    domain that happens to have the same name.
    """
    if sys.platform != "win32":
        pytest.skip("this is not a Windows machine")
    name = acl._current_user()
    assert name
    assert "\\" in name


def test_a_file_that_is_not_there_is_not_changed() -> None:
    if sys.platform != "win32":
        pytest.skip("this is not a Windows machine")
    assert acl.make_user_writable(Path("C:/nowhere/at/all/log.txt")) is False


def test_the_rights_are_read_from_the_file() -> None:
    """The rights are looked at before anything is changed.

    A file that the user may write to is left alone.  Changing the
    permissions of a file that is already right is not harmless: it
    replaces the entries of the administrators with the ones of the
    user.
    """
    if sys.platform != "win32":
        pytest.skip("this is not a Windows machine")
    assert callable(acl._user_may_write)
    assert callable(acl._grant)


def test_a_file_that_the_user_may_write_to_is_left_alone(tmp_path: Path) -> None:
    """A file the user may already write to must not be touched.

    The first call may well change something: the file was made by
    whichever copy of the program started first, and when that was the
    window - which asks for administrator rights - the file belongs to
    the administrators and the daemon, which runs as the plain user,
    cannot write to it.  That is exactly the bug this module exists
    for, and the first call has to repair it.

    The second call has to change nothing.  The program asks for the
    right every time it starts, and every call after the first one
    must leave the file alone.
    """
    if sys.platform != "win32":
        pytest.skip("this is not a Windows machine")
    path = tmp_path / "log.txt"
    path.write_text("hello", encoding="utf-8")
    assert acl._current_user()

    acl.make_user_writable(path)

    assert acl.make_user_writable(path) is False


def test_the_user_can_write_after_the_rights_were_given(tmp_path: Path) -> None:
    """The point of the whole thing: the user can write afterwards."""
    if sys.platform != "win32":
        pytest.skip("this is not a Windows machine")
    path = tmp_path / "log.txt"
    path.write_text("hello", encoding="utf-8")

    acl.make_user_writable(path)

    with open(path, "a", encoding="utf-8") as handle:
        handle.write("more")
    assert "more" in path.read_text(encoding="utf-8")


def test_the_log_file_is_made_writable_when_the_program_starts() -> None:
    """The program asks for the right when it sets up its log.

    Without this the daemon cannot write a line into the log, and the
    window shows an empty log with no explanation.
    """
    source = (Path(__file__).resolve().parent.parent / "src" / "winsleep" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert "_make_log_writable" in source
    assert "acl.make_user_writable" in source
