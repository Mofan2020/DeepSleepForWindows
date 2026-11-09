"""The configuration store.

A single JSON file holds the settings and the rules.  It is read and
written as a whole, atomically (write to a temporary file, then replace),
so a crash halfway through a save cannot leave a half-written config
behind.

This module is plain Python - it does not touch the Windows API - so the
round-trip behaviour is unit tested off Windows.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .rules import Rule

__all__ = ["Config", "load", "save"]


@dataclasses.dataclass
class Config:
    """Everything the user can configure."""

    #: The plan switched to when the machine is idle.
    idle_plan: str = ""

    #: The plan switched to when the machine is busy.
    active_plan: str = ""

    #: Seconds of user idle before the idle plan is applied.
    idle_seconds: int = 600

    #: Total CPU usage must be under this for the idle plan to apply.
    idle_cpu_percent: float = 15.0

    #: When set, a game process running forces the active plan.
    game_processes: tuple[str, ...] = ()

    #: When set, a game process running keeps the machine awake.
    game_keeps_awake: bool = True

    #: Whether server mode is on.
    server_mode: bool = False

    #: Seconds of user idle before server mode locks the screen.
    server_lock_seconds: int = 300

    #: Whether the machine is held awake outside of server mode.
    keep_awake: bool = False

    #: The user rules, evaluated in priority order.
    rules: list[Rule] = dataclasses.field(default_factory=list)

    #: The GUID of the plan the user edited last.  The interface opens
    #: on this one.
    last_plan_guid: str = ""

    #: How many lines the log file keeps.  Zero means "no limit on the
    #: number of lines".
    log_max_lines: int = 0

    #: How many days of log lines are kept.  Zero means "no limit on the
    #: age of the lines".
    log_max_age_days: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "idle_plan": self.idle_plan,
            "active_plan": self.active_plan,
            "idle_seconds": self.idle_seconds,
            "idle_cpu_percent": self.idle_cpu_percent,
            "game_processes": list(self.game_processes),
            "game_keeps_awake": self.game_keeps_awake,
            "server_mode": self.server_mode,
            "server_lock_seconds": self.server_lock_seconds,
            "keep_awake": self.keep_awake,
            "rules": [rule.to_dict() for rule in self.rules],
            "last_plan_guid": self.last_plan_guid,
            "log_max_lines": self.log_max_lines,
            "log_max_age_days": self.log_max_age_days,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Config:
        # ``or`` is not used to fill in the defaults here: it would
        # swallow a zero.  A user who asks for a CPU threshold of 0
        # means "only when the CPU is completely idle", and that has to
        # survive the round trip.
        return cls(
            idle_plan=str(raw.get("idle_plan", "") or ""),
            active_plan=str(raw.get("active_plan", "") or ""),
            idle_seconds=int(_number(raw, "idle_seconds", 600)),
            idle_cpu_percent=float(_number(raw, "idle_cpu_percent", 15.0)),
            game_processes=tuple(
                str(p) for p in raw.get("game_processes", []) or [] if str(p).strip()
            ),
            game_keeps_awake=bool(raw.get("game_keeps_awake", True)),
            server_mode=bool(raw.get("server_mode", False)),
            server_lock_seconds=int(_number(raw, "server_lock_seconds", 300)),
            keep_awake=bool(raw.get("keep_awake", False)),
            rules=[Rule.from_dict(r) for r in raw.get("rules", []) or []],
            last_plan_guid=str(raw.get("last_plan_guid", "") or ""),
            log_max_lines=max(0, int(_number(raw, "log_max_lines", 0))),
            log_max_age_days=max(0.0, float(_number(raw, "log_max_age_days", 0.0))),
        )



def _number(raw: dict[str, Any], key: str, default: float) -> float:
    """Read a number from the config, falling back to *default*.

    Only a missing key or a value that is not a number at all falls
    back.  A zero is a zero and stays a zero.
    """
    value = raw.get(key, default)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load(path: Path) -> Config:
    """Read the config file, returning defaults when it is missing.

    A file that cannot be parsed is treated as missing rather than fatal:
    a corrupt config should not stop the daemon from starting, and the
    user can always rewrite it from the GUI.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Config()
    if not isinstance(raw, dict):
        return Config()
    return Config.from_dict(raw)


def save(config: Config, path: Path) -> None:
    """Write the config file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.to_dict(), indent=2, ensure_ascii=False)

    handle, temp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        # The rename may not have happened; do not leave the temp file behind.
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
