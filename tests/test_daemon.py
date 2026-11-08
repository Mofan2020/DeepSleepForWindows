"""Tests for the daemon's own behaviour.

The decision engine is tested in ``test_decide.py`` and the wire format
in ``test_ipc.py``.  What is left is the part where the daemon acts:
it reads the plan that is really active, compares it with the plan that
should be active, and puts it back when they differ.  That is the
behaviour the macOS version has for ``pmset disablesleep``, and it is
the part that has to keep working when something else on the machine
keeps changing the power plan.
"""

from __future__ import annotations

import dataclasses

import pytest

from winsleep.core import daemon as daemon_mod
from winsleep.core.decide import Intent
from winsleep.core.rules import Action, Condition, Rule, Snapshot


class FakePower:
    """A stand-in for the power plan helpers.

    The daemon only uses four of them, so the fake records what was
    asked of it and lets the test decide what the machine looks like.
    """

    def __init__(self, active: str | None = "Bitsum Highest Performance") -> None:
        #: The plan that is really active: (name, guid).
        self.active = (active, "guid-of-" + active) if active else None
        #: Every call to set_active_plan.
        self.switches: list[str] = []
        #: What set_active_plan returns.
        self.switch_result = (True, "ok")

    def active_plan(self):
        return self.active

    def plan_guid_of(self, name: str) -> str | None:
        if self.active and self.active[0] == name:
            return self.active[1]
        return "guid-of-" + name

    def set_active_plan(self, name: str):
        self.switches.append(name)
        ok, detail = self.switch_result
        if ok:
            self.active = (name, "guid-of-" + name)
        return ok, detail

    def processes_running(self, names):
        return set()


@pytest.fixture
def power(monkeypatch):
    """Replace the power helpers with the fake."""
    fake = FakePower()
    monkeypatch.setattr(daemon_mod, "winpower", fake)
    return fake


@pytest.fixture
def daemon():
    return daemon_mod.Daemon()


class TestKeepingThePowerPlanRight:
    def test_the_plan_is_left_alone_when_it_is_already_right(self, daemon, power):
        daemon._apply_plan(Intent(plan="Bitsum Highest Performance"))

        assert power.switches == []

    def test_a_wrong_plan_is_put_back(self, daemon, power):
        power.active = ("节能", "guid-of-节能")

        daemon._apply_plan(Intent(plan="Bitsum Highest Performance"))

        assert power.switches == ["Bitsum Highest Performance"]
        assert power.active[0] == "Bitsum Highest Performance"

    def test_no_plan_means_no_change(self, daemon, power):
        daemon._apply_plan(Intent(plan=None))

        assert power.switches == []

    def test_the_plan_is_checked_again_every_time(self, daemon, power):
        # Something else changed the plan.  It has to be put back, and
        # it has to be put back again the next time round - the daemon
        # must not remember that it already fixed it once.
        power.active = ("节能", "guid-of-节能")
        daemon._apply_plan(Intent(plan="Bitsum Highest Performance"))
        assert power.switches == ["Bitsum Highest Performance"]

        power.active = ("节能", "guid-of-节能")
        daemon._apply_plan(Intent(plan="Bitsum Highest Performance"))
        assert power.switches == ["Bitsum Highest Performance"] * 2

    def test_a_plan_that_is_already_right_is_not_switched(self, daemon, power):
        daemon._apply_plan(Intent(plan="Bitsum Highest Performance"))

        assert power.switches == []

    def test_the_current_plan_is_remembered(self, daemon, power):
        daemon._apply_plan(Intent(plan="Bitsum Highest Performance"))
        assert daemon._current_plan == "Bitsum Highest Performance"


class TestWhenThePlanCannotBeChanged:
    def test_a_failure_is_counted(self, daemon, power):
        power.switch_result = (False, "access denied")

        daemon._apply_plan(Intent(plan="节能"))

        assert daemon._plan_failures == 1

    def test_the_warning_comes_after_three_failures(self, daemon, power):
        power.switch_result = (False, "access denied")

        daemon._apply_plan(Intent(plan="节能"))
        daemon._apply_plan(Intent(plan="节能"))
        assert daemon._conflict_warning == ""

        daemon._apply_plan(Intent(plan="节能"))
        assert daemon._conflict_warning

    def test_a_success_clears_the_failures(self, daemon, power):
        power.switch_result = (False, "access denied")
        daemon._apply_plan(Intent(plan="节能"))
        daemon._apply_plan(Intent(plan="节能"))

        power.switch_result = (True, "ok")
        daemon._apply_plan(Intent(plan="节能"))

        assert daemon._plan_failures == 0

    def test_a_success_clears_the_warning(self, daemon, power):
        power.switch_result = (False, "access denied")
        for _ in range(3):
            daemon._apply_plan(Intent(plan="节能"))
        assert daemon._conflict_warning

        power.switch_result = (True, "ok")
        daemon._apply_plan(Intent(plan="节能"))

        assert daemon._conflict_warning == ""


class TestTheAgentState:
    def test_nothing_is_decided_while_the_agent_is_silent(self, daemon, power):
        # The daemon must not assume the machine is idle when nobody has
        # reported the idle time - that would switch the plan to the
        # quiet one while the user is at the keyboard.
        config = daemon_mod.config_mod.Config(active_plan="Bitsum Highest Performance")

        intent = daemon._decide(config)

        assert intent.plan is None
        assert "agent" in intent.reason

    def test_a_fresh_report_is_used(self, daemon, power):
        config = daemon_mod.config_mod.Config(
            active_plan="Bitsum Highest Performance",
            idle_plan="节能",
            idle_seconds=600,
            idle_cpu_percent=999.0,
        )
        daemon._server.agent.idle_seconds = 10.0
        daemon._server.agent.updated_at = __import__("time").time()

        intent = daemon._decide(config)

        assert intent.plan == "Bitsum Highest Performance"

    def test_a_stale_report_is_not_used(self, daemon, power):
        config = daemon_mod.config_mod.Config(active_plan="Bitsum Highest Performance")
        daemon._server.agent.idle_seconds = 10.0
        daemon._server.agent.updated_at = 1.0  # a long time ago

        intent = daemon._decide(config)

        assert intent.plan is None


class TestTheTick:
    def test_a_tick_does_not_crash(self, daemon, power, tmp_path):
        daemon._config_path = tmp_path / "config.json"
        daemon._tick()

    def test_the_intent_is_remembered(self, daemon, power, tmp_path):
        daemon._config_path = tmp_path / "config.json"
        daemon._tick()

        assert daemon._last_intent is not None


def test_the_intent_is_a_plain_dataclass():
    # The daemon compares intents to decide whether anything changed.
    # A frozen dataclass compares by value, which is what that needs.
    one = Intent(plan="节能")
    two = Intent(plan="节能")
    assert one == two
    assert dataclasses.replace(one, plan="平衡") != one


class TestTheRules:
    """The rules the user writes have to actually be applied."""

    def _config(self, **kwargs):
        base = {
            "idle_plan": "节能",
            "active_plan": "Bitsum Highest Performance",
            "idle_seconds": 600,
            "idle_cpu_percent": 15.0,
        }
        base.update(kwargs)
        return daemon_mod.config_mod.Config(**base)

    def _snap(self, **kwargs):
        # The user has been away for a while by default, so a rule that
        # asks for "idle for at least N seconds" fires.
        base = {
            "now": __import__("datetime").datetime(2026, 11, 9, 12, 0),
            "idle_seconds": 100.0,
            "cpu_percent": 0.0,
            "running_processes": frozenset(),
        }
        base.update(kwargs)
        return Snapshot(**base)

    def test_a_rule_that_sets_a_plan_wins(self, daemon, power):
        config = self._config(
            rules=[
                Rule(
                    "夜里",
                    (Condition("time_window", "00:00-23:59"),),
                    Action("set_power_plan", "卓越性能"),
                )
            ]
        )

        intent = daemon._apply_rules(config, self._snap(), Intent(plan="节能"))

        assert intent.plan == "卓越性能"

    def test_a_rule_that_does_not_fire_changes_nothing(self, daemon, power):
        config = self._config(
            rules=[
                Rule(
                    "半夜",
                    (Condition("time_window", "03:00-04:00"),),
                    Action("set_power_plan", "卓越性能"),
                )
            ]
        )

        intent = daemon._apply_rules(config, self._snap(), Intent(plan="节能"))

        assert intent.plan == "节能"

    def test_a_rule_can_switch_the_keep_awake(self, daemon, power):
        config = self._config(
            rules=[
                Rule(
                    "保持",
                    (Condition("idle_at_least", "10"),),
                    Action("keep_awake", "on"),
                )
            ]
        )

        intent = daemon._apply_rules(config, self._snap(), Intent(keep_awake=False))

        assert intent.keep_awake is True

    def test_a_rule_can_ask_for_the_screen_to_be_locked(self, daemon, power):
        config = self._config(
            rules=[
                Rule(
                    "锁屏",
                    (Condition("idle_at_least", "10"),),
                    Action("lock_screen"),
                )
            ]
        )

        intent = daemon._apply_rules(config, self._snap(), Intent())

        assert intent.lock_screen is True

    def test_a_rule_can_look_at_a_running_process(self, daemon, power):
        config = self._config(
            rules=[
                Rule(
                    "游戏",
                    (Condition("process_running", "steam"),),
                    Action("set_power_plan", "高性能"),
                )
            ]
        )

        snap = self._snap(running_processes=frozenset({"steam"}))
        intent = daemon._apply_rules(config, snap, Intent(plan="节能"))

        assert intent.plan == "高性能"

    def test_the_highest_priority_rule_wins(self, daemon, power):
        config = self._config(
            rules=[
                Rule(
                    "低",
                    (Condition("idle_at_least", "1"),),
                    Action("set_power_plan", "节能"),
                    priority=1,
                ),
                Rule(
                    "高",
                    (Condition("idle_at_least", "1"),),
                    Action("set_power_plan", "卓越性能"),
                    priority=9,
                ),
            ]
        )

        intent = daemon._apply_rules(config, self._snap(), Intent(plan="Bitsum"))

        assert intent.plan == "卓越性能"

    def test_no_rules_leaves_the_intent_alone(self, daemon, power):
        intent = daemon._apply_rules(self._config(), self._snap(), Intent(plan="节能"))

        assert intent.plan == "节能"

    def test_the_reason_says_which_rule_fired(self, daemon, power):
        config = self._config(
            rules=[
                Rule(
                    "夜里",
                    (Condition("idle_at_least", "1"),),
                    Action("set_power_plan", "卓越性能"),
                )
            ]
        )

        intent = daemon._apply_rules(config, self._snap(), Intent(plan="节能"))

        assert "夜里" in intent.reason

    def test_the_rules_reach_the_decision(self, daemon, power):
        # The whole way through: the daemon reads the config, decides,
        # and the rule has to end up in the result.  A test that only
        # calls _apply_rules would not notice if the daemon stopped
        # calling it at all.
        config = self._config(
            rules=[
                Rule(
                    "夜里",
                    (Condition("idle_at_least", "10"),),
                    Action("set_power_plan", "卓越性能"),
                )
            ]
        )
        daemon._server.agent.idle_seconds = 100.0
        daemon._server.agent.updated_at = __import__("time").time()

        intent = daemon._decide(config)

        assert intent.plan == "卓越性能"


class TestTheWatchedProcesses:
    def test_the_game_processes_are_watched(self, daemon, power):
        seen = []

        def fake_running(names):
            seen.extend(names)
            return set()

        power.processes_running = fake_running
        daemon._watched_processes(daemon_mod.config_mod.Config(game_processes=("steam",)))

        assert "steam" in seen

    def test_the_process_names_of_the_rules_are_watched(self, daemon, power):
        seen = []

        def fake_running(names):
            seen.extend(names)
            return set()

        power.processes_running = fake_running
        daemon._watched_processes(
            daemon_mod.config_mod.Config(
                rules=[
                    Rule(
                        "r",
                        (Condition("process_running", "yysls"),),
                        Action("lock_screen"),
                    )
                ]
            )
        )

        assert "yysls" in seen

    def test_nothing_is_looked_up_when_nothing_is_watched(self, daemon, power):
        calls = []

        def fake_running(names):
            calls.append(names)
            return set()

        power.processes_running = fake_running
        daemon._watched_processes(daemon_mod.config_mod.Config())

        assert calls == []
