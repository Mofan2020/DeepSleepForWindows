"""改电源计划这块：读的必须是选中的那个计划，不是「正在生效的那个」。

用户报过三次：下拉框换成「高性能」，面板上还写着「正在编辑：平衡」，
数值也不动。前端那半在前端的测试里钉（``scripts/check-gui-js.mjs``），
这里钉后面半：桥有没有把 GUID 原样递下去、名字有没有按 GUID 找、读设置
的时候有没有拿错计划。

**为什么值得单独写一条「读的是传入的那个计划」**：Win32 的
``PowerReadACValueIndex`` 第一个参数是 RootPowerKey，第二是 SchemeGuid。
把 SchemeGuid 传成 NULL（或者传了别的计划的 GUID）时，它照常返回成功，
给出的却是**正在生效的那个计划**的值。失败会报错，拿错计划不会——界面
上只是「换了计划数值不动」，看起来像刷新坏了。
"""
from __future__ import annotations

import pytest

from src.winsleep.core import powersettings


def _bridge(monkeypatch: pytest.MonkeyPatch):
    """一个不用真的去读注册表的桥。"""
    from src.winsleep.gui import bridge as bridge_mod

    created = {}

    def fake_init(self, *args, **kwargs):  # noqa: ANN001 - the real one takes a lot
        created["self"] = self

    monkeypatch.setattr(bridge_mod.Bridge, "__init__", fake_init)
    return bridge_mod.Bridge()


def test_the_guid_is_passed_through_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """界面上选的哪个计划，就去读哪个计划。"""
    seen: list[str] = []

    def fake_read(guid: str):  # noqa: ANN202
        seen.append(guid)
        return [{"guid": "g", "group": "sleep", "name": "睡眠", "value": 1}]

    monkeypatch.setattr(powersettings, "read_plan_settings", fake_read)
    bridge = _bridge(monkeypatch)

    result = bridge.plan_settings("381b4222-f694-41f0-9685-ff5bb260df2e")

    assert result["ok"] is True
    assert seen == ["381b4222-f694-41f0-9685-ff5bb260df2e"]


def test_the_name_is_looked_up_by_guid_and_not_borrowed_from_elsewhere(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """名字按 GUID 查。查不到就是空，界面会退回下拉框里那个名字。

    查不到时**不能**随便拿一个名字顶上——那正是「换计划名字不变」的来源。
    """
    monkeypatch.setattr(powersettings, "read_plan_settings",
                        lambda guid: [{"guid": "g", "group": "sleep", "name": "睡眠", "value": 1}])
    from src.winsleep.core import winpower

    monkeypatch.setattr(winpower, "list_plans", lambda: [
        ("平衡", "381b4222-f694-41f0-9685-ff5bb260df2e"),
        ("高性能", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"),
    ])
    bridge = _bridge(monkeypatch)

    hit = bridge.plan_settings("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")
    assert hit["plan_name"] == "高性能"

    # 花括号、大写都要认：界面上传什么形状不该决定对不对得上。
    braced = bridge.plan_settings("{8C5E7FDA-E8BF-4A96-9A85-A6E23A8C635C}")
    assert braced["plan_name"] == "高性能"

    unknown = bridge.plan_settings("00000000-0000-0000-0000-000000000000")
    assert unknown["plan_name"] == ""
    assert unknown["ok"] is True


def test_reading_a_plan_never_falls_back_to_the_active_one(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """每一行都必须带着传进来的那个 GUID 去读。

    ``read_setting`` 里 SchemeGuid 传错（NULL、或者「当前生效的那个」）时
    Win32 不报错，只是把别的计划的值给了你。这条把每个调用点的 plan 参数
    都抓下来核对——这是「换了计划数值不动」唯一能被机器抓住的地方。
    """
    schemes: list[str] = []

    monkeypatch.setattr(powersettings, "_read_ac", lambda: "ac")
    monkeypatch.setattr(powersettings, "_read_ac_default", lambda: "ac_default")

    def fake_read_index(fn, scheme, group, setting):  # noqa: ANN001, ANN202
        schemes.append(scheme)
        return True, 7

    monkeypatch.setattr(powersettings, "_read_index", fake_read_index)

    wanted = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
    rows = powersettings.read_plan_settings(wanted)

    assert rows, "一条设置都没读出来，这条测试就没意义"
    stray = sorted(set(schemes) - {wanted})
    assert set(schemes) == {wanted}, f"有调用没带对计划：{stray}"


def test_a_zero_plan_guid_is_not_silently_swapped_for_the_active_one(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """空 GUID 不能变成「读当前计划的设置」。"""
    schemes: list[str] = []
    monkeypatch.setattr(powersettings, "_read_ac", lambda: "ac")
    monkeypatch.setattr(powersettings, "_read_ac_default", lambda: "ac_default")

    def fake_read_index(fn, scheme, group, setting):  # noqa: ANN001, ANN202
        schemes.append(scheme)
        return True, 7

    monkeypatch.setattr(powersettings, "_read_index", fake_read_index)

    powersettings.read_plan_settings("")

    assert schemes == [] or set(schemes) == {""}, (
        "空 GUID 被换成了别的计划——界面上就是「换计划数值不动」")
