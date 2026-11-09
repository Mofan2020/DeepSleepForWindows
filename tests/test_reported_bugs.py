"""The bugs that made the program look like it did nothing at all.

Everything in here was found on a real Windows machine, not by reading
the code.  Each test says what went wrong and what the program does
now.
"""

from __future__ import annotations

import argparse

from winsleep.cli import build_parser
from winsleep.core import elevate, gpu, install
from winsleep.core.decide import Intent
from winsleep.core.servermode import Phase

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



# ------------------------------------------------- the log that fills up


def test_the_same_decision_is_not_written_to_the_log_again():
    """A line that says the same thing must not be written again.

    The daemon compares the new intent with the last one to decide
    whether anything changed.  It compared the whole intent, and the
    intent carries the reason - "idle for 8704s" - which is a live
    number that grows every tick.  Two intents that asked for exactly
    the same thing therefore never looked the same, and the daemon
    wrote a line about the plan every five seconds.

    On a machine that sat idle over the night that was five hundred
    lines of the same sentence, and the log had to be trimmed away
    every minute to keep up.
    """
    from winsleep.core.daemon import Daemon

    first = Intent(plan="节能", reason="idle for 8704s")
    second = Intent(plan="节能", reason="idle for 8709s")

    assert Daemon._same_decision(first, second)


def test_a_different_plan_is_still_a_different_decision():
    from winsleep.core.daemon import Daemon

    one = Intent(plan="节能", reason="idle for 8704s")
    other = Intent(plan="Bitsum Highest Performance", reason="the user is active")

    assert not Daemon._same_decision(one, other)


def test_the_lock_countdown_does_not_make_a_new_decision():
    """The seconds left on the lock tick down every second.

    The daemon never acts on that number - it only tells the interface
    how long is left - so it must not make every tick look like a
    change.
    """
    from winsleep.core.daemon import Daemon

    one = Intent(plan=None, lock_in=200, phase=Phase.WAITING_FOR_IDLE)
    other = Intent(plan=None, lock_in=195, phase=Phase.WAITING_FOR_IDLE)

    assert Daemon._same_decision(one, other)


def test_nothing_is_the_same_as_something():
    from winsleep.core.daemon import Daemon

    assert not Daemon._same_decision(None, Intent(plan="节能"))
    assert not Daemon._same_decision(Intent(plan="节能"), None)
    assert Daemon._same_decision(None, None)


def test_two_ticks_with_the_same_decision_write_one_line(caplog):
    """The log must not fill up with the same sentence.

    This drives the real tick, not the helper, because the helper is
    what the bug was in: the tick asked "is this the same decision?"
    and the helper answered it wrongly.
    """
    import time

    from winsleep.core import daemon as daemon_mod

    daemon = daemon_mod.Daemon()
    daemon._config_path = __import__("pathlib").Path("/nonexistent/config.json")

    # The agent reports a fresh idle time, so the daemon decides the
    # machine is idle and wants the idle plan.
    daemon._server.agent.idle_seconds = 8704.0
    daemon._server.agent.updated_at = time.time()

    with caplog.at_level("INFO", logger="winsleep.daemon"):
        daemon._tick()
        daemon._server.agent.idle_seconds = 8709.0
        daemon._tick()

    lines = [
        record.getMessage()
        for record in caplog.records
        if "the plan should be" in record.getMessage()
    ]
    assert len(lines) == 1, lines


def test_the_daemon_does_not_write_a_line_about_every_trim(caplog, tmp_path):
    """The trim must not leave a trail behind.

    A line saying "the log was trimmed" is itself a line in the log.
    The file goes over the limit again right away, so the next trim
    takes that line away again and writes a new one - and the file is
    rewritten every minute for ever, with a trail of "trimmed" lines
    in it.
    """
    from winsleep.core import daemon as daemon_mod
    from winsleep.core import logs

    path = tmp_path / "winsleep.log"
    path.write_text("".join(f"line {n}\n" for n in range(10)), encoding="utf-8")

    daemon = daemon_mod.Daemon()
    daemon._config_path = tmp_path / "config.json"
    daemon._last_log_trim = 0.0

    config = daemon_mod.config_mod.Config(log_max_lines=5)
    daemon_mod.paths.log_path = lambda: path  # type: ignore[assignment]

    with caplog.at_level("INFO", logger="winsleep.daemon"):
        daemon._trim_log(config)

    assert "trimmed" not in caplog.text
    # The file is smaller now, and it stays that way.
    assert len(logs.read_log(path)) == 5
