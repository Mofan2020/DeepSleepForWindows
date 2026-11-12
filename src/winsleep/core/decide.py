"""The power decision engine.

This module decides *what should happen*, and nothing else.  It takes the
current machine state and the user's settings, and returns the intent:
which power plan should be active, whether the machine should be held
awake, whether the screen should be locked.

Keeping the decision separate from the acting is what makes it testable.
Every branch below has a test in ``tests/test_decide.py``.

The shape of the decision follows what the user asked for:

* idle for a while and the CPU is quiet -> switch to the idle plan
* a game is running                  -> switch to the active plan
* the user is at the keyboard        -> switch to the active plan
* server mode is on                  -> hold the machine awake, and lock
                                        the screen once nobody has been
                                        at the keyboard for a while
"""

from __future__ import annotations

import dataclasses

from .rules import Snapshot
from .servermode import Decision, Phase

__all__ = ["Intent", "decide"]


@dataclasses.dataclass(frozen=True)
class Intent:
    """What the daemon should do about the power state."""

    #: The plan that should be active, or ``None`` to leave it alone.
    plan: str | None = None

    #: Whether the machine should be held awake.
    keep_awake: bool = False

    #: Whether the screen should be locked right now.
    lock_screen: bool = False

    #: The remaining seconds until the screen gets locked, or ``None``
    #: when server mode is not counting down.
    lock_in: int | None = None

    #: Where server mode is, so the daemon can carry the phase over.
    phase: Phase = Phase.OFF

    #: A short human readable explanation, shown in the UI.
    reason: str = ""


def decide(
    snap: Snapshot,
    *,
    idle_plan: str,
    active_plan: str,
    idle_after_seconds: int,
    idle_cpu_percent: float,
    game_running: bool,
    keep_awake_enabled: bool,
    server_mode: bool,
    server_lock_seconds: int,
    phase: Phase = Phase.OFF,
    override_until: float = 0.0,
) -> Intent:
    """Work out what should happen right now.

    ``idle_after_seconds`` is how long the user has to be away before the
    idle plan applies.  It is deliberately *not* called ``idle_seconds``:
    that name belongs to the snapshot, where it means how idle the user
    is *right now*.  Keeping the two apart avoids a very easy mistake.

    ``override_until`` is an absolute time in epoch seconds.  While it is
    in the future the machine is held awake whatever the idle logic says,
    and it stops mattering the moment it passes - there is no state to
    clean up and no way to leave the machine stuck awake because a window
    was closed before the timer could be cancelled.
    """
    intent = _decide_plain(
        snap,
        idle_plan=idle_plan,
        active_plan=active_plan,
        idle_after_seconds=idle_after_seconds,
        idle_cpu_percent=idle_cpu_percent,
        game_running=game_running,
        keep_awake_enabled=keep_awake_enabled,
        server_mode=server_mode,
        server_lock_seconds=server_lock_seconds,
        phase=phase,
    )

    remaining = override_until - snap.now.timestamp()
    if remaining <= 0:
        return intent

    # The plan is whatever the idle logic worked out; only the "do not
    # sleep" part is overridden.  Keeping the plan decision in one place
    # means an override cannot accidentally leave the machine on the fast
    # plan all night.
    minutes = int(remaining // 60)
    return dataclasses.replace(
        intent,
        keep_awake=True,
        reason=f"timed override, {minutes} min left",
    )


def _decide_plain(
    snap: Snapshot,
    *,
    idle_plan: str,
    active_plan: str,
    idle_after_seconds: int,
    idle_cpu_percent: float,
    game_running: bool,
    keep_awake_enabled: bool,
    server_mode: bool,
    server_lock_seconds: int,
    phase: Phase = Phase.OFF,
) -> Intent:
    idle_for = snap.idle_seconds

    # --- server mode ----------------------------------------------------
    # Server mode owns the machine while it is on: it holds the machine
    # awake and it is the thing that locks the screen.
    if server_mode:
        decision = _server_mode_decision(phase, idle_for, server_lock_seconds)
        return Intent(
            plan=None,
            keep_awake=True,
            lock_screen=decision.kind == "lock_now",
            lock_in=decision.remaining if decision.kind == "countdown" else None,
            phase=_next_phase(phase, decision),
            reason=_server_mode_reason(decision),
        )

    # --- normal mode ----------------------------------------------------
    # A running game wins over everything: the user is clearly busy, and
    # the whole point of the active plan is to not get in the way.
    if game_running:
        return Intent(
            plan=active_plan or None,
            keep_awake=keep_awake_enabled,
            reason="a game is running",
        )

    # The user is at the keyboard - stay on the fast plan.
    if idle_for < idle_after_seconds:
        return Intent(
            plan=active_plan or None,
            keep_awake=keep_awake_enabled,
            reason="the user is active",
        )

    # Idle for long enough.  The CPU also has to be quiet, otherwise
    # something is busy and the machine should not slow down.
    if snap.cpu_percent >= idle_cpu_percent:
        return Intent(
            plan=active_plan or None,
            keep_awake=keep_awake_enabled,
            reason=f"cpu is busy ({snap.cpu_percent:.0f}%)",
        )

    return Intent(
        plan=idle_plan or None,
        keep_awake=keep_awake_enabled,
        reason=f"idle for {idle_for:.0f}s",
    )


def _server_mode_decision(
    phase: Phase, idle_for: float, lock_seconds: int
) -> Decision:
    from .servermode import decide as decide_server_mode

    return decide_server_mode(phase, idle_for, lock_seconds)


def _next_phase(phase: Phase, decision: Decision) -> Phase:
    """The phase server mode should be in after this decision."""
    if decision.kind == "lock_now":
        return Phase.LOCKED
    if decision.kind == "countdown":
        return Phase.WAITING_FOR_IDLE
    # "idle" and "hold_at_zero" keep the phase they were in.
    if phase is Phase.OFF:
        return Phase.STARTING
    return phase


def _server_mode_reason(decision: Decision) -> str:
    if decision.kind == "lock_now":
        return "server mode: locking the screen"
    if decision.kind == "countdown":
        return f"server mode: locking in {decision.remaining}s"
    if decision.kind == "hold_at_zero":
        return "server mode: the screen is locked"
    return "server mode"
