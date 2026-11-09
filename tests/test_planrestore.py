"""Putting a built-in power plan back the way Windows made it."""

from __future__ import annotations

import sys

from winsleep.core import planrestore

BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
SAVER = "a1841308-3541-4fab-bc81-f71556f20b4a"
CUSTOM = "11111111-2222-3333-4444-555555555555"


def test_a_built_in_plan_is_recognised() -> None:
    assert planrestore.is_built_in(BALANCED) is True
    assert planrestore.is_built_in(SAVER) is True


def test_the_guid_is_recognised_whatever_its_case() -> None:
    assert planrestore.is_built_in(BALANCED.upper()) is True
    assert planrestore.is_built_in(f"  {BALANCED}  ") is True


def test_a_plan_the_user_made_is_not_built_in() -> None:
    assert planrestore.is_built_in(CUSTOM) is False
    assert planrestore.is_built_in("") is False


def test_only_the_built_in_plans_are_offered_for_a_restore() -> None:
    plans = [
        ("平衡", BALANCED),
        ("我的计划", CUSTOM),
        ("节能", SAVER),
    ]
    rows = planrestore.restorable_plans(plans)
    names = [row["name"] for row in rows]
    assert names == ["平衡", "节能"]


def test_a_plan_the_user_made_is_not_restored() -> None:
    # The refusal has to come before any Windows call, so it is the
    # same on every machine.
    ok, message = planrestore.restore_plan(CUSTOM)
    assert ok is False
    assert "自己建的计划" in message


def test_off_windows_the_restore_says_so() -> None:
    # The check for the platform comes first, so the answer is the same
    # on a machine that is not Windows.
    if sys.platform == "win32":
        return
    ok, message = planrestore.restore_plan(BALANCED)
    assert ok is False
    assert "Windows" in message
