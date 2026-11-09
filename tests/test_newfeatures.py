"""The things that came with the v0.3.0 round.

The single instance lock, the program's own power plans and the
graphics card are all things that cannot be tried on the machine the
code is written on, so the parts that can be checked without Windows
are checked here.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from winsleep.core import gpu, ownplans, single

# ------------------------------------------------------------ single instance


def test_the_first_copy_gets_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "window.lock"
    with single.SingleInstance(path) as first:
        assert first.is_first is True
        assert path.exists()


def test_the_second_copy_does_not_get_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "window.lock"
    with single.SingleInstance(path) as first:
        assert first.is_first is True
        with single.SingleInstance(path) as second:
            assert second.is_first is False


def test_the_lock_is_given_back(tmp_path: Path) -> None:
    path = tmp_path / "window.lock"
    with single.SingleInstance(path) as first:
        assert first.is_first is True
    assert not path.exists()
    # And the next copy may take it.
    with single.SingleInstance(path) as second:
        assert second.is_first is True


def test_a_lock_of_a_dead_process_is_taken_over(tmp_path: Path) -> None:
    path = tmp_path / "window.lock"
    # A lock that is old and whose owner is gone must not stop the
    # program from starting - otherwise a killed copy would block it
    # forever.
    path.write_text("999999999", encoding="utf-8")
    old = time.time() - 3600
    os.utime(path, (old, old))

    with single.SingleInstance(path) as instance:
        assert instance.is_first is True


def test_a_lock_of_a_live_process_is_respected(tmp_path: Path) -> None:
    path = tmp_path / "window.lock"
    path.write_text(str(os.getpid()), encoding="utf-8")
    old = time.time() - 3600
    os.utime(path, (old, old))

    with single.SingleInstance(path) as instance:
        assert instance.is_first is False


# ------------------------------------------------------------- the own plans


def test_there_are_two_own_plans() -> None:
    plans = ownplans.own_plan_settings()
    assert len(plans) == 2
    names = [plan["name"] for plan in plans]
    assert "DSForWin 最节能" in names
    assert "DSForWin 最高性能" in names


def test_the_own_plans_are_told_apart() -> None:
    saver = ownplans.OWN_PLANS["saver"]["settings"]
    performance = ownplans.OWN_PLANS["performance"]["settings"]
    assert saver != performance


def test_the_saver_plan_does_not_let_the_processor_run_its_top() -> None:
    saver = {setting: value for _group, setting, value in ownplans.OWN_PLANS["saver"]["settings"]}
    performance = {
        setting: value
        for _group, setting, value in ownplans.OWN_PLANS["performance"]["settings"]
    }
    minimum = "893dee8e-2bef-41e0-89c6-b55d0929964c"
    assert saver[minimum] < performance[minimum]


def test_an_unknown_own_plan_is_refused() -> None:
    ok, message = ownplans.create_own_plan("nonsense")
    assert ok is False
    assert "没有这个计划" in message


def test_the_own_plan_names_are_recognised() -> None:
    assert ownplans.is_own_plan("DSForWin 最节能") is True
    assert ownplans.is_own_plan("DSForWin 最高性能") is True
    assert ownplans.is_own_plan("平衡") is False


# ---------------------------------------------------------- the graphics card


def test_off_windows_the_graphics_card_is_not_supported() -> None:
    state = gpu.gpu_state()
    assert state.supported is False
    assert state.message


def test_off_windows_the_graphics_load_is_not_known() -> None:
    assert gpu.gpu_load() == -1


def test_an_unknown_graphics_preference_is_refused() -> None:
    state = gpu.set_gpu_preference("nonsense")
    assert state.applied is False
    assert "没有这个选项" in state.message


def test_the_graphics_state_can_be_given_to_the_interface() -> None:
    data = gpu.gpu_state().to_dict()
    assert "supported" in data
    assert "applied" in data
    assert "utilisation" in data
    assert "message" in data
