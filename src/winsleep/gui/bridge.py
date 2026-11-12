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

import ctypes
import dataclasses
import json
import logging
import socket
import sys
import time
from typing import Any

from ..core import config as config_mod
from ..core import paths, winapi, winpower
from ..core.rules import Rule

log = logging.getLogger("winsleep.gui")


def _is_admin() -> bool:
    """Check if the current process has administrator privileges."""
    if sys.platform != "win32":
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def _run_elevated_command(command: str, timeout_seconds: int = 120) -> dict[str, Any]:
    """Run one sub-command elevated and wait for it to finish.

    The GUI stays open.  A second copy of the program is started with
    administrator rights, but it only runs the given sub-command (like
    ``install``) and exits.  This one waits for it, so the result is
    known right away.

    This replaces the old approach of killing the window and restarting
    the whole program elevated - which threw the window away and never
    actually ran the command.
    """
    if sys.platform != "win32":
        return {"ok": False, "message": "只在 Windows 上可用"}

    executable = sys.executable
    if getattr(sys, "frozen", False):
        params = command
    else:
        params = f"-m winsleep.cli {command}"

    log.info("running elevated: %s %s", command, executable)

    # ShellExecuteExW with SEE_MASK_NOCLOSEPROCESS gives us a handle
    # we can wait on.  ShellExecuteW does not.
    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_HIDE = 0

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint),
            ("fMask", ctypes.c_uint),
            ("hwnd", ctypes.c_void_p),
            ("lpVerb", ctypes.c_wchar_p),
            ("lpFile", ctypes.c_wchar_p),
            ("lpParameters", ctypes.c_wchar_p),
            ("lpDirectory", ctypes.c_wchar_p),
            ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.c_void_p),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", ctypes.c_wchar_p),
            ("hkeyClass", ctypes.c_void_p),
            ("dwHotKey", ctypes.c_uint),
            ("hIcon", ctypes.c_void_p),
            ("hProcess", ctypes.c_void_p),
        ]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = params
    info.nShow = SW_HIDE

    try:
        ok = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"无法启动提权进程：{exc}"}

    if not ok:
        # The user clicked "No" on the UAC prompt, or the system
        # refused.  GetLastError gives the reason.
        error = ctypes.windll.kernel32.GetLastError()
        return {"ok": False, "message": f"提权被拒绝（错误码 {error}）"}

    if info.hProcess:
        # Wait for the elevated process to finish.
        #
        # **这两个函数必须声明 argtypes。** `info.hProcess` 是
        # c_void_p 字段，从 ctypes 结构体里读出来是一个**普通的
        # Python int**（64 位句柄能到 1e18 量级）。不声明 argtypes 的话
        # ctypes 按 C 的 `int` 处理第一个参数，直接抛
        #
        #     argument 1: OverflowError: int too long to convert
        #
        # 提权流程就断在这儿：UAC 已经点了「是」，进程起来了，可这边
        # 崩了，界面上只看到一句 OverflowError。
        kernel32 = ctypes.windll.kernel32
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int

        WAIT_TIMEOUT = 0x00000102
        result = kernel32.WaitForSingleObject(
            ctypes.c_void_p(info.hProcess), timeout_seconds * 1000
        )
        kernel32.CloseHandle(ctypes.c_void_p(info.hProcess))
        if result == WAIT_TIMEOUT:
            return {"ok": False, "message": "安装超时，请重试"}

    return {"ok": True, "message": "完成"}

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

        # Ask the daemon what it decided and why.  A running daemon that
        # has not decided yet answers ``ok: false``; both "no daemon" and
        # "no decision" end up as ``None`` here, which the window shows as
        # "not decided" instead of inventing a reason.
        intent = None
        if daemon is not None:
            answer = self._daemon_request({"kind": "status"})
            if answer is not None and answer.get("ok"):
                intent = answer

        now = time.time()
        return {
            "config": config.to_dict(),
            "plans": [name for name, _ in plans],
            "plan_guids": [guid for _, guid in plans],
            "active_plan": active[0] if active else "",
            "active_plan_guid": active[1] if active else "",
            "daemon_running": daemon is not None,
            "idle_seconds": winapi.idle_seconds(),
            "intent": intent,
            "override_remaining": max(0, int(config.keep_awake_until - now)),
            "server_now": now,
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

    def set_override(self, minutes: int) -> dict[str, Any]:
        """Hold the machine awake for a while, then let it go on its own.

        The end time is written into the config rather than kept in the
        window, so closing the window does not quietly cancel it and
        restarting the program does not quietly extend it.
        """
        config = self._load()
        try:
            wanted = int(minutes)
        except (TypeError, ValueError):
            wanted = 0

        if wanted <= 0:
            config.keep_awake_until = 0.0
            self._save(config)
            return {"ok": True, "minutes": 0, "remaining": 0, "message": "已取消"}

        until = time.time() + wanted * 60
        config.keep_awake_until = until
        self._save(config)
        hours, rest = divmod(wanted, 60)
        shown = f"{hours} 小时 {rest} 分钟" if hours else f"{rest} 分钟"
        return {
            "ok": True,
            "minutes": wanted,
            "remaining": wanted * 60,
            "message": f"接下来 {shown}不睡觉",
        }

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

    def open_log(self, max_lines: int = 0) -> dict[str, Any]:
        """The last lines of the log, so the interface can show them."""
        try:
            text = paths.log_path().read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {"ok": False, "lines": []}
        lines = text.splitlines()[-200:]
        return {"ok": True, "lines": lines}

    def log_tail(self, after: int = 0) -> dict[str, Any]:
        """The log lines that are newer than the byte offset *after*.

        The window keeps the offset it got last time and sends it back,
        so the answer is only what was written since.  That is what
        makes the live view cheap: the whole file is never read again.

        When the file has been cleared or trimmed the offset points past
        the end of it.  That is answered with the whole file and the new
        offset, so the window recovers on its own.
        """

        path = paths.log_path()
        try:
            size = path.stat().st_size
        except OSError:
            return {"ok": True, "lines": [], "offset": after, "cleared": False}

        if after > size:
            # The file was shortened.  Start over from the top.
            after = 0

        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                handle.seek(after)
                text = handle.read()
                offset = handle.tell()
        except OSError:
            return {"ok": False, "lines": [], "offset": after, "cleared": False}

        lines = text.splitlines()
        if not text.endswith("\n") and lines:
            # The last line is still being written.  Leave it for the
            # next round instead of showing half of it.
            cut = text.rfind("\n") + 1
            offset -= len(text) - cut
            lines = lines[:-1]

        return {
            "ok": True,
            "lines": lines,
            "offset": offset,
            "cleared": after == 0 and size == 0,
        }

    def clear_log(self) -> dict[str, Any]:
        """Empty the log file."""
        from ..core import logs

        return logs.clear_log(paths.log_path())

    def trim_log(self, max_lines: int = 0, max_age_days: float = 0.0) -> dict[str, Any]:
        """Drop the old lines from the log file.

        The numbers come from the settings when they are not given, so
        the button "apply the limits" works without the window having
        to pass anything.
        """
        from ..core import logs

        config = self._load()
        lines = int(max_lines) if max_lines else config.log_max_lines
        days = float(max_age_days) if max_age_days else config.log_max_age_days
        result = logs.trim_log(paths.log_path(), lines, days)
        if result.get("changed"):
            logs.prune_rotated(paths.log_path(), keep=1)
        return result

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
        """Register the background tasks and start them.

        When the window is not running as administrator, an elevated
        helper is started that runs the ``install`` command and exits.
        The window stays open and waits for it.
        """
        if not _is_admin():
            return _run_elevated_command("install")

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
        if not _is_admin():
            return _run_elevated_command("start")

        from ..core import install

        ok, detail = install.start_daemon()
        return {"ok": ok, "message": detail}

    def stop_service(self) -> dict[str, Any]:
        """Stop the background service."""
        if not _is_admin():
            return _run_elevated_command("stop")

        from ..core import install

        ok, detail = install.stop_daemon()
        return {"ok": ok, "message": detail}

    def uninstall_service(self) -> dict[str, Any]:
        """Remove the background tasks."""
        if not _is_admin():
            return _run_elevated_command("uninstall")

        from ..core import install

        ok, detail = install.uninstall_all()
        return {"ok": ok, "message": detail}

    # -- Microsoft telemetry ----------------------------------------------

    def telemetry_state(self) -> dict[str, Any]:
        """How much of the Microsoft telemetry is turned off."""
        from ..core import telemetry

        try:
            return telemetry.telemetry_status()
        except Exception as exc:  # noqa: BLE001 - the window has to say why
            log.exception("the telemetry state could not be read")
            return {"ok": False, "message": str(exc)}

    def set_telemetry_disabled(self, disabled: bool) -> dict[str, Any]:
        """Turn the Microsoft telemetry off, or put it back.

        Turning it off needs administrator rights: the registry keys
        are under HKLM, the services belong to the system, and the
        hosts file is in the Windows directory.  When the window is not
        elevated, an elevated helper does the work and the window waits
        for it - so the answer is known right away and the switch can
        show the new state instead of guessing.
        """
        from ..core import telemetry

        if not _is_admin():
            # The helper runs the same code; the marker tells it which
            # way to go.
            command = "telemetry-off" if disabled else "telemetry-on"
            result = _run_elevated_command(command, timeout_seconds=180)
            if not result.get("ok"):
                return result
        else:
            try:
                if disabled:
                    ok, message = telemetry.disable_telemetry()
                else:
                    ok, message = telemetry.enable_telemetry()
            except Exception as exc:  # noqa: BLE001
                log.exception("the telemetry could not be changed")
                return {"ok": False, "message": str(exc)}
            if not ok:
                return {"ok": False, "message": message}

        # Read the state back, so the window shows what the machine
        # really does rather than what was asked for.
        state = self.telemetry_state()
        return {
            "ok": bool(state.get("ok")),
            "message": "已关闭" if disabled else "已恢复",
            "telemetry": state.get("status", {}),
        }

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

    def restore_plan(self, guid: str) -> dict[str, Any]:
        """Put a built-in plan back the way Windows made it.

        Only the plans Windows ships with can be restored.  A plan the
        user made themselves has no default version, so the interface
        does not offer the button for those.
        """
        from ..core import planrestore

        if not planrestore.is_built_in(guid):
            return {
                "ok": False,
                "message": "这是你自己建的计划，没有可以恢复的默认值。",
            }

        reply = self._daemon_request({"kind": "restore_plan", "guid": guid})
        if reply is None:
            return {
                "ok": False,
                "message": "后台服务没有在运行，恢复不了。先在上面启动它。",
            }
        return {"ok": bool(reply.get("ok")), "message": reply.get("message", "")}

    def restorable_plans(self) -> dict[str, Any]:
        """The plans that can be restored to the Windows defaults."""
        from ..core import planrestore, winpower

        return {"ok": True, "plans": planrestore.restorable_plans(winpower.list_plans())}

    def gpu_state(self) -> dict[str, Any]:
        """What the program found out about the graphics card."""
        from ..core import gpu

        try:
            return gpu.gpu_state().to_dict()
        except Exception as exc:  # noqa: BLE001 - the interface has to know
            log.warning("the graphics card could not be read: %s", exc)
            return {"supported": False, "message": str(exc)}

    def set_gpu_preference(self, preference: str) -> dict[str, Any]:
        """Tell the driver which way to lean when the card is idle."""
        from ..core import gpu

        try:
            return gpu.set_gpu_preference(preference).to_dict()
        except Exception as exc:  # noqa: BLE001
            log.warning("the graphics preference could not be set: %s", exc)
            return {"supported": False, "message": str(exc)}

    def own_plans(self) -> dict[str, Any]:
        """The plans the program can make, ready for the interface."""
        from ..core import ownplans

        return {"ok": True, "plans": ownplans.own_plan_settings()}

    def check_update(self) -> dict[str, Any]:
        """Look for a newer version and put it in place when there is one.

        The work is done by a script in the temporary directory, because
        the program cannot replace itself while it is running.  The
        script waits for this program to close, replaces the file,
        points the tasks at the new file and starts the program again.
        """
        from ..cli import _download_and_install_update

        try:
            ok, message = _download_and_install_update()
        except Exception as exc:  # noqa: BLE001 - the interface has to know
            log.warning("the update check failed: %s", exc)
            return {"ok": False, "message": f"检查失败：{exc}"}
        return {"ok": ok, "message": message}

    def update_result(self) -> dict[str, Any]:
        """What the last update did, so the interface can tell the user."""
        from ..core import update

        result = update.read_result()
        if result:
            update.clear_result()
        return result

    def create_own_plan(self, kind: str) -> dict[str, Any]:
        """Make one of the program's own power plans."""
        reply = self._daemon_request({"kind": "create_plan", "plan_kind": kind})
        if reply is None:
            return {
                "ok": False,
                "message": "后台服务没有在运行，建不了计划。先在上面启动它。",
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
