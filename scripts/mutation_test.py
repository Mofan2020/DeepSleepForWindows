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

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "winsleep"

#: Every mutation is (name, file, original snippet, broken snippet).
#: The snippet has to appear exactly once in the file.
MUTATIONS: list[tuple[str, Path, str, str]] = [
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
    failures: list[str] = []
    survived: list[str] = []

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

        try:
            path.write_text(source.replace(original, broken), encoding="utf-8")
            result = run_tests()
        finally:
            # The original has to go back even when the tests were
            # killed half way through.
            path.write_text(source, encoding="utf-8")

        if result.returncode == 0:
            survived.append(name)
            print(f"NOT CAUGHT  {name}")
        else:
            print(f"caught      {name}")

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
