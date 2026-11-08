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
        #: The window, once it exists.  The file dialog needs it.
        self._window: Any = None

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
        plans = winpower.list_plans()

        daemon = self._daemon_request({"kind": "ping"})

        return {
            "config": config.to_dict(),
            "plans": [name for name, _ in plans],
            "plan_guids": [guid for _, guid in plans],
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

    # -- the background service -------------------------------------------

    def service_status(self) -> dict[str, Any]:
        """Whether the background service is installed and running.

        Without this the interface can only say "not running" and the
        user has no way of doing anything about it.
        """
        from ..core import install

        try:
            return install.service_status()
        except Exception as exc:  # noqa: BLE001 - the interface has to know
            log.warning("could not read the service state: %s", exc)
            return {"installed": False, "running": False, "agent_running": False}

    def install_service(self) -> dict[str, Any]:
        """Register the background tasks and start them."""
        from ..core import install

        try:
            ok, detail = install.install_all()
        except Exception as exc:  # noqa: BLE001 - the reason has to show
            log.exception("the background service could not be installed")
            return {"ok": False, "message": f"安装失败：{exc}"}
        if not ok:
            return {"ok": False, "message": f"安装失败：{detail}"}
        try:
            ok, detail = install.start_daemon()
        except Exception as exc:  # noqa: BLE001
            log.exception("the background service could not be started")
            return {"ok": False, "message": f"启动失败：{exc}"}
        return {"ok": ok, "message": detail}

    def start_service(self) -> dict[str, Any]:
        """Start the background service."""
        from ..core import install

        ok, detail = install.start_daemon()
        return {"ok": ok, "message": detail}

    def stop_service(self) -> dict[str, Any]:
        """Stop the background service."""
        from ..core import install

        ok, detail = install.stop_daemon()
        return {"ok": ok, "message": detail}

    def uninstall_service(self) -> dict[str, Any]:
        """Remove the background tasks."""
        from ..core import install

        ok, detail = install.uninstall_all()
        return {"ok": ok, "message": detail}

    # -- the schedule ------------------------------------------------------

    def schedule_sleep(self, clock: str, date: str = "") -> dict[str, Any]:
        """Put the machine to sleep at a given time."""
        from ..core import install

        ok, detail = install.schedule_sleep(clock, date)
        return {"ok": ok, "message": detail}

    def cancel_schedule(self) -> dict[str, Any]:
        """Cancel the scheduled sleep."""
        from ..core import install

        ok, detail = install.cancel_scheduled_sleep()
        return {"ok": ok, "message": detail}

    # -- the processes and the files --------------------------------------

    def list_processes(self, wanted: str = "") -> dict[str, Any]:
        """The processes running right now, for the process picker.

        The list is sorted with the windowed programs first, because
        those are the ones a rule is usually about.  *wanted* filters
        the list by name.
        """
        try:
            import psutil
        except ImportError:
            return {"ok": False, "processes": [], "message": "psutil is missing"}

        needle = (wanted or "").strip().lower()
        rows: list[dict[str, Any]] = []
        try:
            for process in psutil.process_iter(["name", "exe", "pid"]):
                try:
                    name = process.info.get("name") or ""
                    exe = process.info.get("exe") or ""
                except Exception:  # noqa: BLE001 - a process can die mid-read
                    continue
                if not name:
                    continue
                if needle and needle not in name.lower():
                    continue
                rows.append({"name": name, "exe": exe, "pid": process.info.get("pid", 0)})
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "processes": [], "message": str(exc)}

        rows.sort(key=lambda row: row["name"].lower())
        return {"ok": True, "processes": rows}

    def pick_file(self, title: str = "") -> dict[str, Any]:
        """Let the user choose a file with the system's own dialog.

        The dialog is opened by the window, so the bridge needs a
        handle on it.  When there is no window - the program is being
        driven from a command line - nothing is opened and the answer
        says so.
        """
        window = self._window
        if window is None:
            return {"ok": False, "path": "", "message": "没有窗口可以打开文件选择器"}
        try:
            import webview

            chosen = window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("所有文件 (*.*)",),
            )
        except Exception as exc:  # noqa: BLE001 - the dialog can fail
            log.warning("the file dialog failed: %s", exc)
            return {"ok": False, "path": "", "message": str(exc)}

        if not chosen:
            return {"ok": False, "path": "", "message": "没有选择文件"}
        first = chosen[0] if isinstance(chosen, list | tuple) else chosen
        return {"ok": True, "path": str(first)}

    def attach_window(self, window: Any) -> None:
        """Remember the window, so the file dialog can be opened."""
        self._window = window

    # -- the contents of a power plan -------------------------------------

    def plan_settings(self, guid: str) -> dict[str, Any]:
        """The editable settings of one power plan."""
        from ..core import powersettings

        try:
            rows = powersettings.read_plan_settings(guid)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "settings": [], "message": str(exc)}
        return {"ok": True, "settings": rows}

    def save_plan_settings(self, guid: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
        """Change the settings of one power plan.

        Writing needs administrator rights, so the work is done by the
        daemon, which runs as SYSTEM.  When the daemon is not there the
        change is refused with an explanation rather than half done.
        """
        reply = self._daemon_request(
            {
                "kind": "set_plan_settings",
                "guid": guid,
                "changes": changes,
            }
        )
        if reply is None:
            return {
                "ok": False,
                "message": "后台服务没有在运行，改不了电源计划的内容。先在上面启动它。",
            }
        return {"ok": bool(reply.get("ok")), "message": reply.get("message", "")}

    def duplicate_plan(self, guid: str, name: str) -> dict[str, Any]:
        """Copy a power plan and give the copy a name."""
        reply = self._daemon_request(
            {"kind": "duplicate_plan", "guid": guid, "name": name}
        )
        if reply is None:
            return {
                "ok": False,
                "message": "后台服务没有在运行，复制不了电源计划。先在上面启动它。",
            }
        return {
            "ok": bool(reply.get("ok")),
            "message": reply.get("message", ""),
            "guid": reply.get("guid", ""),
        }

    def delete_plan(self, guid: str) -> dict[str, Any]:
        """Delete a power plan."""
        reply = self._daemon_request({"kind": "delete_plan", "guid": guid})
        if reply is None:
            return {
                "ok": False,
                "message": "后台服务没有在运行，删不掉电源计划。先在上面启动它。",
            }
        return {"ok": bool(reply.get("ok")), "message": reply.get("message", "")}
