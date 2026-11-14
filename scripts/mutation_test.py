#!/usr/bin/env python3
"""Mutation testing: break the code on purpose and check the tests notice.

A green test suite only means something if the tests would go red when
the behaviour they cover is broken.  This script takes each important
piece of logic, changes it in a way that looks plausible but is wrong,
runs the tests, and expects them to fail.  The original file is put
back in a ``finally`` block, so an interrupted run cannot leave the
repository with mutated code in it.

Usage::

    python scripts/mutation_test.py
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "winsleep"

#: Two runs at the same time write over each other's files.  Each one
#: mutates source, runs the tests and puts the original back, and the
#: second run's "original" is the first run's mutation.  The results
#: are then about code that never existed: a snippet that "cannot be
#: found", a mutation that "was not caught".  The lock makes the second
#: run wait its turn instead.
LOCK = ROOT / ".mutation-lock"

#: Where the original code is kept while it is broken.  A run that is
#: killed cannot put the code back itself, so the next run finds the
#: original here and puts it back.
JOURNAL = ROOT / ".mutation-journal.json"


def take_lock() -> bool:
    """Take the lock, or say that another run has it."""
    import os
    import time

    try:
        handle = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # A run that was killed leaves the lock behind.  The process
        # that wrote it is asked whether it is still there.
        try:
            pid = int(LOCK.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = 0
        if pid and _process_alive(pid):
            print(f"another mutation run is using the source tree (pid {pid}).")
            print("wait for it to finish, or delete .mutation-lock if it is gone.")
            return False
        try:
            LOCK.unlink()
        except OSError:
            return False
        return take_lock()
    except OSError as exc:
        print(f"the lock could not be taken: {exc}")
        return False

    try:
        os.write(handle, str(os.getpid()).encode("ascii"))
        os.close(handle)
    except OSError:
        pass
    # A lock that is an hour old belongs to nobody.
    try:
        os.utime(LOCK, (time.time(), time.time()))
    except OSError:
        pass
    return True


def drop_lock() -> None:
    try:
        LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def _process_alive(pid: int) -> bool:
    """True when a process with that number is running."""
    try:
        import psutil

        return psutil.pid_exists(pid)
    except ImportError:
        pass
    try:
        import os

        os.kill(pid, 0)
    except (OSError, ValueError, AttributeError):
        return False
    return True


def read_journal() -> dict[str, str]:
    """The original code of the files that were changed."""
    try:
        data = json.loads(JOURNAL.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def write_journal(data: dict[str, str]) -> None:
    try:
        JOURNAL.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def forget_bytecode(path: Path) -> None:
    """Throw away the compiled copy of a file.

    Python decides whether a ``.pyc`` is still good by comparing the
    file's modification time - to the second - and its size.  A
    mutation that swaps ``==`` for ``!=`` changes neither, so the
    compiled copy of the broken code stays "valid" and every test after
    the mutation runs against code that is not on disk any more.

    That makes the whole run lie: the tests look green while being run
    against code that no longer exists.  Deleting the compiled copy is
    the only reliable way out.
    """
    cache = path.parent / "__pycache__"
    if not cache.is_dir():
        return
    stem = path.stem
    for compiled in cache.glob(f"{stem}.*.pyc"):
        try:
            compiled.unlink()
        except OSError:
            pass


def put_back() -> None:
    """Put the original code back.

    This runs when the script is finished, and also when it is asked to
    stop.  A run that is killed hard - the machine goes down - cannot
    do it, which is why the original code is written down first.
    """
    for name, original in read_journal().items():
        path = ROOT / name
        try:
            if path.read_text(encoding="utf-8") != original:
                path.write_text(original, encoding="utf-8")
                print(f"put the original code back into {name}")
        except OSError:
            continue
    try:
        JOURNAL.unlink(missing_ok=True)
    except OSError:
        pass


def on_signal(signum, _frame):
    """Put the code back when the script is stopped."""
    put_back()
    drop_lock()
    sys.exit(128 + signum)

#: Every mutation is (name, file, original snippet, broken snippet).
#: The snippet has to appear exactly once in the file.
MUTATIONS: list[tuple[str, Path, str, str]] = [
    (
        "a service handle is passed without its signature (64-bit overflow)",
        SRC / "core" / "telemetry.py",
        """    advapi32.CloseServiceHandle.restype = wintypes.BOOL
    advapi32.CloseServiceHandle.argtypes = [wintypes.HANDLE]""",
        """    advapi32.CloseServiceHandle.restype = wintypes.BOOL""",
    ),
    (
        "closing a service handle raises and wipes out the answer",
        SRC / "core" / "telemetry.py",
        """    try:
        advapi32.CloseServiceHandle(handle)
    except Exception:  # noqa: BLE001 - closing must stay silent
        pass""",
        """    advapi32.CloseServiceHandle(handle)""",
    ),
    (
        "the tray icon is never put up at all",
        SRC / "core" / "tray.py",
        """    try:
        icon.run_detached()""",
        """    try:
        pass  # mutation: the icon is never added""",
    ),
    (
        "closing the window ends the program instead of hiding it",
        SRC / "gui" / "app.py",
        """        return False

    return _hide_instead_of_closing""",
        """        return True

    return _hide_instead_of_closing""",
    ),
    (
        "the daemon trusts the plan it set last time instead of reading "
        "the real one",
        SRC / "core" / "daemon.py",
        """        actual = winpower.active_plan()
        actual_guid = actual[1] if actual else ""
        expected_guid = winpower.plan_guid_of(expected) or ""

        if actual_guid and expected_guid and actual_guid == expected_guid:""",
        """        actual_guid = ""
        expected_guid = ""

        if True:""",
    ),
    (
        "the log is trimmed but the removed lines are kept",
        SRC / "core" / "logs.py",
        """    after = len(kept)
    if after == before:""",
        """    after = before
    if True:""",
    ),
    (
        "the log is never trimmed",
        SRC / "core" / "logs.py",
        """    if max_lines > 0 and len(kept) > max_lines:
        kept = kept[-max_lines:]""",
        """    if False:
        kept = kept[-max_lines:]""",
    ),
    (
        "the log tailer gives the same lines again",
        SRC / "core" / "logs.py",
        """                text = handle.read()
                self._offset = handle.tell()""",
        """                text = handle.read()
                self._offset = self._offset""",
    ),
    (
        "the log is not cleared",
        SRC / "core" / "logs.py",
        """    before = len(read_log(path))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)""",
        """    before = 0
    try:
        path.parent.mkdir(parents=True, exist_ok=True)""",
    ),
    (
        "the elevation passes the executable twice",
        SRC / "core" / "elevate.py",
        """    parts = [_quote(program)]
    parts.extend(_quote(part) for part in source[1:])""",
        """    parts = [_quote(program)]
    parts.extend(_quote(part) for part in source)""",
    ),
    (
        "the elevation asks for administrator rights forever",
        SRC / "core" / "elevate.py",
        """    if ELEVATION_MARKER in source:
        return source""",
        """    if False:
        return source""",
    ),
    (
        "a plan the user made is restored as well",
        SRC / "core" / "planrestore.py",
        """    return guid.strip().lower() in BUILT_IN_PLANS""",
        """    return True""",
    ),
    (
        "the single instance lock is given to everybody",
        SRC / "core" / "single.py",
        """        except FileExistsError:
            if not self._stale():
                return""",
        """        except FileExistsError:
            if False:
                return""",
    ),
    (
        "a lock whose owner died is never seen as stale",
        SRC / "core" / "single.py",
        """        if self.owner():
            return not self.owner_alive()""",
        """        if self.owner():
            return False""",
    ),
    (
        "a reused pid is taken for the owner of the lock",
        SRC / "core" / "single.py",
        "        return abs(actual - recorded) <= _START_TOLERANCE",
        "        return True",
    ),
    (
        "the daemon deletes a live window's lock",
        SRC / "core" / "single.py",
        """    if instance.owner_alive():
        return False""",
        """    if False:
        return False""",
    ),
    (
        "a second copy gives up instead of asking the running window",
        SRC / "gui" / "app.py",
        '''    if windowlink.request_show():
        log.info("the running window was asked to come forward")
        return "shown"''',
        '''    if False:
        log.info("the running window was asked to come forward")
        return "shown"''',
    ),
    (
        "the icon is never looked for once it is up",
        SRC / "core" / "tray.py",
        "            if there is False:",
        "            if False:",
    ),
    (
        "the daemon does not count a failed switch",
        SRC / "core" / "daemon.py",
        "        self._plan_failures += 1\n",
        "        self._plan_failures += 0\n",
    ),
    (
        "the daemon keeps the conflict warning after it recovers",
        SRC / "core" / "daemon.py",
        """            # The warning was about the plan not being restorable; now
            # it is, so the warning has to go as well.
            self._conflict_warning = ""
""",
        "",
    ),
    (
        "the daemon decides on a stale idle time",
        SRC / "core" / "daemon.py",
        """        idle_seconds = self._server.agent.idle_seconds
        if not self._server.agent.is_fresh():""",
        """        idle_seconds = self._server.agent.idle_seconds
        if False:""",
    ),
    (
        "a duration is read as seconds whatever unit it is written in",
        SRC / "core" / "rules.py",
        """    factor = _DURATION_UNITS.get(unit_text)
    if factor is None:
        return None
    try:
        return float(number_text) * factor""",
        """    factor = _DURATION_UNITS.get(unit_text)
    if factor is None:
        return None
    try:
        return float(number_text)""",
    ),
    (
        "the Chinese units are not understood",
        SRC / "core" / "rules.py",
        """    for suffix, factor in (
        ("毫秒", 0.001),
        ("秒", 1),
        ("分钟", 60),
        ("分", 60),
        ("小时", 3600),
        ("时", 3600),
        ("天", 86400),
    ):""",
        """    for suffix, factor in ():""",
    ),
    (
        "a CPU threshold is read as a duration",
        SRC / "core" / "rules.py",
        """    if kind == "cpu_below":
        threshold = parse_number(value)""",
        """    if kind == "cpu_below":
        threshold = parse_duration(value)""",
    ),
    (
        "the daemon writes the plan settings to the wrong plan",
        SRC / "core" / "daemon.py",
        """        ok, detail = powersettings.apply_settings(guid, list(changes))""",
        """        ok, detail = powersettings.apply_settings("", list(changes))""",
    ),
    (
        "the daemon writes no plan settings at all",
        SRC / "core" / "daemon.py",
        """        ok, detail = powersettings.apply_settings(guid, list(changes))""",
        """        ok, detail = True, "ok" """,
    ),
    (
        "the daemon copies a plan without a name",
        SRC / "core" / "daemon.py",
        """        if not name:
            return json.dumps({"ok": False, "message": "没有给新计划起名字"}).encode("utf-8")""",
        """        if False:
            return json.dumps({"ok": False, "message": "没有给新计划起名字"}).encode("utf-8")""",
    ),
    (
        "the process list is not filtered",
        SRC / "gui" / "bridge.py",
        """                if needle and needle not in name.lower():
                    continue""",
        """                if False:
                    continue""",
    ),
    (
        "a busy CPU no longer keeps the machine on the fast plan",
        SRC / "core" / "decide.py",
        "    if snap.cpu_percent >= idle_cpu_percent:",
        "    if False:",
    ),
    (
        "a running game no longer wins over the idle time",
        SRC / "core" / "decide.py",
        "    if game_running:",
        "    if False:",
    ),
    (
        "the idle threshold is exclusive instead of inclusive",
        SRC / "core" / "decide.py",
        "    if idle_for < idle_after_seconds:",
        "    if idle_for <= idle_after_seconds:",
    ),
    (
        "a zero CPU threshold is read as the default",
        SRC / "core" / "config.py",
        'idle_cpu_percent=float(_number(raw, "idle_cpu_percent", 15.0)),',
        'idle_cpu_percent=float(raw.get("idle_cpu_percent", 15.0) or 15.0),',
    ),
    (
        "server mode locks the screen again after it was locked",
        SRC / "core" / "servermode.py",
        '''    # Phase.LOCKED
    if idle_seconds >= threshold_seconds:
        # Still nobody there - hold at zero, do not lock again.
        return Decision(kind="hold_at_zero")''',
        '''    # Phase.LOCKED
    if idle_seconds >= threshold_seconds:
        # Still nobody there - hold at zero, do not lock again.
        return Decision(kind="lock_now")''',
    ),
    (
        "the process name keeps its .exe suffix when it is matched",
        SRC / "core" / "rules.py",
        """        wanted = name.strip().lower()
        if wanted.endswith(".exe"):
            wanted = wanted[:-4]""",
        """        wanted = name.strip().lower()""",
    ),
    (
        "the rules are not applied at all",
        SRC / "core" / "daemon.py",
        "        return self._apply_rules(config, snap, intent)",
        "        return intent",
    ),
    (
        "a lower priority rule wins over a higher priority one",
        SRC / "core" / "daemon.py",
        '''                if not plan_is_set:
                    plan = action.value.strip()
                    plan_is_set = True
                    reason = f"rule: {rule.name}"''',
        '''                plan = action.value.strip()
                reason = f"rule: {rule.name}"''',
    ),
    (
        "the same decision is written to the log again and again",
        SRC / "core" / "daemon.py",
        "        if not self._same_decision(intent, self._last_intent):",
        "        if intent != self._last_intent:",
    ),
    (
        "the daemon writes a line about every trim it does",
        SRC / "core" / "daemon.py",
        """                logs.prune_rotated(paths.log_path(), keep=1)
                # Nothing is written about the trim here.""",
        """                logs.prune_rotated(paths.log_path(), keep=1)
                log.info("the log was trimmed: %d lines removed", result.get("removed", 0))
                # Nothing is written about the trim here.""",
    ),
    (
        "the lock countdown makes every tick look like a change",
        SRC / "core" / "daemon.py",
        "            and one.lock_screen == other.lock_screen\n"
        "            and one.phase == other.phase",
        "            and one.lock_screen == other.lock_screen\n"
        "            and one.lock_in == other.lock_in\n"
        "            and one.phase == other.phase",
    ),
    # --- the copy of the program that the tasks point at -------------
    (
        "an older copy of the program is never replaced",
        SRC / "core" / "bootstrap.py",
        """    theirs = fingerprint(installed_exe()) if installed_exe().exists() else ""
    if theirs == mine:""",
        """    theirs = fingerprint(installed_exe()) if installed_exe().exists() else ""
    if theirs or True:""",
    ),
    (
        "the copy is made again even when it is already the same",
        SRC / "core" / "bootstrap.py",
        '    if theirs == mine:\n        # The file is there and it is the same one.',
        '    if False:\n        # The file is there and it is the same one.',
    ),
    (
        "a copy that was cut short is taken for a good one",
        SRC / "core" / "bootstrap.py",
        "    if written != mine:\n"
        '        return False, "the copy does not match the program"',
        "    if False:\n"
        '        return False, "the copy does not match the program"',
    ),
    # --- the tasks that keep the background half alive ---------------
    (
        "a task is not started again when it stops",
        SRC / "core" / "tasks.py",
        '        f"    <RestartOnFailure>\\n"\n'
        '        f"      <Interval>{RESTART_INTERVAL}</Interval>\\n"\n'
        '        f"      <Count>{RESTART_COUNT}</Count>\\n"\n'
        '        f"    </RestartOnFailure>\\n"\n',
        "",
    ),
    (
        "a task is killed after the default three days",
        SRC / "core" / "tasks.py",
        "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
        "<ExecutionTimeLimit>PT72H</ExecutionTimeLimit>",
    ),
    (
        "a task stops when the laptop goes onto the battery",
        SRC / "core" / "tasks.py",
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
        "<StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>",
    ),
    (
        "the daemon is registered as a task of the logged on user",
        SRC / "core" / "tasks.py",
        """    if name == DAEMON_TASK:
        return KIND_SYSTEM
    return KIND_INTERACTIVE""",
        """    if name == "never":
        return KIND_SYSTEM
    return KIND_INTERACTIVE""",
    ),
    (
        "the tasks are registered but never started",
        SRC / "core" / "install.py",
        """    started_ok, started_detail = start_all()
    messages.append(started_detail if started_ok else f"没能启动：{started_detail}")""",
        """    started_ok, started_detail = True, "not started"
    messages.append(started_detail)""",
    ),
    # --- reading the telemetry state without a language ---------------
    (
        "a task that is switched off is read as if it were running",
        SRC / "core" / "telemetry.py",
        '    return enabled.text.strip().lower() == "false"',
        '    return enabled.text.strip().lower() != "false"',
    ),
    # --- the timed override, the reason line, the tray ------------------
    (
        "the timed override is ignored",
        SRC / "core" / "decide.py",
        """    remaining = override_until - snap.now.timestamp()
    if remaining <= 0:
        return intent""",
        """    remaining = override_until - snap.now.timestamp()
    if True:
        return intent""",
    ),
    (
        "the timed override also picks the plan",
        SRC / "core" / "decide.py",
        """    return dataclasses.replace(
        intent,
        keep_awake=True,
        reason=f"timed override, {minutes} min left",
    )""",
        """    return dataclasses.replace(
        intent,
        plan=active_plan or None,
        keep_awake=True,
        reason=f"timed override, {minutes} min left",
    )""",
    ),
    (
        "a deadline in the past still counts as an override",
        SRC / "core" / "decide.py",
        """    remaining = override_until - snap.now.timestamp()
    if remaining <= 0:
        return intent""",
        """    remaining = override_until - snap.now.timestamp()
    if remaining < -86400:
        return intent""",
    ),
    (
        "the override deadline is not written to the config",
        SRC / "core" / "config.py",
        '            "keep_awake_until": self.keep_awake_until,',
        '            "keep_awake_until": 0.0,',
    ),
    (
        "the override deadline is not read back from the config",
        SRC / "core" / "config.py",
        '            keep_awake_until=max(0.0, float(_number(raw, "keep_awake_until", 0.0))),',
        '            keep_awake_until=0.0,',
    ),
    (
        "cancelling the override leaves the deadline in place",
        SRC / "gui" / "bridge.py",
        """        if wanted <= 0:
            config.keep_awake_until = 0.0
            self._save(config)""",
        """        if wanted <= 0:
            config.keep_awake_until = config.keep_awake_until
            self._save(config)""",
    ),
    (
        "the window cannot cancel an override",
        SRC / "gui" / "bridge.py",
        """        if wanted <= 0:
            config.keep_awake_until = 0.0""",
        """        if wanted <= 0:
            config.keep_awake_until = time.time()""",
    ),
    (
        "the daemon never says what it decided",
        SRC / "core" / "daemon.py",
        """        intent = self._current_intent
        if intent is None:
            return {"ok": False, "message": "not decided yet"}""",
        """        intent = None
        if intent is None:
            return {"ok": False, "message": "not decided yet"}""",
    ),
    (
        "the daemon reports its first decision forever",
        SRC / "core" / "daemon.py",
        """        intent = self._decide(config)
        self._current_intent = intent""",
        """        intent = self._decide(config)
        self._current_intent = self._current_intent or intent""",
    ),
    (
        "the status message is not answered",
        SRC / "core" / "daemon.py",
        """        if kind == "status":
            return self._status()""",
        """        if kind == "status!":
            return self._status()""",
    ),
    (
        "the tray has no way to hold the machine awake",
        SRC / "core" / "tray.py",
        '        items.append(pystray.MenuItem("保持唤醒", pystray.Menu(*entries)))',
        "        pass",
    ),
    (
        "the tray's cancel entry sets half an hour instead",
        SRC / "core" / "tray.py",
        """entries.append(pystray.MenuItem("取消，让它照常睡", _callback(on_override, 0)))""",
        """entries.append(pystray.MenuItem("取消，让它照常睡", _callback(on_override, 30)))""",
    ),
    (
        "a menu action carries its argument in the signature again",
        SRC / "core" / "tray.py",
        """    def _act(_icon: Any = None, _item: Any = None) -> None:
        handler(argument)""",
        """    def _act(_icon=None, _item=None, extra=argument) -> None:
        handler(argument)""",
    ),
    (
        "the selftest does not notice a missing interface file",
        SRC / "cli.py",
        """    absent = [
        name for name in interface_files
        if not (assets / name).is_file() or (assets / name).stat().st_size == 0
    ]""",
        """    absent = []""",
    ),
    (
        "an empty interface file is treated as present",
        SRC / "cli.py",
        """        if not (assets / name).is_file() or (assets / name).stat().st_size == 0""",
        """        if not (assets / name).is_file()""",
    ),
    (
        "the hold command ignores the number",
        SRC / "cli.py",
        """    config.keep_awake_until = 0.0 if minutes <= 0 else time.time() + minutes * 60
    config_mod.save(config, paths.config_path())

    if minutes <= 0:
        print("已取消，机器会照常睡")
        return 0""",
        """    config.keep_awake_until = time.time() + 60
    config_mod.save(config, paths.config_path())

    if minutes <= 0:
        print("已取消，机器会照常睡")
        return 0""",
    ),
]


def run_tests() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is not None:
            try:
                signal.signal(signum, on_signal)
            except (OSError, ValueError):
                pass

    if not take_lock():
        return 3

    failures: list[str] = []
    survived: list[str] = []

    # A run that was killed before could put the code back.  The
    # original is written down first, so it can be put back now.
    leftovers = read_journal()
    if leftovers:
        print("a previous run was killed before it could put the code back.")
        put_back()

    journal: dict[str, str] = {}

    for name, path, original, broken in MUTATIONS:
        source = path.read_text(encoding="utf-8")
        found = source.count(original)
        if found != 1:
            if found == 0:
                failures.append(f"{name}: the snippet was not found in {path.name}")
            else:
                failures.append(
                    f"{name}: the snippet appears {found} times in {path.name}"
                )
            continue

        key = str(path.relative_to(ROOT))
        journal.setdefault(key, source)
        write_journal(journal)

        try:
            path.write_text(source.replace(original, broken), encoding="utf-8")
            forget_bytecode(path)
            result = run_tests()
        finally:
            # The original has to go back even when the tests were
            # killed half way through.
            path.write_text(source, encoding="utf-8")
            forget_bytecode(path)

        if result.returncode == 0:
            survived.append(name)
            print(f"NOT CAUGHT  {name}")
        else:
            print(f"caught      {name}")

    # Everything is back in place.
    try:
        JOURNAL.unlink(missing_ok=True)
    except OSError:
        pass
    drop_lock()

    print()
    print(f"{len(MUTATIONS) - len(survived)}/{len(MUTATIONS)} mutations caught")

    if survived:
        print()
        print("the tests did not notice these:")
        for name in survived:
            print(f"  - {name}")
    if failures:
        print()
        print("the script itself is broken:")
        for line in failures:
            print(f"  - {line}")

    return 1 if (survived or failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
