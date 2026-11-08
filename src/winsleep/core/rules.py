"""Rule engine for DeepSleep For Windows.

A rule is a small declarative object: when every condition holds, the
rule's action fires.  Everything here is plain Python - no Windows API is
touched - so the whole decision table is unit tested off Windows.

Conditions are evaluated against a :class:`Snapshot`, a flat description of
"what the machine looks like right now".  The daemon builds that snapshot
and hands it over; the engine does not know where the numbers came from.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

__all__ = [
    "Action",
    "Condition",
    "Rule",
    "Snapshot",
    "evaluate",
    "parse_duration",
    "parse_number",
]


#: The units a duration may be written in.  The value is in seconds.
_DURATION_UNITS = {
    "": 1,
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
}


def parse_number(text: str) -> float | None:
    """Read a plain number, with an optional ``%`` after it.

    Used for the CPU thresholds, which are percentages and not
    durations.
    """
    raw = text.strip()
    if raw.endswith("%"):
        raw = raw[:-1].strip()
    try:
        return float(raw)
    except ValueError:
        return None


def parse_duration(text: str) -> float | None:
    """Read a duration and return it in seconds.

    A plain number is a number of seconds.  A unit may be written
    after it - ``"10m"``, ``"2h"``, ``"1d"`` - and is understood in
    English and in Chinese (``"10分钟"``, ``"2小时"``).  ``None`` is
    returned when the text is not a duration at all.
    """
    raw = text.strip().lower()
    if not raw:
        return None

    # A Chinese unit can be longer than one character, so it is
    # looked for before the single letter ones.
    for suffix, factor in (
        ("毫秒", 0.001),
        ("秒", 1),
        ("分钟", 60),
        ("分", 60),
        ("小时", 3600),
        ("时", 3600),
        ("天", 86400),
    ):
        if raw.endswith(suffix):
            number = raw[: -len(suffix)].strip()
            try:
                return float(number) * factor
            except ValueError:
                return None

    # Split the number from the unit.
    index = 0
    while index < len(raw) and (raw[index].isdigit() or raw[index] in ".+-"):
        index += 1
    number_text = raw[:index].strip()
    unit_text = raw[index:].strip()

    if not number_text:
        return None
    factor = _DURATION_UNITS.get(unit_text)
    if factor is None:
        return None
    try:
        return float(number_text) * factor
    except ValueError:
        return None


# --------------------------------------------------------------------- model


@dataclasses.dataclass(frozen=True)
class Action:
    """What to do when a rule fires.

    Only a small, closed set of actions is supported.  Each one maps onto a
    single operation the daemon knows how to perform, so adding an action
    means adding it in both places - there is no plugin mechanism, and that
    is deliberate.

    ``kind`` is one of:

    ``set_power_plan``
        Switch the active power plan.  ``value`` is the plan name.
    ``keep_awake``
        Hold a "system required" execution state so the machine does not
        sleep.  ``value`` is ``"on"`` or ``"off"``.
    ``lock_screen``
        Lock the workstation.  ``value`` is ignored.
    ``run_command``
        Run a command line.  ``value`` is the command.
    """

    kind: str
    value: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Action:
        return cls(kind=str(raw.get("kind", "")), value=str(raw.get("value", "")))


@dataclasses.dataclass(frozen=True)
class Condition:
    """One requirement that has to hold for a rule to fire.

    ``kind`` is one of:

    ``idle_at_least``
        The user has been idle for at least ``value`` seconds.
    ``idle_below``
        The user has been idle for less than ``value`` seconds.
    ``cpu_below``
        Total CPU usage is below ``value`` percent.
    ``cpu_above``
        Total CPU usage is at least ``value`` percent.
    ``process_running``
        A process whose name matches ``value`` is running.  The match is
        case-insensitive and ignores the ``.exe`` suffix.
    ``time_window``
        The local time is inside ``value``, written as ``HH:MM-HH:MM``.
        A window whose end is earlier than its start wraps past midnight.
    ``day_of_week``
        Today is one of the days listed in ``value`` (comma separated
        names, e.g. ``mon,tue``).

    The number in ``value`` is always in seconds for the time based
    conditions and in percent for the CPU ones.  A number with a unit
    written after it - ``"10m"``, ``"2h"`` - is understood as well, so
    a rule written by hand can say what it means.  :func:`parse_duration`
    does the parsing.
    """

    kind: str
    value: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Condition:
        return cls(kind=str(raw.get("kind", "")), value=str(raw.get("value", "")))


@dataclasses.dataclass(frozen=True)
class Rule:
    """A named set of conditions and the action they trigger.

    ``enabled`` lets a rule be parked without deleting it.  ``priority``
    orders the evaluation; when several rules fire at once the daemon
    applies them in priority order (higher first).
    """

    name: str
    conditions: tuple[Condition, ...] = ()
    action: Action = Action(kind="set_power_plan", value="")
    enabled: bool = True
    priority: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "conditions": [c.to_dict() for c in self.conditions],
            "action": self.action.to_dict(),
            "enabled": self.enabled,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Rule:
        return cls(
            name=str(raw.get("name", "")),
            conditions=tuple(
                Condition.from_dict(c) for c in raw.get("conditions", []) or []
            ),
            action=Action.from_dict(raw.get("action", {}) or {}),
            enabled=bool(raw.get("enabled", True)),
            priority=int(raw.get("priority", 0) or 0),
        )


# ------------------------------------------------------------------ snapshot


@dataclasses.dataclass(frozen=True)
class Snapshot:
    """What the machine looks like at one instant.

    ``idle_seconds`` is the number of seconds since the user last touched
    the keyboard or mouse.  It is the single most important number in the
    whole program and it can only be read from inside the user's own
    session - see :mod:`winsleep.core.idle`.
    """

    now: dt.datetime
    idle_seconds: float = 0.0
    cpu_percent: float = 0.0
    running_processes: frozenset[str] = frozenset()

    def has_process(self, name: str) -> bool:
        """True when a process matching *name* is running.

        Matching ignores case and an optional ``.exe`` suffix, so
        ``"steam"``, ``"Steam"`` and ``"steam.exe"`` all match the same
        process.
        """
        wanted = name.strip().lower()
        if wanted.endswith(".exe"):
            wanted = wanted[:-4]
        if not wanted:
            return False
        for proc in self.running_processes:
            candidate = proc.lower()
            if candidate.endswith(".exe"):
                candidate = candidate[:-4]
            if candidate == wanted:
                return True
        return False


# ---------------------------------------------------------------- evaluation

_DAY_NAMES = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def _parse_time_window(value: str) -> tuple[dt.time, dt.time] | None:
    """Parse ``"HH:MM-HH:MM"`` into a pair of times."""
    raw = value.strip()
    if "-" not in raw:
        return None
    start_raw, _, end_raw = raw.partition("-")
    try:
        start = dt.time.fromisoformat(start_raw.strip())
        end = dt.time.fromisoformat(end_raw.strip())
    except ValueError:
        return None
    return start, end


def _in_time_window(now: dt.datetime, value: str) -> bool:
    window = _parse_time_window(value)
    if window is None:
        return False
    start, end = window
    current = now.time()
    if start <= end:
        return start <= current < end
    # The window wraps past midnight.
    return current >= start or current < end


def _in_day_of_week(now: dt.datetime, value: str) -> bool:
    wanted = {
        _DAY_NAMES[part.strip().lower()[:3]]
        for part in value.split(",")
        if part.strip().lower()[:3] in _DAY_NAMES
    }
    if not wanted:
        return False
    # ``weekday()`` is Monday == 0, which matches the table above.
    return now.weekday() in wanted


def _condition_holds(condition: Condition, snap: Snapshot) -> bool:
    kind = condition.kind
    value = condition.value

    if kind == "idle_at_least":
        threshold = parse_duration(value)
        if threshold is None:
            return False
        return snap.idle_seconds >= threshold

    if kind == "idle_below":
        threshold = parse_duration(value)
        if threshold is None:
            return False
        return snap.idle_seconds < threshold

    if kind == "cpu_below":
        threshold = parse_number(value)
        if threshold is None:
            return False
        return snap.cpu_percent < threshold

    if kind == "cpu_above":
        threshold = parse_number(value)
        if threshold is None:
            return False
        return snap.cpu_percent >= threshold

    if kind == "process_running":
        return snap.has_process(value)

    if kind == "time_window":
        return _in_time_window(snap.now, value)

    if kind == "day_of_week":
        return _in_day_of_week(snap.now, value)

    # An unknown condition never holds.  Failing open here would let a
    # typo in the config fire the rule for everything.
    return False


def evaluate(rules: list[Rule], snap: Snapshot) -> list[Rule]:
    """Return the rules whose conditions all hold, in priority order.

    Disabled rules are skipped.  Rules with equal priority keep their
    original relative order, which makes the result stable across runs.
    """
    fired = [rule for rule in rules if rule.enabled and _rule_holds(rule, snap)]
    return sorted(fired, key=lambda rule: -rule.priority)


def _rule_holds(rule: Rule, snap: Snapshot) -> bool:
    # A rule with no conditions fires unconditionally - that is a valid
    # way to express "always do this".
    return all(_condition_holds(c, snap) for c in rule.conditions)


def actions_of(rules: list[Rule]) -> list[Action]:
    """Flatten the actions of the given rules, keeping their order."""
    return [rule.action for rule in rules]
