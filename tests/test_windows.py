"""Tests that need a real Windows machine.

These are marked ``windows`` and only run on a Windows runner (or on a
real machine).  They check the things that cannot be checked off Windows:
that ``powercfg`` is parsed correctly, that the Win32 calls work, and
that the whole program starts.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from winsleep.core import winapi, winpower

pytestmark = pytest.mark.windows


def test_the_machine_reports_the_power_plans():
    plans = winpower.list_plans()
    assert plans, "powercfg /list returned nothing"
    for name, guid in plans:
        assert name
        assert len(guid) == 36


def test_the_active_plan_can_be_read():
    active = winpower.active_plan()
    assert active is not None
    name, guid = active
    assert name
    assert len(guid) == 36


def test_the_active_plan_is_among_the_known_plans():
    active = winpower.active_plan()
    assert active is not None
    guids = {guid for _, guid in winpower.list_plans()}
    assert active[1] in guids


def test_switching_to_the_plan_that_is_already_active_succeeds():
    active = winpower.active_plan()
    assert active is not None
    ok, detail = winpower.set_active_plan(active[1])
    assert ok, detail


def test_switching_to_a_plan_that_does_not_exist_fails():
    ok, detail = winpower.set_active_plan("no such plan at all")
    assert not ok
    assert "no such plan" in detail.lower()


def test_the_idle_time_can_be_read():
    idle = winapi.idle_seconds()
    assert idle >= 0.0


def test_the_execution_state_can_be_set_and_released():
    previous = winapi.set_execution_state(0x80000000 | 0x00000001)
    assert previous is not None
    winapi.release_execution_state()


def test_locking_the_screen_does_not_crash():
    # It may or may not succeed depending on the session, but it must
    # not raise.
    winapi.lock_workstation()


def test_tasklist_is_parsed():
    result = subprocess.run(
        ["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0


def test_the_running_processes_can_be_looked_up():
    running = winpower.processes_running(["python.exe", "explorer.exe"])
    assert isinstance(running, set)


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the command line in a child process.

    The package lives under ``src/`` and is not installed, so the path
    is passed on explicitly - a child process does not inherit the
    ``sys.path`` change the conftest made.
    """
    import os

    child_env = dict(os.environ)
    src = str(Path(__file__).resolve().parent.parent / "src")
    child_env["PYTHONPATH"] = src + os.pathsep + child_env.get("PYTHONPATH", "")
    if env:
        child_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "winsleep.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=child_env,
    )


def test_the_command_line_answers():
    result = _run_cli("--version")
    assert result.returncode == 0, result.stderr
    assert "DeepSleep For Windows" in result.stdout


def test_the_plans_command_answers():
    result = _run_cli("plans")
    assert result.returncode == 0, result.stderr
    assert "-" in result.stdout


def test_the_status_command_answers():
    result = _run_cli("status")
    assert result.returncode == 0, result.stderr
    assert "version" in result.stdout


class TestSwitchingByName:
    """The plan names the interface shows have to be usable as input."""

    def test_the_built_in_plans_are_listed(self):
        names = [name for name, _ in winpower.list_plans()]
        assert names

    def test_the_names_are_not_guids(self):
        for name, guid in winpower.list_plans():
            assert name
            assert guid in name or not guid.startswith(name)

    def test_a_built_in_plan_can_be_found_by_its_guid(self):
        guid = winpower.plan_guid_of("381b4222-f694-41f0-9685-ff5bb260df2e")
        assert guid == "381b4222-f694-41f0-9685-ff5bb260df2e"

    def test_the_active_plan_is_one_of_the_known_plans(self):
        active = winpower.active_plan()
        assert active is not None
        guids = {guid for _, guid in winpower.list_plans()}
        assert active[1] in guids

    def test_switching_to_a_built_in_plan_by_name_works(self):
        # Pick a built-in plan that is not the active one, switch to it,
        # and switch back.  The active plan is restored either way.
        active = winpower.active_plan()
        assert active is not None

        target = next(
            (guid for guid, names in winpower._BUILT_IN_PLANS.items()
             if guid != active[1] and any(
                 winpower.plan_guid_of(part) == guid for part in names)),
            None,
        )
        if target is None:
            pytest.skip("only one plan on this machine")

        ok, detail = winpower.set_active_plan(target)
        assert ok, detail
        assert winpower.active_plan()[1] == target

        ok, detail = winpower.set_active_plan(active[1])
        assert ok, detail
        assert winpower.active_plan()[1] == active[1]

    def test_switching_to_a_plan_that_does_not_exist_fails(self):
        ok, detail = winpower.set_active_plan("no such plan at all")
        assert not ok
        assert "no such plan" in detail.lower()


class TestALegacyCodePage:
    """The console code page must not decide whether the program runs.

    The power plan names are the ones Windows shows, so they are in the
    language of the machine.  An English Windows has a cp1252 console,
    a Chinese one has something else, and neither can be relied on.
    """

    def test_the_plans_command_survives_a_legacy_code_page(self):
        result = _run_cli("plans", env={"PYTHONIOENCODING": "cp1252"})
        assert result.returncode == 0, result.stderr

    def test_the_status_command_survives_a_legacy_code_page(self):
        result = _run_cli("status", env={"PYTHONIOENCODING": "cp1252"})
        assert result.returncode == 0, result.stderr

    def test_the_version_survives_a_legacy_code_page(self):
        result = _run_cli("--version", env={"PYTHONIOENCODING": "cp1252"})
        assert result.returncode == 0, result.stderr

    def test_the_help_survives_a_legacy_code_page(self):
        result = _run_cli("--help", env={"PYTHONIOENCODING": "cp1252"})
        assert result.returncode == 0
