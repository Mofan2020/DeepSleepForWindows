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
