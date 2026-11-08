"""Tests for the units, the process picker and the plan editor.

Three things the interface relies on are tested here:

* the durations the user writes are understood, in seconds and in
  minutes, in English and in Chinese;
* the process list the picker shows comes from the machine and can be
  filtered;
* the plan editor reads the settings of a plan and writes them back
  through the daemon, which is the half that has the rights to write.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest

from winsleep.core import daemon as daemon_mod
from winsleep.core import powersettings
from winsleep.core.rules import (
    Condition,
    Rule,
    Snapshot,
    evaluate,
    parse_duration,
    parse_number,
)

# ------------------------------------------------------------------ units


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("600", 600.0),
        ("600s", 600.0),
        ("10m", 600.0),
        ("10min", 600.0),
        ("10 minutes", 600.0),
        ("2h", 7200.0),
        ("2 hours", 7200.0),
        ("1d", 86400.0),
        ("10分钟", 600.0),
        ("2小时", 7200.0),
        ("30秒", 30.0),
        ("  10 m  ", 600.0),
    ],
)
def test_parse_duration_understands_the_units(text, expected):
    assert parse_duration(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "abc", "m", "10x", "分钟"])
def test_parse_duration_rejects_what_is_not_a_duration(text):
    assert parse_duration(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("15", 15.0), ("15%", 15.0), (" 20 ", 20.0), ("0", 0.0)],
)
def test_parse_number_reads_a_plain_number(text, expected):
    assert parse_number(text) == pytest.approx(expected)


def test_parse_number_rejects_what_is_not_a_number():
    assert parse_number("abc") is None
    assert parse_number("15m") is None


def test_a_rule_with_minutes_holds_the_same_as_one_with_seconds():
    """The interface writes minutes, the rule keeps seconds."""
    in_seconds = Rule(
        name="a",
        conditions=(Condition(kind="idle_at_least", value="600"),),
    )
    in_minutes = Rule(
        name="b",
        conditions=(Condition(kind="idle_at_least", value="10m"),),
    )
    snap = Snapshot(now=dt.datetime(2026, 1, 1, 12, 0), idle_seconds=600.0)

    assert evaluate([in_seconds], snap)
    assert evaluate([in_minutes], snap)


def test_a_cpu_threshold_does_not_take_a_time_unit():
    """A percentage is not a duration: ``15m`` must not become 900."""
    rule = Rule(
        name="cpu",
        conditions=(Condition(kind="cpu_below", value="15m"),),
    )
    snap = Snapshot(now=dt.datetime(2026, 1, 1, 12, 0), idle_seconds=0.0, cpu_percent=50.0)
    assert not evaluate([rule], snap)


# ------------------------------------------------------- the plan editor


class FakePowersettings:
    """A stand-in for the power settings module.

    It remembers what was written so the test can check that the
    daemon passed the right values on.
    """

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str, int]] = []
        self.result = (True, "ok")

    def apply_settings(self, scheme, changes):
        for change in changes:
            self.writes.append(
                (scheme, change["group"], change["guid"], int(change["value"]))
            )
        return self.result

    def duplicate_plan(self, scheme, name):
        return self.result

    def delete_plan(self, scheme):
        return self.result


def test_the_daemon_writes_the_plan_settings(monkeypatch):
    fake = FakePowersettings()
    monkeypatch.setattr(powersettings, "apply_settings", fake.apply_settings)

    server = daemon_mod.StateServer()
    reply = server._dispatch(
        json.dumps(
            {
                "kind": "set_plan_settings",
                "guid": "plan-guid",
                "changes": [
                    {"group": "group-a", "guid": "setting-a", "value": 600},
                    {"group": "group-b", "guid": "setting-b", "value": 1800},
                ],
            }
        ).encode("utf-8")
    )

    answer = json.loads(reply.decode("utf-8"))
    assert answer["ok"] is True
    assert fake.writes == [
        ("plan-guid", "group-a", "setting-a", 600),
        ("plan-guid", "group-b", "setting-b", 1800),
    ]


def test_the_daemon_refuses_to_write_without_a_plan(monkeypatch):
    fake = FakePowersettings()
    monkeypatch.setattr(powersettings, "apply_settings", fake.apply_settings)

    server = daemon_mod.StateServer()
    reply = server._dispatch(
        json.dumps({"kind": "set_plan_settings", "changes": []}).encode("utf-8")
    )

    answer = json.loads(reply.decode("utf-8"))
    assert answer["ok"] is False
    assert fake.writes == []


def test_the_daemon_reports_a_failed_write(monkeypatch):
    fake = FakePowersettings()
    fake.result = (False, "the system refused the change")
    monkeypatch.setattr(powersettings, "apply_settings", fake.apply_settings)

    server = daemon_mod.StateServer()
    reply = server._dispatch(
        json.dumps(
            {
                "kind": "set_plan_settings",
                "guid": "plan-guid",
                "changes": [{"group": "g", "guid": "s", "value": 1}],
            }
        ).encode("utf-8")
    )

    answer = json.loads(reply.decode("utf-8"))
    assert answer["ok"] is False
    assert "refused" in answer["message"]


def test_the_daemon_copies_a_plan(monkeypatch):
    fake = FakePowersettings()
    fake.result = (True, "new-plan-guid")
    monkeypatch.setattr(powersettings, "duplicate_plan", fake.duplicate_plan)

    server = daemon_mod.StateServer()
    reply = server._dispatch(
        json.dumps(
            {"kind": "duplicate_plan", "guid": "plan-guid", "name": "我的计划"}
        ).encode("utf-8")
    )

    answer = json.loads(reply.decode("utf-8"))
    assert answer["ok"] is True
    assert answer["guid"] == "new-plan-guid"


def test_the_daemon_refuses_to_copy_a_plan_without_a_name(monkeypatch):
    fake = FakePowersettings()
    monkeypatch.setattr(powersettings, "duplicate_plan", fake.duplicate_plan)

    server = daemon_mod.StateServer()
    reply = server._dispatch(
        json.dumps({"kind": "duplicate_plan", "guid": "plan-guid", "name": "  "}).encode(
            "utf-8"
        )
    )

    answer = json.loads(reply.decode("utf-8"))
    assert answer["ok"] is False
    assert fake.writes == []


def test_the_daemon_deletes_a_plan(monkeypatch):
    fake = FakePowersettings()
    fake.result = (True, "the plan was deleted")
    monkeypatch.setattr(powersettings, "delete_plan", fake.delete_plan)

    server = daemon_mod.StateServer()
    reply = server._dispatch(
        json.dumps({"kind": "delete_plan", "guid": "plan-guid"}).encode("utf-8")
    )

    answer = json.loads(reply.decode("utf-8"))
    assert answer["ok"] is True


# ------------------------------------------------- the background service


def test_the_install_module_does_not_depend_on_winpower():
    """The install code used a helper that no longer exists.

    It raised an ImportError the moment the background service was
    installed, so the task was never registered and the interface
    showed nothing at all.
    """
    import importlib

    module = importlib.import_module("winsleep.core.install")
    assert hasattr(module, "_decode")


def test_the_command_line_of_a_task_is_right():
    """The task has to run the program with the right sub command.

    A frozen build is one executable, so the command is the executable
    followed by the sub command - not ``--module``, which is not an
    option of the program at all.
    """
    from winsleep.core import install

    command = install._module_args("daemon")
    assert "--module" not in command
    assert command.endswith("daemon")


def test_the_task_commands_use_the_sub_commands():
    from winsleep.core import install

    for name in ("daemon", "agent", "sleep"):
        command = install._module_args(name)
        assert command.rstrip('"').endswith(name)


def test_the_decode_reads_what_windows_wrote():
    from winsleep.core.install import _decode

    assert _decode(b"") == ""
    assert _decode(b"hello") == "hello"
    # Bytes that are not valid UTF-8 must not raise.
    assert _decode(b"\xff\xfe\x00") is not None


def test_installing_reports_the_reason_when_it_fails(monkeypatch):
    """A failed install has to say why, not just fail quietly."""
    from winsleep.core import install
    from winsleep.gui import bridge as bridge_mod

    def broken():
        raise RuntimeError("the task could not be created")

    monkeypatch.setattr(install, "install_all", broken)

    bridge = bridge_mod.Bridge()
    answer = bridge.install_service()
    assert answer["ok"] is False
    assert "the task could not be created" in answer["message"]


def test_installing_reports_the_reason_when_the_command_fails(monkeypatch):
    from winsleep.core import install
    from winsleep.gui import bridge as bridge_mod

    monkeypatch.setattr(install, "install_all", lambda: (False, "schtasks failed"))

    bridge = bridge_mod.Bridge()
    answer = bridge.install_service()
    assert answer["ok"] is False
    assert "schtasks failed" in answer["message"]


def test_installing_starts_the_service_when_the_tasks_are_registered(monkeypatch):
    from winsleep.core import install
    from winsleep.gui import bridge as bridge_mod

    monkeypatch.setattr(install, "install_all", lambda: (True, "the tasks were registered"))
    monkeypatch.setattr(install, "start_daemon", lambda: (True, "the background service was started"))

    bridge = bridge_mod.Bridge()
    answer = bridge.install_service()
    assert answer["ok"] is True


# ------------------------------------------------------------ the bridge


def test_the_bridge_lists_the_processes(monkeypatch):
    from winsleep.gui import bridge as bridge_mod

    class FakeProcess:
        def __init__(self, name, exe, pid):
            self.info = {"name": name, "exe": exe, "pid": pid}

    class FakePsutil:
        @staticmethod
        def process_iter(_attrs):
            return [
                FakeProcess("steam.exe", r"C:\steam\steam.exe", 10),
                FakeProcess("notepad.exe", r"C:\notepad.exe", 20),
            ]

    import sys

    monkeypatch.setitem(sys.modules, "psutil", FakePsutil)

    bridge = bridge_mod.Bridge()
    answer = bridge.list_processes()
    assert answer["ok"] is True
    assert [row["name"] for row in answer["processes"]] == ["notepad.exe", "steam.exe"]


def test_the_bridge_filters_the_processes(monkeypatch):
    from winsleep.gui import bridge as bridge_mod

    class FakeProcess:
        def __init__(self, name):
            self.info = {"name": name, "exe": "", "pid": 1}

    class FakePsutil:
        @staticmethod
        def process_iter(_attrs):
            return [FakeProcess("steam.exe"), FakeProcess("notepad.exe")]

    import sys

    monkeypatch.setitem(sys.modules, "psutil", FakePsutil)

    bridge = bridge_mod.Bridge()
    answer = bridge.list_processes("steam")
    assert [row["name"] for row in answer["processes"]] == ["steam.exe"]


def test_the_bridge_says_so_when_there_is_no_window():
    from winsleep.gui import bridge as bridge_mod

    bridge = bridge_mod.Bridge()
    answer = bridge.pick_file()
    assert answer["ok"] is False
    assert answer["path"] == ""


# ------------------------------------------------------- the front end


def test_the_interface_javascript_passes_its_own_checks():
    """The front end has its own test script.

    It runs the real code out of app.js in a node sandbox, so a change
    in the interface that breaks the rules is caught here.
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    script = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "check-gui-js.mjs"
    result = subprocess.run(
        [node, str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ------------------------------------------------------------ the doctor


def test_the_doctor_says_what_is_missing(monkeypatch, capsys):
    """The doctor command tells the user what is not set up.

    It is the first thing to run when something does not work, so it
    has to say which part is missing rather than only that something
    is.
    """
    from winsleep import cli

    monkeypatch.setattr(
        "winsleep.core.install.service_status",
        lambda: {"installed": False, "running": False, "agent_running": False},
    )

    def no_daemon(_self, _message):
        return None

    monkeypatch.setattr("winsleep.gui.bridge.Bridge._daemon_request", no_daemon)

    assert cli._cmd_doctor(None) == 1

    out = capsys.readouterr().out
    assert "installed:" in out
    assert "answering:" in out
    assert "something is missing" in out


def test_the_doctor_is_happy_when_everything_is_in_place(monkeypatch, capsys):
    from winsleep import cli

    monkeypatch.setattr(
        "winsleep.core.install.service_status",
        lambda: {"installed": True, "running": True, "agent_running": True},
    )
    monkeypatch.setattr(
        "winsleep.gui.bridge.Bridge._daemon_request",
        lambda self, message: {"ok": True, "kind": "pong"},
    )

    assert cli._cmd_doctor(None) == 0

    out = capsys.readouterr().out
    assert "everything is in place" in out


# --------------------------------------------------- how the service is seen


def test_the_daemon_is_only_running_when_it_answers(monkeypatch):
    """Looking for the word "daemon" in a command line is not proof.

    It said the daemon was running while nothing was running at all.
    The daemon listens on a port, so it is asked instead.
    """
    from winsleep.core import install

    monkeypatch.setattr(install, "_daemon_answers", lambda: False)
    assert install.daemon_running() is False

    monkeypatch.setattr(install, "_daemon_answers", lambda: True)
    assert install.daemon_running() is True


def test_the_agent_is_only_running_when_its_heartbeat_is_fresh(monkeypatch, tmp_path):
    """The agent writes a heartbeat while it is alive.

    A heartbeat that is too old means the agent stopped, even though
    the file is still there.
    """
    import time

    from winsleep.core import install

    monkeypatch.setattr(install, "heartbeat_path", lambda: tmp_path / "agent.alive")

    assert install.agent_running() is False

    install.write_heartbeat()
    assert install.agent_running() is True

    # An old heartbeat means the agent stopped.
    (tmp_path / "agent.alive").write_text(str(time.time() - 3600), encoding="utf-8")
    assert install.agent_running() is False


def test_the_heartbeat_is_written_where_the_program_looks(monkeypatch, tmp_path):
    from winsleep.core import install

    monkeypatch.setattr(install, "heartbeat_path", lambda: tmp_path / "agent.alive")
    install.write_heartbeat()
    assert (tmp_path / "agent.alive").exists()


def test_a_console_keeps_its_own_code_page(monkeypatch):
    """The console knows its own code page.

    A Chinese machine writes GBK into a GBK console.  Forcing UTF-8
    there makes the characters unreadable, so the encoding is only
    changed when the console really cannot hold the plan names.
    """
    from winsleep import cli

    class FakeStream:
        """A stream that only says what its encoding is."""

        def __init__(self, encoding):
            self.encoding = encoding
            self.reconfigured = None

        def reconfigure(self, **kwargs):
            self.reconfigured = kwargs

    gbk = FakeStream("gbk")
    monkeypatch.setattr(cli.sys, "stdout", gbk)
    cli._use_utf8_output()
    assert gbk.reconfigured["encoding"] == "gbk"

    # A console that cannot hold the names gets UTF-8 instead.
    ascii_stream = FakeStream("ascii")
    monkeypatch.setattr(cli.sys, "stdout", ascii_stream)
    cli._use_utf8_output()
    assert ascii_stream.reconfigured["encoding"] == "utf-8"


def test_stopping_the_service_takes_the_heartbeat_away(monkeypatch, tmp_path):
    """The window says the agent stopped right away.

    A heartbeat that is still there would keep saying the agent is
    running for a while after it was stopped.
    """
    from winsleep.core import install

    monkeypatch.setattr(install, "heartbeat_path", lambda: tmp_path / "agent.alive")
    monkeypatch.setattr(install, "task_exists", lambda _name: False)

    install.write_heartbeat()
    assert (tmp_path / "agent.alive").exists()

    install.stop_daemon()
    assert not (tmp_path / "agent.alive").exists()


def test_the_heartbeat_stays_good_only_for_a_short_while(monkeypatch, tmp_path):
    """A heartbeat that is too old means the agent stopped.

    The agent writes it every five seconds, so twenty seconds is
    enough to survive a busy moment and short enough that a stopped
    agent is noticed right away.
    """
    import time

    from winsleep.core import install

    monkeypatch.setattr(install, "heartbeat_path", lambda: tmp_path / "agent.alive")
    assert install.HEARTBEAT_SECONDS <= 30

    install.write_heartbeat()
    assert install.agent_running() is True

    (tmp_path / "agent.alive").write_text(
        str(time.time() - install.HEARTBEAT_SECONDS - 1), encoding="utf-8"
    )
    assert install.agent_running() is False
