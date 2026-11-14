"""电源计划的决定不许抖。

**为什么要有这一层。** 决定是「CPU 低于阈值」这种单点比较，机器上常有
后台任务在跑，读数就在阈值边上晃。实测一台安静机器上后台服务每五秒切
一次电源计划，界面上「正在编辑的计划」也跟着换，用户没保存的改动当场
就没了。

这里钉住两件事：

* CPU 读数先平均，再用平均值判断。
* 新计划要连续稳定够久才真的切过去；抖动的决定一次都不换。
"""
from __future__ import annotations

from winsleep.core.stability import CpuAverage, PlanStabilizer

# ------------------------------------------------------------- CPU average


def test_the_average_smooths_one_spike() -> None:
    average = CpuAverage(window=4)
    for value in (10.0, 10.0, 10.0):
        average.add(value)
    # 一次 80% 的尖峰只把平均值抬到 27.5，没有直接用原始值的话会判成忙。
    assert average.add(80.0) == 27.5


def test_the_window_keeps_only_the_last_samples() -> None:
    average = CpuAverage(window=3)
    for value in (10.0, 10.0, 10.0):
        average.add(value)
    assert average.add(0.0) == 6.666666666666667
    # 再来一次，最早的那个 10 就被挤出去了。
    assert average.add(0.0) == 3.3333333333333335


def test_no_samples_means_no_cpu() -> None:
    assert CpuAverage().value() == 0.0


def test_a_broken_reading_does_not_break_the_average() -> None:
    average = CpuAverage(window=2)
    average.add(10.0)
    assert average.add("not a number") == 5.0


# ---------------------------------------------------------- plan stabilizer


def test_the_first_plan_is_taken_right_away() -> None:
    stabilizer = PlanStabilizer(hold_seconds=15.0)
    # 刚启动不用等：这就是第一次决定。
    assert stabilizer.update(100.0, "idle") == "idle"
    assert stabilizer.current == "idle"


def test_a_new_plan_has_to_hold_before_it_is_used() -> None:
    stabilizer = PlanStabilizer(hold_seconds=15.0)
    stabilizer.update(100.0, "idle")
    assert stabilizer.update(105.0, "busy") == "idle"
    assert stabilizer.update(114.0, "busy") == "idle"
    assert stabilizer.update(120.0, "busy") == "busy"


def test_a_decision_that_keeps_flipping_never_switches() -> None:
    """这就是那台机器上的样子：每五秒在忙 / 闲之间换。

    计时器每次都被重置，于是计划一次都不动。
    """
    stabilizer = PlanStabilizer(hold_seconds=15.0)
    stabilizer.update(0.0, "idle")
    now = 5.0
    for _ in range(20):
        now += 5.0
        stabilizer.update(now, "busy")
        now += 5.0
        stabilizer.update(now, "idle")
    assert stabilizer.current == "idle"


def test_no_plan_means_leave_it_alone() -> None:
    """决定是「别动计划」的时候，不能把上一个计划再写回去。

    那会变成跟别的工具抢电源计划。
    """
    stabilizer = PlanStabilizer(hold_seconds=15.0)
    stabilizer.update(0.0, "idle")
    assert stabilizer.update(100.0, None) is None


def test_going_back_to_the_current_plan_clears_the_pending_one() -> None:
    stabilizer = PlanStabilizer(hold_seconds=15.0)
    stabilizer.update(0.0, "idle")
    stabilizer.update(5.0, "busy")
    assert stabilizer.pending == "busy"
    stabilizer.update(6.0, "idle")
    assert stabilizer.pending is None
    # 计时器被清掉了：再想换成 busy 得从头等。
    assert stabilizer.update(20.0, "busy") == "idle"
