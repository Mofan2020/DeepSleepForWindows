"""Tests for the server mode state machine."""

from __future__ import annotations

from winsleep.core.servermode import Decision, Phase, decide


class TestOff:
    def test_off_does_nothing(self):
        assert decide(Phase.OFF, 9999, 300) == Decision(kind="idle")

    def test_starting_does_nothing(self):
        assert decide(Phase.STARTING, 9999, 300) == Decision(kind="idle")


class TestWaitingForIdle:
    def test_counts_down(self):
        result = decide(Phase.WAITING_FOR_IDLE, 100, 300)
        assert result.kind == "countdown"
        assert result.remaining == 200

    def test_locks_when_the_time_is_up(self):
        assert decide(Phase.WAITING_FOR_IDLE, 300, 300).kind == "lock_now"

    def test_locks_a_little_over_the_threshold(self):
        assert decide(Phase.WAITING_FOR_IDLE, 300.5, 300).kind == "lock_now"

    def test_the_remaining_time_never_goes_negative(self):
        result = decide(Phase.WAITING_FOR_IDLE, 0, 300)
        assert result.remaining == 300
        assert result.remaining >= 0


class TestLocked:
    def test_stays_at_zero_while_nobody_is_there(self):
        result = decide(Phase.LOCKED, 5000, 300)
        assert result.kind == "hold_at_zero"
        assert result.remaining == 0

    def test_does_not_lock_again(self):
        assert decide(Phase.LOCKED, 5000, 300).kind != "lock_now"

    def test_the_user_coming_back_restarts_the_countdown(self):
        result = decide(Phase.LOCKED, 10, 300)
        assert result.kind == "countdown"
        assert result.remaining == 290

    def test_the_user_being_back_for_a_moment_does_not_lock_again(self):
        # The whole point of the locked phase: a short interruption
        # resets the timer, it does not cause another lock.
        result = decide(Phase.LOCKED, 1, 300)
        assert result.kind == "countdown"
        assert result.remaining == 299
