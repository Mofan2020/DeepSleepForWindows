"""Tests for the power decision engine."""

from __future__ import annotations

import datetime as dt

from winsleep.core.decide import decide
from winsleep.core.rules import Snapshot
from winsleep.core.servermode import Phase

NOON = dt.datetime(2026, 11, 9, 12, 0, 0)


def snap(**kwargs) -> Snapshot:
    base = {"now": NOON, "idle_seconds": 0.0, "cpu_percent": 0.0}
    base.update(kwargs)
    return Snapshot(**base)


def decide_with(
    idle_for: float = 0.0,
    idle_after: int = 600,
    cpu: float = 0.0,
    **kwargs,
):
    """Call decide() with the usual defaults.

    ``idle_for`` is how idle the user is right now; ``idle_after`` is the
    threshold.  They are different things and are named differently on
    purpose.
    """
    base = {
        "idle_plan": "节能",
        "active_plan": "Bitsum Highest Performance",
        "idle_after_seconds": idle_after,
        "idle_cpu_percent": 15.0,
        "game_running": False,
        "keep_awake_enabled": False,
        "server_mode": False,
        "server_lock_seconds": 300,
        "phase": Phase.OFF,
    }
    base.update(kwargs)
    return decide(snap(idle_seconds=idle_for, cpu_percent=cpu), **base)


class TestNormalMode:
    def test_active_user_gets_the_active_plan(self):
        intent = decide_with(idle_for=100)
        assert intent.plan == "Bitsum Highest Performance"
        assert intent.reason == "the user is active"

    def test_idle_and_quiet_cpu_gets_the_idle_plan(self):
        intent = decide_with(idle_for=700, cpu=5.0)
        assert intent.plan == "节能"
        assert "idle" in intent.reason

    def test_idle_but_busy_cpu_stays_on_the_active_plan(self):
        intent = decide_with(idle_for=700, cpu=40.0)
        assert intent.plan == "Bitsum Highest Performance"
        assert "cpu is busy" in intent.reason

    def test_a_running_game_wins_over_idle(self):
        intent = decide_with(idle_for=700, cpu=5.0, game_running=True)
        assert intent.plan == "Bitsum Highest Performance"
        assert intent.reason == "a game is running"

    def test_a_running_game_wins_over_an_active_user(self):
        intent = decide_with(idle_for=0, game_running=True)
        assert intent.reason == "a game is running"

    def test_exactly_at_the_idle_threshold_counts_as_idle(self):
        intent = decide_with(idle_for=600, cpu=5.0)
        assert intent.plan == "节能"

    def test_just_below_the_threshold_is_still_active(self):
        intent = decide_with(idle_for=599.9, cpu=5.0)
        assert intent.plan == "Bitsum Highest Performance"

    def test_cpu_exactly_at_the_threshold_is_busy(self):
        intent = decide_with(idle_for=700, cpu=15.0)
        assert intent.plan == "Bitsum Highest Performance"

    def test_keep_awake_is_carried_through(self):
        intent = decide_with(idle_for=100, keep_awake_enabled=True)
        assert intent.keep_awake is True

    def test_no_plan_configured_means_do_not_touch_it(self):
        intent = decide_with(idle_for=700, cpu=5.0, idle_plan="")
        assert intent.plan is None


class TestServerMode:
    def test_server_mode_holds_the_machine_awake(self):
        intent = decide_with(server_mode=True, phase=Phase.WAITING_FOR_IDLE)
        assert intent.keep_awake is True

    def test_server_mode_counts_down(self):
        intent = decide_with(
            server_mode=True, phase=Phase.WAITING_FOR_IDLE, idle_for=100
        )
        assert intent.lock_in == 200
        assert intent.lock_screen is False

    def test_server_mode_locks_when_the_time_is_up(self):
        intent = decide_with(
            server_mode=True, phase=Phase.WAITING_FOR_IDLE, idle_for=400
        )
        assert intent.lock_screen is True

    def test_server_mode_moves_to_locked_after_locking(self):
        intent = decide_with(
            server_mode=True, phase=Phase.WAITING_FOR_IDLE, idle_for=400
        )
        assert intent.phase is Phase.LOCKED

    def test_locked_screen_is_not_locked_again(self):
        intent = decide_with(
            server_mode=True, phase=Phase.LOCKED, idle_for=900
        )
        assert intent.lock_screen is False
        assert intent.lock_in is None
        assert intent.phase is Phase.LOCKED

    def test_the_user_coming_back_restarts_the_countdown(self):
        intent = decide_with(server_mode=True, phase=Phase.LOCKED, idle_for=5)
        assert intent.lock_in == 295
        assert intent.phase is Phase.WAITING_FOR_IDLE

    def test_server_mode_ignores_the_power_plan(self):
        intent = decide_with(
            server_mode=True, phase=Phase.WAITING_FOR_IDLE, idle_for=0
        )
        assert intent.plan is None

    def test_server_mode_ignores_a_running_game(self):
        intent = decide_with(
            server_mode=True, phase=Phase.WAITING_FOR_IDLE, game_running=True
        )
        assert intent.plan is None
