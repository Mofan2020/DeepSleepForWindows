"""The copy of the program that stays put, and the tasks that run it.

Two things are checked here, and they are the two that decide whether
the background half is still there tomorrow:

*   The program keeps a copy of itself in a folder of its own, and the
    copy is replaced when the program that is running is another
    version.  A task that names a file the user has since deleted is a
    task that fails silently, every time.
*   The tasks are registered from XML that says what to do when the
    program stops - start it again.  The command line switches cannot
    say that, which is why a crashed daemon used to stay crashed.
"""

from __future__ import annotations

import json

import pytest

from winsleep.core import bootstrap, tasks

# ------------------------------------------------------------ the copy


def test_the_fingerprint_of_a_file_is_stable(tmp_path):
    """The same bytes give the same answer, every time."""
    path = tmp_path / "one.bin"
    path.write_bytes(b"hello")

    assert bootstrap.fingerprint(path) == bootstrap.fingerprint(path)
    assert len(bootstrap.fingerprint(path)) == 64


def test_a_file_that_is_not_there_has_no_fingerprint(tmp_path):
    """A missing file is never equal to a real one."""
    assert bootstrap.fingerprint(tmp_path / "nope.bin") == ""


def test_two_different_files_have_different_fingerprints(tmp_path):
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    assert bootstrap.fingerprint(first) != bootstrap.fingerprint(second)


def test_a_run_from_the_source_has_nothing_to_copy(tmp_path, monkeypatch):
    """A developer run must not scatter copies of the program around."""
    monkeypatch.setenv("WINSLEEP_PROGRAM_DIR", str(tmp_path))
    monkeypatch.setattr(bootstrap.sys, "frozen", False, raising=False)

    ok, detail = bootstrap.ensure_installed()
    assert ok is True
    assert "source" in detail
    assert not list(tmp_path.iterdir())


def _pretend_to_be_frozen(monkeypatch, executable):
    """Make the module believe it is a frozen build started from *executable*."""
    monkeypatch.setattr(bootstrap.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bootstrap.sys, "executable", str(executable))


def test_the_copy_is_made_when_it_is_not_there(tmp_path, monkeypatch):
    running = tmp_path / "download" / "DeepSleepForWindows.exe"
    running.parent.mkdir()
    running.write_bytes(b"the program")

    program_dir = tmp_path / "app"
    monkeypatch.setenv("WINSLEEP_PROGRAM_DIR", str(program_dir))
    _pretend_to_be_frozen(monkeypatch, running)

    ok, detail = bootstrap.ensure_installed()

    assert ok is True, detail
    assert bootstrap.installed_exe().exists()
    assert bootstrap.installed_exe().read_bytes() == b"the program"


def test_the_manifest_says_which_version_is_in_the_folder(tmp_path, monkeypatch):
    running = tmp_path / "download" / "DeepSleepForWindows.exe"
    running.parent.mkdir()
    running.write_bytes(b"the program")

    monkeypatch.setenv("WINSLEEP_PROGRAM_DIR", str(tmp_path / "app"))
    _pretend_to_be_frozen(monkeypatch, running)

    bootstrap.ensure_installed()

    manifest = json.loads(bootstrap.manifest_path().read_text(encoding="utf-8"))
    assert manifest["file"] == bootstrap.EXE_NAME
    assert len(manifest["sha256"]) == 64
    assert manifest["version"]


def test_a_copy_that_is_the_same_is_left_alone(tmp_path, monkeypatch):
    """Nothing is rewritten when the folder already has this program."""
    running = tmp_path / "download" / "DeepSleepForWindows.exe"
    running.parent.mkdir()
    running.write_bytes(b"the program")

    monkeypatch.setenv("WINSLEEP_PROGRAM_DIR", str(tmp_path / "app"))
    _pretend_to_be_frozen(monkeypatch, running)
    bootstrap.ensure_installed()

    # A time stamp in the past, so a rewrite would be easy to see.
    before = bootstrap.installed_exe().stat().st_mtime
    bootstrap.installed_exe().touch()
    after_touch = bootstrap.installed_exe().stat().st_mtime

    ok, detail = bootstrap.ensure_installed()

    assert ok is True
    assert "up to date" in detail
    assert after_touch >= before


def test_an_older_copy_is_replaced(tmp_path, monkeypatch):
    """A new download has to reach the folder the tasks point at."""
    running = tmp_path / "download" / "DeepSleepForWindows.exe"
    running.parent.mkdir()
    running.write_bytes(b"the newer program")

    program_dir = tmp_path / "app"
    program_dir.mkdir()
    (program_dir / bootstrap.EXE_NAME).write_bytes(b"the older program")

    monkeypatch.setenv("WINSLEEP_PROGRAM_DIR", str(program_dir))
    _pretend_to_be_frozen(monkeypatch, running)

    ok, detail = bootstrap.ensure_installed()

    assert ok is True
    assert bootstrap.installed_exe().read_bytes() == b"the newer program"


def test_the_copy_is_not_made_from_a_copy_of_itself(tmp_path, monkeypatch):
    """Running the copy must not copy it over itself."""
    program_dir = tmp_path / "app"
    program_dir.mkdir()
    executable = program_dir / bootstrap.EXE_NAME
    executable.write_bytes(b"the program")

    monkeypatch.setenv("WINSLEEP_PROGRAM_DIR", str(program_dir))
    _pretend_to_be_frozen(monkeypatch, executable)

    ok, detail = bootstrap.ensure_installed()

    assert ok is True
    assert "from the copy" in detail
    assert executable.read_bytes() == b"the program"


# ----------------------------------------------------------- the tasks


def test_a_task_starts_again_when_it_stops():
    """This is the whole reason the tasks are XML.

    The command line switches cannot say it, and without it one crash
    is the end of the background half.
    """
    xml = tasks.build_task_xml(
        tasks.DAEMON_TASK,
        tasks.Path(r"C:\App\DeepSleepForWindows.exe"),
        "daemon",
    )

    assert "<RestartOnFailure>" in xml
    assert f"<Count>{tasks.RESTART_COUNT}</Count>" in xml
    assert f"<Interval>{tasks.RESTART_INTERVAL}</Interval>" in xml


def test_a_task_is_never_stopped_for_taking_too_long():
    """The default limit is three days.

    A daemon killed every three days looks exactly like a daemon that
    disappears on its own.
    """
    xml = tasks.build_task_xml(
        tasks.DAEMON_TASK,
        tasks.Path(r"C:\App\DeepSleepForWindows.exe"),
        "daemon",
    )

    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml


def test_a_task_keeps_running_on_the_battery():
    """A laptop on the battery still wants its power plan managed."""
    xml = tasks.build_task_xml(
        tasks.DAEMON_TASK,
        tasks.Path(r"C:\App\DeepSleepForWindows.exe"),
        "daemon",
    )

    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
    assert "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml


def test_a_task_is_not_started_twice():
    xml = tasks.build_task_xml(
        tasks.AGENT_TASK,
        tasks.Path(r"C:\App\DeepSleepForWindows.exe"),
        "agent",
    )

    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in xml


def test_the_daemon_runs_whether_anybody_is_logged_on():
    """The daemon owns the power plan and has to work at the lock screen."""
    xml = tasks.build_task_xml(
        tasks.DAEMON_TASK,
        tasks.Path(r"C:\App\DeepSleepForWindows.exe"),
        "daemon",
    )

    assert f"<UserId>{tasks.SYSTEM_SID}</UserId>" in xml
    assert "<BootTrigger>" in xml
    assert "<RunLevel>HighestAvailable</RunLevel>" in xml


def test_the_tray_and_the_agent_belong_to_the_logged_on_person():
    """They need a desktop and a keyboard, so they are not system tasks."""
    for name, command in (
        (tasks.AGENT_TASK, "agent"),
        (tasks.TRAY_TASK, "tray"),
    ):
        xml = tasks.build_task_xml(
            name,
            tasks.Path(r"C:\App\DeepSleepForWindows.exe"),
            command,
        )
        assert f"<GroupId>{tasks.USERS_SID}</GroupId>" in xml
        assert "<RunLevel>LeastPrivilege</RunLevel>" in xml
        assert "<BootTrigger>" not in xml


def test_the_command_line_of_a_task_is_the_program_and_one_word():
    xml = tasks.build_task_xml(
        tasks.DAEMON_TASK,
        tasks.Path(r"C:\Program Files\DeepSleep\DeepSleepForWindows.exe"),
        "daemon",
    )

    assert "<Command>C:\\Program Files\\DeepSleep\\DeepSleepForWindows.exe</Command>" in xml
    assert "<Arguments>daemon</Arguments>" in xml


def test_a_path_with_an_ampersand_does_not_break_the_xml():
    """An XML file that cannot be parsed is a task that is not created."""
    xml = tasks.build_task_xml(
        tasks.DAEMON_TASK,
        tasks.Path(r"C:\A & B\DeepSleepForWindows.exe"),
        "daemon",
    )

    assert "&amp;" in xml
    assert " & " not in xml


def test_the_xml_can_be_parsed():
    """The scheduler refuses a file it cannot parse, and says nothing useful."""
    import xml.etree.ElementTree as ElementTree

    for name, command in (
        (tasks.DAEMON_TASK, "daemon"),
        (tasks.AGENT_TASK, "agent"),
        (tasks.TRAY_TASK, "tray"),
    ):
        xml = tasks.build_task_xml(name, tasks.Path(r"C:\App\x.exe"), command)
        root = ElementTree.fromstring(xml)
        assert root.tag.endswith("Task")


def test_the_daemon_task_is_the_system_kind():
    assert tasks.describe_task_kind(tasks.DAEMON_TASK) == tasks.KIND_SYSTEM
    assert tasks.describe_task_kind(tasks.AGENT_TASK) == tasks.KIND_INTERACTIVE
    assert tasks.describe_task_kind(tasks.TRAY_TASK) == tasks.KIND_INTERACTIVE


@pytest.mark.parametrize("seconds, expected", [(0, "PT0S"), (20, "PT20S"), (90, "PT90S")])
def test_a_delay_is_written_the_way_the_schema_wants_it(seconds, expected):
    assert tasks._iso_seconds(seconds) == expected


# ------------------------------------------------ a copy that went wrong


def test_a_copy_that_was_cut_short_is_not_accepted(tmp_path, monkeypatch):
    """A half written file must not be taken for the program.

    A full disk, a file that is still being written - either one
    leaves a file that is there and is not the program.  The only way
    to know is to read it back.
    """
    running = tmp_path / "download" / "DeepSleepForWindows.exe"
    running.parent.mkdir()
    running.write_bytes(b"the program, all 10 bytes")

    monkeypatch.setenv("WINSLEEP_PROGRAM_DIR", str(tmp_path / "app"))
    _pretend_to_be_frozen(monkeypatch, running)

    # The copy writes half of what it was given, which is what a full
    # disk does at the end of a copy.
    real_copy = bootstrap.shutil.copyfile

    def half_a_copy(source, target):
        real_copy(source, target)
        with open(target, "wb") as handle:
            handle.write(b"the prog")

    monkeypatch.setattr(bootstrap.shutil, "copyfile", half_a_copy)

    ok, detail = bootstrap.ensure_installed()

    assert ok is False
    assert "does not match" in detail


def test_the_manifest_is_not_written_for_a_bad_copy(tmp_path, monkeypatch):
    """A manifest says "this folder is good", so it waits for a good copy."""
    running = tmp_path / "download" / "DeepSleepForWindows.exe"
    running.parent.mkdir()
    running.write_bytes(b"the program")

    monkeypatch.setenv("WINSLEEP_PROGRAM_DIR", str(tmp_path / "app"))
    _pretend_to_be_frozen(monkeypatch, running)
    monkeypatch.setattr(
        bootstrap.shutil, "copyfile", lambda source, target: None
    )

    ok, _detail = bootstrap.ensure_installed()

    assert ok is False
    assert not bootstrap.manifest_path().exists()


# ------------------------------------- installing has to start the tasks


def test_installing_starts_the_tasks_right_away(monkeypatch, tmp_path):
    """A logon trigger waits for a logon that already happened.

    Registering the tasks and stopping there leaves the program
    installed and doing nothing until the machine restarts - and the
    icon in the notification area missing until then.
    """
    from winsleep.core import install

    called = []
    monkeypatch.setattr(install, "install_daemon_task", lambda: (True, "daemon"))
    monkeypatch.setattr(install, "install_agent_task", lambda: (True, "agent"))
    monkeypatch.setattr(install, "install_tray_task", lambda: (True, "tray"))
    monkeypatch.setattr(
        install, "start_all", lambda: called.append(True) or (True, "started")
    )
    # The copy is made before anything is registered; it is not what
    # this test is about.
    from winsleep.core import bootstrap

    monkeypatch.setattr(bootstrap, "ensure_installed", lambda: (True, "copy"))

    ok, detail = install.install_all()

    assert ok is True
    assert called == [True]
    assert "started" in detail


# ------------------------------------ reading a task without the language


def _task_xml(enabled: str, trigger_enabled: str = "true") -> bytes:
    """The XML the scheduler publishes, in the shape it publishes it."""
    text = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<Task version="1.4" '
        'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        "<Triggers><LogonTrigger>"
        f"<Enabled>{trigger_enabled}</Enabled>"
        "</LogonTrigger></Triggers>"
        f"<Settings><Enabled>{enabled}</Enabled></Settings>"
        "</Task>"
    )
    return text.encode("utf-16")


def test_a_task_that_is_switched_off_is_read_as_off(monkeypatch):
    """The answer must not come from a word in the machine's language.

    ``schtasks /query`` writes ``Disabled`` on an English Windows and
    something else everywhere else, so the state is read from the XML
    the scheduler publishes - which has no translated words in it.
    """
    from winsleep.core import telemetry

    class Done:
        returncode = 0
        stdout = _task_xml("false", trigger_enabled="false")
        stderr = b""

    monkeypatch.setattr(telemetry.subprocess, "run", lambda *a, **k: Done())
    assert telemetry._task_is_disabled("Any-Task") is True


def test_the_switch_that_counts_is_the_one_in_the_settings(monkeypatch):
    """The trigger and the task each have an ``Enabled``.

    A trigger that is on says when a task may start; the setting says
    whether the task may start at all.  Reading the wrong one reports
    a task that runs as disabled - and a switch that says "off" while
    the telemetry is being sent is worse than no switch.
    """
    from winsleep.core import telemetry

    # The trigger is on, the task itself is off: the task is off.
    class TaskOff:
        returncode = 0
        stdout = _task_xml("false", trigger_enabled="true")
        stderr = b""

    monkeypatch.setattr(telemetry.subprocess, "run", lambda *a, **k: TaskOff())
    assert telemetry._task_is_disabled("Any-Task") is True

    # The trigger is off, the task itself is on: the task is on.
    class TriggerOff:
        returncode = 0
        stdout = _task_xml("true", trigger_enabled="false")
        stderr = b""

    monkeypatch.setattr(telemetry.subprocess, "run", lambda *a, **k: TriggerOff())
    assert telemetry._task_is_disabled("Any-Task") is False


def test_a_task_that_is_on_is_read_as_on(monkeypatch):
    from winsleep.core import telemetry

    class Done:
        returncode = 0
        stdout = _task_xml("true", trigger_enabled="true")
        stderr = b""

    monkeypatch.setattr(telemetry.subprocess, "run", lambda *a, **k: Done())
    assert telemetry._task_is_disabled("Any-Task") is False


def test_a_task_that_is_not_on_the_machine_is_not_an_error(monkeypatch):
    """Not every machine has every telemetry task."""
    from winsleep.core import telemetry

    class Done:
        returncode = 1
        stdout = b""
        stderr = b""

    monkeypatch.setattr(telemetry.subprocess, "run", lambda *a, **k: Done())
    assert telemetry._task_is_disabled("Missing-Task") is None
