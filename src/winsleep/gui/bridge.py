"""The bridge between the web view and the Python side.

Every public method of :class:`Bridge` is callable from the interface as
``window.pywebview.api.<name>(...)``.  The methods are thin: they read and
write the config, ask the daemon what the current state is, and return
plain JSON-friendly values.

Nothing here blocks for long.  The daemon is a separate process, so the
bridge talks to it over the loopback socket with a short timeout and
copes with it not being there.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import socket
import time
from typing import Any

from ..core import config as config_mod
from ..core import paths, winapi, winpower
from ..core.rules import Rule

log = logging.getLogger("winsleep.gui")

#: How long the bridge waits for the daemon to answer.
DAEMON_TIMEOUT_SECONDS = 3.0


class Bridge:
    """The API the web view talks to."""

    def __init__(self, config_path=None) -> None:
        self._config_path = config_path or paths.config_path()

    # -- helpers ----------------------------------------------------------

    def _load(self) -> config_mod.Config:
        return config_mod.load(self._config_path)

    def _save(self, config: config_mod.Config) -> None:
        config_mod.save(config, self._config_path)

    def _daemon_request(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Send one message to the daemon, or ``None`` when it is down."""
        try:
            port = int(paths.socket_path().read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        try:
            with socket.create_connection(
                ("127.0.0.1", port), timeout=DAEMON_TIMEOUT_SECONDS
            ) as conn:
                conn.sendall(json.dumps(message).encode("utf-8") + b"\n")
                conn.settimeout(DAEMON_TIMEOUT_SECONDS)
                reply = conn.recv(65536)
            if not reply:
                return None
            return json.loads(reply.decode("utf-8").splitlines()[0])
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.debug("the daemon did not answer: %s", exc)
            return None

    # -- the interface ----------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        """Everything the interface needs to draw itself."""
        config = self._load()
        active = winpower.active_plan()
        plans = [name for name, _ in winpower.list_plans()]

        daemon = self._daemon_request({"kind": "ping"})

        return {
            "config": config.to_dict(),
            "plans": plans,
            "active_plan": active[0] if active else "",
            "active_plan_guid": active[1] if active else "",
            "daemon_running": daemon is not None,
            "idle_seconds": winapi.idle_seconds(),
            "now": time.strftime("%H:%M:%S"),
        }

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Write the settings back.

        The whole config is replaced by what the interface sent, so the
        interface is the single source of truth for what it shows.
        """
        current = self._load()
        merged = config_mod.Config.from_dict({**current.to_dict(), **payload})
        self._save(merged)
        return {"ok": True, "config": merged.to_dict()}

    def set_server_mode(self, enabled: bool) -> dict[str, Any]:
        """Turn server mode on or off."""
        config = self._load()
        config.server_mode = bool(enabled)
        self._save(config)
        return {"ok": True, "server_mode": config.server_mode}

    def set_keep_awake(self, enabled: bool) -> dict[str, Any]:
        """Hold the machine awake, or stop doing that."""
        config = self._load()
        config.keep_awake = bool(enabled)
        self._save(config)
        return {"ok": True, "keep_awake": config.keep_awake}

    def switch_plan(self, name: str) -> dict[str, Any]:
        """Switch the active power plan right now."""
        ok, detail = winpower.set_active_plan(name)
        return {"ok": ok, "message": detail}

    def lock_screen(self) -> dict[str, Any]:
        """Lock the screen now."""
        ok = winapi.lock_workstation()
        return {"ok": ok}

    def add_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Add a rule to the config."""
        config = self._load()
        rule = Rule.from_dict(payload)
        if not rule.name:
            return {"ok": False, "message": "the rule needs a name"}
        config.rules.append(rule)
        self._save(config)
        return {"ok": True, "rules": [r.to_dict() for r in config.rules]}

    def remove_rule(self, name: str) -> dict[str, Any]:
        """Remove a rule by name."""
        config = self._load()
        before = len(config.rules)
        config.rules = [r for r in config.rules if r.name != name]
        self._save(config)
        return {"ok": len(config.rules) != before, "rules": [r.to_dict() for r in config.rules]}

    def toggle_rule(self, name: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable a rule."""
        config = self._load()
        for index, rule in enumerate(config.rules):
            if rule.name == name:
                # The dataclass is frozen, so the rule is replaced rather
                # than modified in place.
                config.rules[index] = dataclasses.replace(rule, enabled=bool(enabled))
                self._save(config)
                return {"ok": True, "rules": [r.to_dict() for r in config.rules]}
        return {"ok": False, "message": f"no such rule: {name}"}

    def open_log(self) -> dict[str, Any]:
        """The last lines of the log, so the interface can show them."""
        try:
            text = paths.log_path().read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {"ok": False, "lines": []}
        lines = text.splitlines()[-200:]
        return {"ok": True, "lines": lines}
