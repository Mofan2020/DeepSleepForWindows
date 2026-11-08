"""Server mode: keep the machine awake, lock it when nobody is there.

This is the Windows counterpart of Deep Sleep's server mode.  The macOS
version keeps a ``PreventUserIdleSystemSleep`` assertion alive forever and
locks the screen once the user has been idle for a while.  The Windows
equivalent is:

* hold ``ES_SYSTEM_REQUIRED`` so the machine never sleeps on its own, and
* call ``LockWorkStation`` once the user has been idle for N seconds.

The state machine is kept separate from the Windows calls so it can be
tested anywhere.  It mirrors Deep Sleep's ``ServerModeIdlePolicy``:

    off / starting         -> idle (not counting down)
    waitingForIdle + due   -> sleepNow (lock the screen)
    waitingForIdle + not   -> countdown(remaining)
    locked + still idle    -> holdAtZero (do not lock again)
    locked + user came back-> countdown(remaining)

The ``locked`` case is the important one: once the screen is locked the
countdown must stay at zero instead of firing over and over, and the only
thing that restarts it is the user coming back.
"""

from __future__ import annotations

import dataclasses
import enum

__all__ = ["Decision", "Phase", "decide"]


class Phase(enum.Enum):
    """Where server mode currently is."""

    OFF = "off"
    STARTING = "starting"
    LOCKED = "locked"
    WAITING_FOR_IDLE = "waitingForIdle"


@dataclasses.dataclass(frozen=True)
class Decision:
    """What the daemon should do on this tick.

    ``kind`` is one of ``idle``, ``countdown``, ``lock_now`` or
    ``hold_at_zero``.
    """

    kind: str
    remaining: int = 0


def decide(phase: Phase, idle_seconds: float, threshold_seconds: int) -> Decision:
    """Decide what to do given the current phase and the idle time.

    *phase* is where server mode is now, *idle_seconds* is how long the
    user has been away from the keyboard and mouse, and *threshold_seconds*
    is how long that may be before the screen gets locked.
    """
    if phase in (Phase.OFF, Phase.STARTING):
        return Decision(kind="idle")

    if phase is Phase.WAITING_FOR_IDLE:
        if idle_seconds >= threshold_seconds:
            return Decision(kind="lock_now")
        return Decision(kind="countdown", remaining=max(0, int(threshold_seconds - idle_seconds)))

    # Phase.LOCKED
    if idle_seconds >= threshold_seconds:
        # Still nobody there - hold at zero, do not lock again.
        return Decision(kind="hold_at_zero")
    # The user came back; start counting down again.
    return Decision(kind="countdown", remaining=max(0, int(threshold_seconds - idle_seconds)))
