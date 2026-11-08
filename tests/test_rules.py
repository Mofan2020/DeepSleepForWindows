"""Tests for the rule engine."""

from __future__ import annotations

import datetime as dt

import pytest

from winsleep.core.rules import (
    Action,
    Condition,
    Rule,
    Snapshot,
    evaluate,
)


@pytest.fixture
def monday_noon() -> dt.datetime:
    return dt.datetime(2026, 11, 9, 12, 0, 0)  # a Monday


def snap(**kwargs) -> Snapshot:
    base = {
        "now": dt.datetime(2026, 11, 9, 12, 0, 0),
        "idle_seconds": 0.0,
        "cpu_percent": 0.0,
        "running_processes": frozenset(),
    }
    base.update(kwargs)
    return Snapshot(**base)


class TestSnapshot:
    def test_process_match_ignores_case_and_exe_suffix(self):
        s = snap(running_processes=frozenset({"Steam.exe"}))
        assert s.has_process("steam")
        assert s.has_process("STEAM")
        assert s.has_process("steam.exe")
        assert not s.has_process("steam2")

    def test_empty_name_never_matches(self):
        s = snap(running_processes=frozenset({"steam.exe"}))
        assert not s.has_process("")
        assert not s.has_process("   ")


class TestConditions:
    def test_idle_at_least(self):
        rule = Rule("r", (Condition("idle_at_least", "600"),), Action("lock_screen"))
        assert evaluate([rule], snap(idle_seconds=599.0)) == []
        assert evaluate([rule], snap(idle_seconds=600.0)) == [rule]

    def test_idle_below(self):
        rule = Rule("r", (Condition("idle_below", "60"),), Action("lock_screen"))
        assert evaluate([rule], snap(idle_seconds=59.0)) == [rule]
        assert evaluate([rule], snap(idle_seconds=60.0)) == []

    def test_cpu_below_is_strict(self):
        rule = Rule("r", (Condition("cpu_below", "15"),), Action("lock_screen"))
        assert evaluate([rule], snap(cpu_percent=14.9)) == [rule]
        assert evaluate([rule], snap(cpu_percent=15.0)) == []

    def test_cpu_above_is_inclusive(self):
        rule = Rule("r", (Condition("cpu_above", "15"),), Action("lock_screen"))
        assert evaluate([rule], snap(cpu_percent=15.0)) == [rule]
        assert evaluate([rule], snap(cpu_percent=14.9)) == []

    def test_process_running(self):
        rule = Rule("r", (Condition("process_running", "steam"),), Action("lock_screen"))
        assert evaluate([rule], snap(running_processes=frozenset({"steam.exe"}))) == [rule]
        assert evaluate([rule], snap(running_processes=frozenset())) == []

    def test_time_window_inside(self):
        rule = Rule("r", (Condition("time_window", "09:00-18:00"),), Action("lock_screen"))
        assert evaluate([rule], snap(now=dt.datetime(2026, 11, 9, 12, 0))) == [rule]
        assert evaluate([rule], snap(now=dt.datetime(2026, 11, 9, 20, 0))) == []

    def test_time_window_wrapping_past_midnight(self):
        rule = Rule("r", (Condition("time_window", "22:00-06:00"),), Action("lock_screen"))
        assert evaluate([rule], snap(now=dt.datetime(2026, 11, 9, 23, 0))) == [rule]
        assert evaluate([rule], snap(now=dt.datetime(2026, 11, 10, 2, 0))) == [rule]
        assert evaluate([rule], snap(now=dt.datetime(2026, 11, 9, 12, 0))) == []

    def test_day_of_week(self):
        rule = Rule("r", (Condition("day_of_week", "mon,tue"),), Action("lock_screen"))
        # 2026-11-09 is a Monday, 2026-11-10 a Tuesday, 2026-11-14 a Saturday.
        assert evaluate([rule], snap(now=dt.datetime(2026, 11, 9, 12, 0))) == [rule]
        assert evaluate([rule], snap(now=dt.datetime(2026, 11, 10, 12, 0))) == [rule]
        assert evaluate([rule], snap(now=dt.datetime(2026, 11, 14, 12, 0))) == []

    def test_day_of_week_rejects_other_days(self):
        rule = Rule("r", (Condition("day_of_week", "sat"),), Action("lock_screen"))
        assert evaluate([rule], snap(now=dt.datetime(2026, 11, 9, 12, 0))) == []

    def test_unknown_condition_never_holds(self):
        rule = Rule("r", (Condition("nonsense", "1"),), Action("lock_screen"))
        assert evaluate([rule], snap()) == []

    def test_malformed_number_never_holds(self):
        rule = Rule("r", (Condition("idle_at_least", "soon"),), Action("lock_screen"))
        assert evaluate([rule], snap(idle_seconds=99999.0)) == []

    def test_malformed_time_window_never_holds(self):
        rule = Rule("r", (Condition("time_window", "nonsense"),), Action("lock_screen"))
        assert evaluate([rule], snap()) == []


class TestEvaluate:
    def test_all_conditions_must_hold(self):
        rule = Rule(
            "r",
            (Condition("idle_at_least", "600"), Condition("cpu_below", "15")),
            Action("set_power_plan", "节能"),
        )
        assert evaluate([rule], snap(idle_seconds=700, cpu_percent=5)) == [rule]
        assert evaluate([rule], snap(idle_seconds=700, cpu_percent=50)) == []
        assert evaluate([rule], snap(idle_seconds=10, cpu_percent=5)) == []

    def test_disabled_rule_does_not_fire(self):
        rule = Rule("r", (), Action("lock_screen"), enabled=False)
        assert evaluate([rule], snap()) == []

    def test_rule_with_no_conditions_always_fires(self):
        rule = Rule("r", (), Action("lock_screen"))
        assert evaluate([rule], snap()) == [rule]

    def test_higher_priority_first(self):
        low = Rule("low", (), Action("lock_screen"), priority=1)
        high = Rule("high", (), Action("lock_screen"), priority=9)
        result = evaluate([low, high], snap())
        assert [r.name for r in result] == ["high", "low"]

    def test_equal_priority_keeps_input_order(self):
        first = Rule("a", (), Action("lock_screen"), priority=5)
        second = Rule("b", (), Action("lock_screen"), priority=5)
        result = evaluate([first, second], snap())
        assert [r.name for r in result] == ["a", "b"]


class TestRoundTrip:
    def test_rule_survives_a_dict_round_trip(self):
        rule = Rule(
            "晚间节能",
            (Condition("time_window", "22:00-07:00"), Condition("cpu_below", "10")),
            Action("set_power_plan", "节能"),
            enabled=False,
            priority=3,
        )
        assert Rule.from_dict(rule.to_dict()) == rule

    def test_condition_survives_a_dict_round_trip(self):
        condition = Condition("process_running", "steam")
        assert Condition.from_dict(condition.to_dict()) == condition

    def test_action_survives_a_dict_round_trip(self):
        action = Action("keep_awake", "on")
        assert Action.from_dict(action.to_dict()) == action
