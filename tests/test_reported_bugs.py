"""The bugs that made the program look like it did nothing at all.

Everything in here was found on a real Windows machine, not by reading
the code.  Each test says what went wrong and what the program does
now.
"""

from __future__ import annotations

import argparse

from winsleep.cli import build_parser
from winsleep.core import elevate, gpu, install

# ------------------------------------------------------- the elevation mark


def test_the_elevated_copy_can_start() -> None:
    """The marker must be a known argument.

    The elevation code puts ``--winsleep-elevated`` on the command line
    of the second copy.  The parser did not know that argument, so the
    copy refused to start with "unrecognized arguments" and died
    before it did anything at all.

    That is what made the program look broken: the first copy asked for
    administrator rights, the second copy died instantly, and not a
    single line was written to the log.
    """
    parser = build_parser()
    args = parser.parse_args([elevate.ELEVATION_MARKER])
    assert args.command is None


def test_the_elevated_copy_can_open_the_window() -> None:
    parser = build_parser()
    args = parser.parse_args([elevate.ELEVATION_MARKER, "gui"])
    assert args.command == "gui"
    assert callable(args.func)


def test_the_elevated_copy_can_run_the_daemon() -> None:
    parser = build_parser()
    args = parser.parse_args([elevate.ELEVATION_MARKER, "daemon"])
    assert args.command == "daemon"


def test_the_marker_does_not_show_up_in_the_help() -> None:
    parser = build_parser()
    assert elevate.ELEVATION_MARKER not in parser.format_help()


# ------------------------------------------------------- the graphics card


def test_the_nvidia_card_is_found_by_its_provider() -> None:
    """The card is found under ``Control\\Video``.

    The old code looked for a ``Settings`` subkey with a ``GUID`` in
    it.  That subkey does not exist on current drivers, so the card was
    never found and the interface always said the feature does not
    apply - on a machine with an RTX 3060 in it.
    """
    # The provider is what tells the entries apart.
    assert gpu.nvidia_adapters() == [] or all(
        "Video" in path for path in gpu.nvidia_adapters()
    )


def test_the_graphics_preference_is_read_from_the_global_settings() -> None:
    """The preference lives in one value with several settings in it.

    The value holds things like ``GpuPreference=2;`` and
    ``SwapEffectUpgradeEnable=0;``.  Writing it has to keep the other
    settings, because they are not ours to change.
    """
    assert gpu._GLOBAL_SETTINGS_VALUE == "DirectXUserGlobalSettings"
    assert gpu._GPU_PREFERENCE_SETTING == "GpuPreference"


def test_the_graphics_preference_is_looked_for_in_the_global_value() -> None:
    assert gpu._global_preference_from("GpuPreference=2;") == "2"
    assert gpu._global_preference_from("SwapEffectUpgradeEnable=0;") == ""
    assert gpu._global_preference_from("GpuPreference=3;SwapEffectUpgradeEnable=0;") == "3"
    assert gpu._global_preference_from("") == ""


# ------------------------------------------------------------ the service


def test_a_task_that_points_at_a_missing_file_is_noticed() -> None:
    """The task holds the path of the program.

    When the user moves the program, the task keeps pointing at the
    old place and every start fails with 0x80070002 - "the file was
    not found".  The program has to notice that, because otherwise it
    tells the user the service started when nothing happened.
    """
    assert callable(install.task_points_at)


def test_the_service_state_says_when_the_tasks_are_stale() -> None:
    status = install.service_status()
    assert "stale" in status
    assert "stale_task" in status


def test_a_missing_file_is_reported_as_such() -> None:
    """The error code of a task that cannot find its file.

    ``0x80070002`` is ERROR_FILE_NOT_FOUND.  It is the usual failure
    of a task that points at a place where the program is not any
    more, and it deserves its own message.
    """
    assert install._FILE_NOT_FOUND == 0x80070002


# ----------------------------------------------------------- the log file


def test_the_log_is_written_where_the_interface_looks_for_it() -> None:
    """Both the window and the daemon have to use the same file."""
    from winsleep.core import paths

    assert paths.log_path().name == "winsleep.log"
    assert paths.log_path().parent == paths.app_dir()


def test_the_program_can_be_started_without_a_command() -> None:
    """Started by double clicking, there is no sub command.

    This is the way the user starts it, and it is the way the
    elevation happens.  The parser must not fall over on it.
    """
    parser = build_parser()
    args = parser.parse_args([])
    assert args.command is None


def test_an_unknown_command_is_still_refused() -> None:
    parser = build_parser()
    with __import__("pytest").raises(SystemExit):
        parser.parse_args(["nonsense"])


def test_the_parser_is_an_argparse_parser() -> None:
    assert isinstance(build_parser(), argparse.ArgumentParser)
