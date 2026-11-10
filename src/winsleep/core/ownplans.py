"""The program's own power plans.

Windows ships with a few plans, and they are not extreme enough for
what this program is for.  Two of its own are made here:

最节能
    The machine is put into the lowest state it can live in.  The
    processor drops as far as it goes, the screen goes dark quickly,
    the disks stop spinning, and the parts that are not needed are
    switched off.

最高性能
    The machine is kept as awake as it can be.  The processor never
    drops below its top speed, the screen stays on, the disks keep
    spinning, and the links between the parts stay at their fastest.

Both are made by copying a built-in plan and writing the settings into
the copy.  That way the plan has the same structure as any other one on
the machine, and the user can still change it afterwards.

The settings are written through the powrprof calls, so nothing has to
be parsed out of a localized command output.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = [
    "OWN_PLANS",
    "create_own_plan",
    "is_own_plan",
    "own_plan_settings",
]

log = logging.getLogger("winsleep.ownplans")

#: The plans this program makes.  The key is what the interface calls
#: them, and the value holds the settings they are made of.
OWN_PLANS: dict[str, dict[str, Any]] = {
    "saver": {
        "name": "DeepSleep 最节能",
        "description": "把机器压到最省电的状态。显示快速关闭、快速睡眠、硬盘快速停转、CPU 降频、USB/PCIe/无线节能。台式机/服务器可配置是否允许自动睡眠。",
        "settings": [
            # 显示
            ("7516b95f-f776-4464-8c53-06167f40cc99", "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e", 60),   # 1分钟关显示
            ("7516b95f-f776-4464-8c53-06167f40cc99", "adeda363-4d4b-4f3a-8d63-5b8c7c9e2f4a", 30),   # 亮度30%
            ("7516b95f-f776-4464-8c53-06167f40cc99", "17aaa29b-8b43-4c3a-bc64-0e5a7b8c9d01", 1),   # 自适应亮度开
            # 睡眠
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "29f6c1db-86da-48c5-9fdb-f2b67b1f44da", 300),  # 5分钟睡眠
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "9d7815a6-7ee4-497e-8888-515a05f02364", 0),   # 休眠从不
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d", 0),   # 唤醒定时器禁用
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "94ac6d29-73ce-41a6-809f-6363ba21b47e", 0),   # 混合睡眠关
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "e89c3d6b-5f7a-4c1e-9b8a-3d2e1f0a9b8c", 1),   # 睡眠需密码
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "23f3e4b1-9d8a-4b7c-8a5e-9f2c1d0b3a4e", 0),   # 快速启动关
            # 硬盘
            ("0012ee47-9041-4b5d-9b77-535fba8b1442", "6738e2c4-e8a5-4a42-b16a-e040e769756e", 60),  # 1分钟关硬盘
            ("0012ee47-9041-4b5d-9b77-535fba8b1442", "8c4f2b1a-9e3d-4c5a-8b7f-6e2d1c0b3a4f", 1),   # NVMe电源管理开
            # 处理器
            ("54533251-82be-4824-96c1-47b60b740d00", "893dee8e-2bef-41e0-89c6-b55d0929964c", 5),    # 最小处理器5%
            ("54533251-82be-4824-96c1-47b60b740d00", "bc5038f7-23e0-4960-96da-33abaf5935ec", 50),   # 最大处理器50%
            ("54533251-82be-4824-96c1-47b60b740d00", "5d76a2ca-e8c0-402f-a133-2158492d58ad", 0),   # 冷却策略被动
            ("54533251-82be-4824-96c1-47b60b740d00", "06cadf0e-64ed-448a-8927-ce7bf90eb35d", 1),   # 核心停车开
            # PCIe
            ("501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5", 2),   # 最大节能
            # 网络/无线
            ("12bbebe6-58d6-4636-95bb-3217ef867c1a", "1b8e5a4f-3c2d-4e1f-9b8a-7c6d5e4f3a2b", 1),   # 网络最大节能
            ("19cbb8fa-5279-450e-9fac-8a3d5fede023", "12bbebe6-58d6-4636-95bb-3217ef867c1a", 1),   # 无线最大节能
            # USB
            ("2a737441-1930-4402-8d77-b2bebba308a3", "48e6b7a6-50f5-4782-a5d4-53bb8f07e226", 1),   # USB挂起开
            # 多媒体
            ("9596fb26-9850-41fd-ac3e-4c5f8b4e1a2d", "03680956-93aa-451d-a930-8c4e7b2f1a3d", 2),   # 视频节能优先
            ("9596fb26-9850-41fd-ac3e-4c5f8b4e1a2d", "44d3b721-4c9a-4d1e-8b7a-6f5c4d3e2f1a", 1),   # 音频电源管理开
            # PCIe (重复确保)
            ("501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5", 2),
            # 显卡
            ("dab60367-53fe-4fbc-825e-521d069d2456", "a4e5b3c2-1f8d-4e7a-9b6c-5d4e3f2a1b0c", 1),
        ],
    },
    "performance": {
        "name": "DeepSleep 最高性能",
        "description": "把机器的性能拉满。显示常亮、不睡眠、硬盘不停转、CPU 满频、所有节能关闭。",
        "settings": [
            # 显示
            ("7516b95f-f776-4464-8c53-06167f40cc99", "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e", 0),    # 显示从不关
            ("7516b95f-f776-4464-8c53-06167f40cc99", "adeda363-4d4b-4f3a-8d63-5b8c7c9e2f4a", 100),  # 亮度100%
            ("7516b95f-f776-4464-8c53-06167f40cc99", "17aaa29b-8b43-4c3a-bc64-0e5a7b8c9d01", 0),   # 自适应亮度关
            # 睡眠
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "29f6c1db-86da-48c5-9fdb-f2b67b1f44da", 0),   # 睡眠从不
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "9d7815a6-7ee4-497e-8888-515a05f02364", 0),   # 休眠从不
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d", 2),   # 唤醒定时器仅重要
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "94ac6d29-73ce-41a6-809f-6363ba21b47e", 0),   # 混合睡眠关
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "e89c3d6b-5f7a-4c1e-9b8a-3d2e1f0a9b8c", 0),   # 睡眠不需密码
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "23f3e4b1-9d8a-4b7c-8a5e-9f2c1d0b3a4e", 1),   # 快速启动开
            # 硬盘
            ("0012ee47-9041-4b5d-9b77-535fba8b1442", "6738e2c4-e8a5-4a42-b16a-e040e769756e", 0),   # 硬盘从不关
            ("0012ee47-9041-4b5d-9b77-535fba8b1442", "8c4f2b1a-9e3d-4c5a-8b7f-6e2d1c0b3a4f", 0),   # NVMe电源管理关
            # 处理器
            ("54533251-82be-4824-96c1-47b60b740d00", "893dee8e-2bef-41e0-89c6-b55d0929964c", 100), # 最小处理器100%
            ("54533251-82be-4824-96c1-47b60b740d00", "bc5038f7-23e0-4960-96da-33abaf5935ec", 100), # 最大处理器100%
            ("54533251-82be-4824-96c1-47b60b740d00", "5d76a2ca-e8c0-402f-a133-2158492d58ad", 1),   # 冷却策略主动
            ("54533251-82be-4824-96c1-47b60b740d00", "06cadf0e-64ed-448a-8927-ce7bf90eb35d", 0),   # 核心停车关
            # PCIe
            ("501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5", 0),   # 链路电源管理关
            # 网络/无线
            ("12bbebe6-58d6-4636-95bb-3217ef867c1a", "1b8e5a4f-3c2d-4e1f-9b8a-7c6d5e4f3a2b", 0),   # 网络节能关
            ("19cbb8fa-5279-450e-9fac-8a3d5fede023", "12bbebe6-58d6-4636-95bb-3217ef867c1a", 0),   # 无线节能关
            # USB
            ("2a737441-1930-4402-8d77-b2bebba308a3", "48e6b7a6-50f5-4782-a5d4-53bb8f07e226", 0),   # USB挂起关
            # 多媒体
            ("9596fb26-9850-41fd-ac3e-4c5f8b4e1a2d", "03680956-93aa-451d-a930-8c4e7b2f1a3d", 0),   # 视频性能优先
            ("9596fb26-9850-41fd-ac3e-4c5f8b4e1a2d", "44d3b721-4c9a-4d1e-8b7a-6f5c4d3e2f1a", 0),   # 音频电源管理关
            # PCIe
            ("501a4d13-42af-4429-9fd1-a8218c268e20", "ee12f906-d277-404b-b6da-e5fa1a576df5", 0),
            # 显卡
            ("dab60367-53fe-4fbc-825e-521d069d2456", "a4e5b3c2-1f8d-4e7a-9b6c-5d4e3f2a1b0c", 0),
        ],
    },
}

#: The plan the copies are made from.  "平衡" is the one Windows always
#: has, and it is a sane base for both of the program's plans.
_BASE_PLAN = "381b4222-f694-41f0-9685-ff5bb260df2e"


def is_own_plan(name: str) -> bool:
    """True when the name is one of the program's own plans."""
    return name.strip() in {str(plan["name"]) for plan in OWN_PLANS.values()}


def own_plan_settings() -> list[dict[str, str]]:
    """The program's own plans, ready for the interface."""
    return [
        {
            "key": key,
            "name": str(plan["name"]),
            "description": str(plan["description"]),
        }
        for key, plan in OWN_PLANS.items()
    ]


def create_own_plan(kind: str) -> tuple[bool, str]:
    """Make one of the program's own power plans.

    A built-in plan is copied, the copy is given the name of the new
    plan, and the settings that make it what it is are written into it.

    Returns the GUID of the new plan.
    """
    plan = OWN_PLANS.get(kind.strip())
    if plan is None:
        # The plan is checked before the platform, so the answer is the
        # same on every machine.
        return False, f"没有这个计划：{kind}"

    from . import powersettings

    ok, detail = powersettings.duplicate_plan(_BASE_PLAN, str(plan["name"]))
    if not ok:
        return False, f"复制电源计划失败：{detail}"

    new_guid = detail
    try:
        from .winapi import guid_from_string

        guid_from_string(new_guid)
    except ValueError:
        return False, "复制出来的计划没有有效的 GUID"

    written = 0
    for group, setting, value in plan["settings"]:
        wrote, message = powersettings.write_setting(new_guid, group, setting, int(value))
        if wrote:
            written += 1
        else:
            log.warning(
                "the setting %s of the new plan could not be written: %s", setting, message
            )

    # The copy is the one the user sees, so it has to be the one that
    # is active - otherwise the settings would not be in effect until
    # the next switch.
    from . import winpower

    winpower.set_active_plan(new_guid)
    log.info("the plan %s was created with %d settings", plan["name"], written)
    return True, new_guid
