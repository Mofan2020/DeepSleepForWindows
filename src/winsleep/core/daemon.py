"""The daemon: the background half of DeepSleep For Windows.

It runs in session 0 (the service session), owns the power plan, holds
the machine awake, evaluates the rules and locks the screen.  It does not
read the idle time itself - it gets that from the agent, which runs in
the user's session where the keyboard and mouse actually are.

The two halves talk over a loopback TCP socket with newline-delimited
JSON.  A port is chosen when the daemon starts and written into the state
file so the agent can find it.

The loop is deliberately simple: read the latest state, decide, act,
sleep.  Everything that can fail is caught and logged - the daemon must
not die because one ``powercfg`` call went wrong.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from . import config as config_mod
from . import paths, winapi, winpower
from .decide import Intent, decide
from .rules import Rule, Snapshot, evaluate
from .servermode import Phase

log = logging.getLogger("winsleep.daemon")

#: How often the daemon wakes up and re-evaluates, in seconds.
TICK_SECONDS = 5.0

#: How many times in a row the power plan could not be restored before
#: the user is told that something else is fighting over it.
PLAN_FAILURE_LIMIT = 3

#: How often the daemon trims the log file.  The file is written by
#: three processes, and rewriting it on every tick would be a lot of
#: work for nothing.
LOG_TRIM_INTERVAL_SECONDS = 60.0

#: How often the daemon refreshes the "keep awake" execution state.
#: Windows drops the state if it is not refreshed, so this has to be
#: more frequent than the tick.
KEEP_AWAKE_REFRESH_SECONDS = 10.0


@dataclasses.dataclass
class AgentState:
    """What the agent last reported."""

    idle_seconds: float = -1.0
    updated_at: float = 0.0

    #: Set when the daemon wants the screen locked.  The agent picks it
    #: up on its next report and clears it.
    lock_requested: bool = False

    def is_fresh(self, max_age: float = 60.0) -> bool:
        if self.updated_at <= 0:
            return False
        return (time.time() - self.updated_at) <= max_age


class StateServer:
    """A tiny line-delimited JSON server the agent talks to.

    The agent is the client: it connects, sends a line, and the daemon
    answers.  Only one agent is expected at a time, but the server
    handles several connections anyway.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.agent = AgentState()
        #: Called with the new agent state whenever the agent reports.
        self.on_agent_update: Callable[[AgentState], None] | None = None
        #: Returns what the loop decided on its last tick.  The server
        #: owns the socket, the loop owns the decision - the window asks
        #: the server, which asks this.
        self.on_status: Callable[[], dict[str, Any]] | None = None

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1] if self._sock else 0

    def start(self) -> int:
        """Bind, listen and start serving.  Returns the port."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        self._sock.listen(8)
        self._port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, name="winsleep-state", daemon=True)
        self._thread.start()
        return self._port

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _serve(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                conn, _ = sock.accept()
            except OSError:
                if self._stop.is_set():
                    return
                continue
            try:
                self._handle(conn)
            except Exception:  # noqa: BLE001 - a broken agent must not kill the daemon
                log.exception("the agent connection failed")

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(30.0)
            buffer = b""
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, _, buffer = buffer.partition(b"\n")
                    if not line.strip():
                        continue
                    response = self._dispatch(line)
                    try:
                        conn.sendall(response + b"\n")
                    except OSError:
                        return

    def _status(self) -> bytes:
        """Ask whoever owns the decision what it decided."""
        if self.on_status is None:
            return json.dumps({"ok": False, "message": "not decided yet"}).encode("utf-8")
        try:
            return json.dumps(self.on_status()).encode("utf-8")
        except Exception:  # noqa: BLE001 - the window must still get an answer
            log.exception("the status could not be built")
            return json.dumps({"ok": False, "message": "status failed"}).encode("utf-8")

    def _dispatch(self, line: bytes) -> bytes:
        try:
            message = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return b'{"ok":false,"error":"bad json"}'

        kind = message.get("kind")
        if kind == "ping":
            return json.dumps({"ok": True, "kind": "pong"}).encode("utf-8")
        if kind == "status":
            return self._status()
        if kind == "set_plan_settings":
            return self._set_plan_settings(message)
        if kind == "duplicate_plan":
            return self._duplicate_plan(message)
        if kind == "delete_plan":
            return self._delete_plan(message)
        if kind == "restore_plan":
            return self._restore_plan(message)
        if kind == "create_plan":
            return self._create_plan(message)
        if kind == "report":
            try:
                idle = float(message.get("idle_seconds", -1.0))
            except (TypeError, ValueError):
                idle = -1.0
            self.agent.idle_seconds = idle
            self.agent.updated_at = time.time()
            if self.on_agent_update:
                try:
                    self.on_agent_update(self.agent)
                except Exception:  # noqa: BLE001
                    log.exception("the agent update callback failed")
            # A pending lock request travels back with the reply, so the
            # agent learns about it without a second round trip.
            reply = {"ok": True}
            if self.agent.lock_requested:
                reply["lock"] = True
                self.agent.lock_requested = False
            return json.dumps(reply).encode("utf-8")

        return b'{"ok":false,"error":"unknown message"}'

    def request_lock(self) -> None:
        """Ask the agent to lock the screen.

        Locking has to happen inside the user's session: a process in
        session 0 cannot lock the interactive window station, and
        ``LockWorkStation`` there just fails.  So the request is queued
        and the agent picks it up the next time it reports.
        """
        self.agent.lock_requested = True

    # -- the contents of a power plan -------------------------------------

    def _set_plan_settings(self, message: dict[str, Any]) -> bytes:
        """Write the settings of a power plan.

        This runs in the daemon because the settings live under
        ``HKEY_LOCAL_MACHINE`` and only an administrator may write
        them.  The daemon runs as SYSTEM, so it can.
        """
        from . import powersettings

        guid = str(message.get("guid", ""))
        changes = message.get("changes", []) or []
        if not guid:
            return json.dumps({"ok": False, "message": "没有指定电源计划"}).encode("utf-8")
        ok, detail = powersettings.apply_settings(guid, list(changes))
        if ok:
            log.info("the settings of the plan %s were changed", guid)
        else:
            log.warning("the settings of the plan %s could not be changed: %s", guid, detail)
        return json.dumps({"ok": ok, "message": detail}, ensure_ascii=False).encode("utf-8")

    def _duplicate_plan(self, message: dict[str, Any]) -> bytes:
        """Copy a power plan."""
        from . import powersettings

        guid = str(message.get("guid", ""))
        name = str(message.get("name", "")).strip()
        if not guid:
            return json.dumps({"ok": False, "message": "没有指定电源计划"}).encode("utf-8")
        if not name:
            return json.dumps({"ok": False, "message": "没有给新计划起名字"}).encode("utf-8")
        ok, detail = powersettings.duplicate_plan(guid, name)
        if ok:
            log.info("the plan %s was copied to %s", guid, detail)
        return json.dumps(
            {"ok": ok, "message": detail, "guid": detail if ok else ""},
            ensure_ascii=False,
        ).encode("utf-8")

    def _restore_plan(self, message: dict[str, Any]) -> bytes:
        """Put a built-in plan back the way Windows made it.

        The settings live under ``HKEY_LOCAL_MACHINE``, so this has to
        happen in the daemon, which runs with administrator rights.
        """
        from . import planrestore

        guid = str(message.get("guid", "")).strip()
        if not guid:
            return json.dumps({"ok": False, "message": "没有指定电源计划"}, ensure_ascii=False).encode("utf-8")
        ok, detail = planrestore.restore_plan(guid)
        if ok:
            log.info("the plan %s was restored to the Windows defaults", guid)
        return json.dumps({"ok": ok, "message": detail}, ensure_ascii=False).encode("utf-8")

    def _create_plan(self, message: dict[str, Any]) -> bytes:
        """Create one of the program's own power plans.

        A built-in plan is copied and renamed, and then the settings
        that make it what it is are written into the copy.
        """
        from . import ownplans

        kind = str(message.get("plan_kind", "")).strip()
        ok, detail = ownplans.create_own_plan(kind)
        if ok:
            log.info("the own plan %s was created", kind)
        return json.dumps({"ok": ok, "message": detail, "guid": detail if ok else ""}, ensure_ascii=False).encode("utf-8")

    def _delete_plan(self, message: dict[str, Any]) -> bytes:
        """Delete a power plan."""
        from . import powersettings

        guid = str(message.get("guid", ""))
        if not guid:
            return json.dumps({"ok": False, "message": "没有指定电源计划"}).encode("utf-8")
        ok, detail = powersettings.delete_plan(guid)
        if ok:
            log.info("the plan %s was deleted", guid)
        return json.dumps({"ok": ok, "message": detail}, ensure_ascii=False).encode("utf-8")


class Daemon:
    """The background service."""

    def __init__(self, config_path=None) -> None:
        self._config_path = config_path or paths.config_path()
        self._server = StateServer()
        self._phase = Phase.OFF
        self._last_keep_awake = 0.0
        self._current_plan: str | None = None
        self._stop = threading.Event()
        self._last_intent: Intent | None = None

        #: What the loop decided on its most recent tick.  ``_last_intent``
        #: only changes when the *decision* changes, so it is not a good
        #: answer to "what is the program doing right now" - its reason
        #: can be half an hour old.  This one is rewritten every tick.
        self._current_intent: Intent | None = None
        self._current_intent_at: float = 0.0
        self._server.on_status = self._status
        #: How many times in a row restoring the power plan failed.
        self._plan_failures = 0
        #: Set when another program seems to hold the power plan.
        self._conflict_warning: str = ""
        #: When the log file was last trimmed.
        self._last_log_trim: float = 0.0

    # -- lifecycle -------------------------------------------------------

    def run_forever(self) -> None:
        # The lock file is only for the GUI to prevent multiple windows.
        # The daemon runs in session 0 and doesn't need it, so clean it up.
        try:
            from . import single
            lock = single.lock_path()
            if lock.exists():
                lock.unlink()
                log.info("removed stale window lock file")
        except Exception:  # noqa: BLE001 - lock file is best effort
            pass

        port = self._server.start()
        self._write_port(port)
        log.info("the daemon is listening on 127.0.0.1:%d", port)
        self._server.on_agent_update = self._on_agent_update

        # Ensure the agent is running in the user's session.
        # The scheduled task may not have started it yet, or it may have crashed.
        self._ensure_agent_running()

        try:
            while not self._stop.is_set():
                self._tick()
                self._stop.wait(TICK_SECONDS)
        finally:
            self._server.stop()
            winapi.release_execution_state()

    def _ensure_agent_running(self) -> None:
        """Start the agent in the active user session if it's not running.

        The agent is a scheduled task (ONLOGON + /IT) but may not be running
        if the user logged in before the task was created, or if it crashed.
        We start it by running the task, which runs in the interactive session.
        """
        try:
            from . import install
            # Check if agent is alive via heartbeat
            if not install.agent_running():
                log.info("agent not running; starting it via scheduled task")
                ok, detail = install.start_agent()
                if ok:
                    log.info("agent start requested: %s", detail)
                else:
                    log.warning("could not start agent: %s", detail)
        except Exception as exc:  # noqa: BLE001
            log.warning("agent auto-start check failed: %s", exc)

    def stop(self) -> None:
        self._stop.set()

    def _write_port(self, port: int) -> None:
        try:
            paths.app_dir().mkdir(parents=True, exist_ok=True)
            paths.socket_path().write_text(str(port), encoding="utf-8")
        except OSError as exc:
            log.warning("could not write the port file: %s", exc)

    def _on_agent_update(self, state: AgentState) -> None:
        # Nothing to do here beyond the bookkeeping the server already
        # did; the tick loop picks the new value up.
        log.debug("the agent reported %.0fs of idle time", state.idle_seconds)

    # -- the loop ---------------------------------------------------------

    def _status(self) -> dict[str, Any]:
        """What the loop is doing right now, for the window to show.

        ``Intent.reason`` has always been documented as "shown in the
        UI", but nothing ever sent it there - the window could say which
        plan is active and not why.  For a program whose whole job is
        making that decision, the why is the interesting half.
        """
        intent = self._current_intent
        if intent is None:
            return {"ok": False, "message": "not decided yet"}
        return {
            "ok": True,
            "plan": intent.plan or "",
            "keep_awake": intent.keep_awake,
            "lock_screen": intent.lock_screen,
            "lock_in": intent.lock_in,
            "reason": intent.reason,
            "at": self._current_intent_at,
            "agent_idle": self._server.agent.idle_seconds,
            "agent_fresh": self._server.agent.is_fresh(),
        }

    def _tick(self) -> None:
        config = config_mod.load(self._config_path)
        self._trim_log(config)
        intent = self._decide(config)
        self._current_intent = intent
        self._current_intent_at = time.time()
        if not self._same_decision(intent, self._last_intent):
            log.info(
                "the plan should be %s (%s)",
                intent.plan or "unchanged",
                intent.reason,
            )
            self._last_intent = intent
        self._apply(config, intent)

    @staticmethod
    def _same_decision(one: Intent | None, other: Intent | None) -> bool:
        """True when two intents ask for the same thing.

        Comparing the whole intent is not enough.  The reason carries a
        live number - "idle for 8704s" - that changes every single
        tick, so two intents that ask for exactly the same thing would
        never look the same.  The daemon would then write a line about
        the plan on every tick, which is what filled the log with the
        same sentence five hundred times.

        What matters is what the daemon does: which plan it puts in
        place, whether it holds the machine awake, whether it locks the
        screen, and where the server mode countdown stands.  The
        wording of the reason is only there for the log, and so is the
        number of seconds left on the lock - it ticks down every
        second and the daemon never acts on it.
        """
        if one is None or other is None:
            return one is other
        return (
            one.plan == other.plan
            and one.keep_awake == other.keep_awake
            and one.lock_screen == other.lock_screen
            and one.phase == other.phase
        )

    def _trim_log(self, config: config_mod.Config) -> None:
        """Keep the log file within the limits the user set.

        The daemon does this rather than the window, because the daemon
        is the one that keeps running.  A window that is closed for a
        week must not leave an unbounded log behind.

        The trim runs at most once a minute: the file is written by
        three processes, and rewriting it on every tick would be a lot
        of work for nothing.
        """
        if not config.log_max_lines and not config.log_max_age_days:
            return

        now = time.time()
        if now - self._last_log_trim < LOG_TRIM_INTERVAL_SECONDS:
            return
        self._last_log_trim = now

        try:
            from . import logs

            result = logs.trim_log(
                paths.log_path(), config.log_max_lines, config.log_max_age_days
            )
            if result.get("changed"):
                logs.prune_rotated(paths.log_path(), keep=1)
                # Nothing is written about the trim here.  A line
                # saying "the log was trimmed" is itself a line in the
                # log, so the file goes over the limit again right
                # away, and the next minute the whole file is written
                # once more to take that line away again.  The trim
                # would then run for ever, rewriting the file every
                # minute and leaving a trail of "trimmed" lines behind.
                #
                # The window tells the user when it trims the log on
                # purpose, which is the moment it is worth knowing.
        except OSError as exc:
            log.warning("the log could not be trimmed: %s", exc)

    def _decide(self, config: config_mod.Config) -> Intent:
        idle_seconds = self._server.agent.idle_seconds
        if not self._server.agent.is_fresh():
            # The agent is not reporting.  Without a trustworthy idle
            # time we must not decide the machine is idle, so we keep
            # the active plan and hold the machine awake if the user
            # asked for it.
            log.debug("the agent has not reported recently")
            # A timed override does not need the idle time to be
            # trustworthy - the user asked for it by hand, so honour it
            # even when the idle number is the thing that is missing.
            remaining = config.keep_awake_until - time.time()
            if remaining > 0:
                return Intent(
                    plan=None,
                    keep_awake=True,
                    reason=f"timed override, {int(remaining // 60)} min left",
                )
            return Intent(
                plan=None,
                keep_awake=config.keep_awake or config.server_mode,
                reason="waiting for the agent",
            )

        running = self._watched_processes(config)

        snap = Snapshot(
            now=_local_now(),
            idle_seconds=idle_seconds,
            cpu_percent=self._cpu_percent(),
            running_processes=frozenset(running),
        )

        game_running = bool(
            running & {n.strip().lower() for n in config.game_processes}
        )

        intent = decide(
            snap,
            idle_plan=config.idle_plan,
            active_plan=config.active_plan,
            idle_after_seconds=config.idle_seconds,
            idle_cpu_percent=config.idle_cpu_percent,
            game_running=game_running,
            keep_awake_enabled=config.keep_awake,
            server_mode=config.server_mode,
            server_lock_seconds=config.server_lock_seconds,
            phase=self._phase,
            override_until=config.keep_awake_until,
        )

        return self._apply_rules(config, snap, intent)

    def _watched_processes(self, config: config_mod.Config) -> set[str]:
        """The processes this program cares about that are running now.

        Both the game list and the ``process_running`` conditions of the
        rules are looked at, so the process list is only read once per
        tick no matter how many places ask for it.
        """
        wanted = {name.strip() for name in config.game_processes if name.strip()}
        for rule in config.rules:
            for condition in rule.conditions:
                if condition.kind == "process_running" and condition.value.strip():
                    wanted.add(condition.value.strip())
        if not wanted:
            return set()
        try:
            return winpower.processes_running(sorted(wanted))
        except OSError as exc:
            log.warning("could not list the processes: %s", exc)
            return set()

    def _apply_rules(
        self,
        config: config_mod.Config,
        snap: Snapshot,
        intent: Intent,
    ) -> Intent:
        """Let the user's own rules have the last word.

        A rule is something the user wrote down explicitly, so when one
        fires it wins over the built-in idle and game logic.  The rules
        are applied in priority order, and the last one that sets a
        power plan decides which plan it is.
        """
        if not config.rules:
            return intent

        fired = evaluate(config.rules, snap)
        if not fired:
            return intent

        plan = intent.plan
        keep_awake = intent.keep_awake
        lock_screen = intent.lock_screen
        reason = intent.reason

        # The rules come back in priority order, highest first, so the
        # first rule that has something to say wins.  A lower priority
        # rule must not undo what a higher priority one asked for.
        plan_is_set = False
        keep_awake_is_set = False

        for rule in fired:
            action = rule.action
            if action.kind == "set_power_plan" and action.value.strip():
                if not plan_is_set:
                    plan = action.value.strip()
                    plan_is_set = True
                    reason = f"rule: {rule.name}"
            elif action.kind == "keep_awake":
                if not keep_awake_is_set:
                    keep_awake = action.value.strip().lower() in (
                        "on",
                        "true",
                        "yes",
                        "1",
                    )
                    keep_awake_is_set = True
                    reason = f"rule: {rule.name}"
            elif action.kind == "lock_screen":
                lock_screen = True
                reason = f"rule: {rule.name}"
            elif action.kind == "run_command" and action.value.strip():
                self._run_command(rule, action.value)

        return dataclasses.replace(
            intent,
            plan=plan,
            keep_awake=keep_awake,
            lock_screen=lock_screen,
            reason=reason,
        )

    def _run_command(self, rule: Rule, command: str) -> None:
        """Run the command of a ``run_command`` rule.

        The command is started without a shell, so it cannot be used to
        chain other commands together.  It is the user's own config, but
        there is no reason to hand a shell to it either.
        """
        try:
            # CREATE_NO_WINDOW (0x08000000) prevents the black console window
            # from flashing when the daemon (running in session 0) spawns a process.
            creationflags = 0
            if sys.platform == "win32":
                creationflags = 0x08000000  # subprocess.CREATE_NO_WINDOW
            subprocess.Popen(  # noqa: S603 - no shell, the user wrote this
                command,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            log.info("rule %s started the command %s", rule.name, command)
        except OSError as exc:
            log.warning("rule %s could not start %s: %s", rule.name, command, exc)

    def _cpu_percent(self) -> float:
        try:
            import psutil  # noqa: PLC0415 - optional, checked at call time
        except ImportError:
            return 0.0
        try:
            return float(psutil.cpu_percent(interval=None))
        except Exception:  # noqa: BLE001
            return 0.0

    # -- acting -----------------------------------------------------------

    def _apply(self, config: config_mod.Config, intent: Intent) -> None:
        if intent.lock_screen:
            # The lock has to happen in the user's own session, so it is
            # asked of the agent rather than done here.
            self._server.request_lock()
            log.info("the agent was asked to lock the screen")

        self._apply_plan(intent)

        now = time.time()
        if intent.keep_awake and now - self._last_keep_awake >= KEEP_AWAKE_REFRESH_SECONDS:
            self._last_keep_awake = now
            winapi.keep_awake()
        elif not intent.keep_awake:
            winapi.release_execution_state()
            self._last_keep_awake = 0.0

        self._phase = intent.phase

    def _apply_plan(self, intent: Intent) -> None:
        """Make the active power plan the one that should be active.

        The actual plan is read back every time rather than remembered.
        Something else on the machine - another power tool, a driver,
        the user clicking in the Windows settings - can change the plan
        at any moment, and then the machine is not in the state it was
        supposed to be.  Deep Sleep on macOS does the same thing with
        ``pmset disablesleep``: it checks the real value and writes it
        back when it does not match.

        Three failures in a row means something else is fighting over
        the power plan, and that is worth telling the user about.
        """
        expected = intent.plan
        if not expected:
            return

        actual = winpower.active_plan()
        actual_guid = actual[1] if actual else ""
        expected_guid = winpower.plan_guid_of(expected) or ""

        if actual_guid and expected_guid and actual_guid == expected_guid:
            self._plan_failures = 0
            self._current_plan = expected
            return

        # The plan is not what it should be.  It may be that something
        # else changed it, or that we never got around to changing it.
        # Either way it has to be put back now.
        ok, detail = winpower.set_active_plan(expected)
        if ok:
            self._plan_failures = 0
            self._current_plan = expected
            # The warning was about the plan not being restorable; now
            # it is, so the warning has to go as well.
            self._conflict_warning = ""
            if actual_guid:
                log.info(
                    "the power plan was %s, not %s; changed it back",
                    actual[0],
                    expected,
                )
            else:
                log.info("switched to the power plan %s", expected)
            return

        self._plan_failures += 1
        log.warning(
            "could not switch to %s: %s (failure %d of %d)",
            expected,
            detail,
            self._plan_failures,
            PLAN_FAILURE_LIMIT,
        )
        if self._plan_failures >= PLAN_FAILURE_LIMIT:
            # Repeated failures mean another program is holding the power
            # plan, which is exactly what the macOS version warns about
            # when something else keeps resetting ``disablesleep``.
            self._conflict_warning = (
                "另一个程序在抢占电源计划，恢复连续失败。请检查是否有其他电源管理工具在运行。"
            )
            log.error(self._conflict_warning)


def _local_now():
    import datetime as dt

    return dt.datetime.now()


def main() -> int:
    """Entry point for the daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("winsleep").setLevel(logging.INFO)

    daemon = Daemon()
    try:
        daemon.run_forever()
    except KeyboardInterrupt:
        daemon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
