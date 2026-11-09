"""The program's own power plans.

Windows ships with a few plans, and they are not extreme enough for
what this program is for.  Two of its own are made here:

DSForWin 最节能
    The machine is put into the lowest state it can live in.  The
    processor drops as far as it goes, the screen goes dark quickly,
    the disks stop spinning, and the parts that are not needed are
    switched off.

DSForWin 最高性能
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
        "name": "DSForWin 最节能",
        "description": "把机器压到最省电的状态。",
        "settings": [
            ("7516b95f-f776-4464-8c53-06167f40cc99", "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e", 120),
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "29f6c1db-86da-48c5-9fdb-f2b67b1f44da", 600),
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "9d7815a6-7ee4-497e-8888-515a05f02364", 0),
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d", 0),
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "94ac6d29-73ce-41a6-809f-6363ba21b47e", 0),
            ("0012ee47-9041-4b5d-9b77-535fba8b1442", "6738e2c4-e8a5-4a42-b16a-e040e769756e", 120),
            ("54533251-82be-4824-96c1-47b60b740d00", "893dee8e-2bef-41e0-89c6-b55d0929964c", 5),
            ("54533251-82be-4824-96c1-47b60b740d00", "bc5038f7-23e0-4960-96da-33abaf5935ec", 50),
            ("54533251-82be-4824-96c1-47b60b740d00", "be337238-0d82-5606-927f-8c896e116314", 3),
            ("501a4d13-42af-4429-9fd1-a8218c268e20", "ee0d0a2c-bbc0-4e3e-b9c4-3e3b3c5b5b0b", 0),
        ],
    },
    "performance": {
        "name": "DSForWin 最高性能",
        "description": "把机器的性能拉满。",
        "settings": [
            ("7516b95f-f776-4464-8c53-06167f40cc99", "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e", 0),
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "29f6c1db-86da-48c5-9fdb-f2b67b1f44da", 0),
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "9d7815a6-7ee4-497e-8888-515a05f02364", 0),
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d", 2),
            ("238c9fa8-0aad-41ed-83f4-97be242c8f20", "94ac6d29-73ce-41a6-809f-6363ba21b47e", 0),
            ("0012ee47-9041-4b5d-9b77-535fba8b1442", "6738e2c4-e8a5-4a42-b16a-e040e769756e", 0),
            ("54533251-82be-4824-96c1-47b60b740d00", "893dee8e-2bef-41e0-89c6-b55d0929964c", 100),
            ("54533251-82be-4824-96c1-47b60b740d00", "bc5038f7-23e0-4960-96da-33abaf5935ec", 100),
            ("54533251-82be-4824-96c1-47b60b740d00", "be337238-0d82-5606-927f-8c896e116314", 0),
            ("501a4d13-42af-4429-9fd1-a8218c268e20", "ee0d0a2c-bbc0-4e3e-b9c4-3e3b3c5b5b0b", 0),
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
