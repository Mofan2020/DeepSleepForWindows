"""Putting a built-in power plan back the way Windows made it.

A user can change every setting of a power plan.  When they want the
plan back the way it was, the settings have to be restored to the
values Windows ships with.

Two things make this less simple than it looks:

*   A plan the user made themselves has no "default" - there is no
    version of it that Windows made.  Those plans are not restored.
*   The values that are restored are the ones for the machine, taken
    from the template the plan was made from.  Windows keeps those in
    the registry, under the "Default" subkeys of the power scheme.

Everything is done through the registry, so nothing has to be parsed
out of a localized command output.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

__all__ = [
    "BUILT_IN_PLANS",
    "is_built_in",
    "restorable_plans",
    "restore_plan",
]

log = logging.getLogger("winsleep.planrestore")

#: The plans Windows ships with.  A plan whose GUID is in here can be
#: restored; anything else is the user's own and is left alone.
#:
#: The GUIDs are the same on every Windows 10 and 11 machine, whatever
#: the language, so they can be written down here.
BUILT_IN_PLANS: dict[str, str] = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "平衡",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "高性能",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "节能",
    "e9a42b02-d5df-448d-aa00-03f14749eb61": "卓越性能",
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": "卓越性能（叠加）",
    "961cc777-2547-4f9d-8174-7d86181b8a7a": "节能（叠加）",
    "ded574b5-45a0-4f42-8737-46345c09c238": "最高性能（叠加）",
}

#: Where Windows keeps the power schemes.  The path goes through
#: "User" - "Control\Power\PowerSchemes" does not exist.
_POWER_SCHEMES_KEY = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"

#: The two halves of a plan.  Both are restored, because a laptop that
#: is on the charger and one that is on the battery are two different
#: machines as far as the settings go.
_SETTINGS_ROOTS = ("ACSettingIndex", "DCSettingIndex")


def is_built_in(guid: str) -> bool:
    """True when the plan is one Windows made.

    The user's own plans cannot be restored, so the interface only shows
    the button for the ones that can.
    """
    return guid.strip().lower() in BUILT_IN_PLANS


def restorable_plans(plans: list[tuple[str, str]]) -> list[dict[str, str]]:
    """The plans that can be restored, ready for the interface.

    *plans* is the list of (name, guid) pairs the program found.
    """
    rows: list[dict[str, str]] = []
    for name, guid in plans:
        if is_built_in(guid):
            rows.append({"name": name, "guid": guid.lower()})
    return rows


def restore_plan(guid: str) -> tuple[bool, str]:
    """Put a built-in plan back the way Windows made it.

    The values are copied from the "Default" subkeys of the same plan
    in the registry.  Those hold what Windows uses for the settings the
    plan does not mention, so copying them over gives the plan exactly
    the values Windows intends.
    """
    target = guid.strip().lower()
    if not is_built_in(target):
        # This check comes first, so the answer is the same on every
        # machine - the user does not get a different reason because
        # the program is running somewhere else.
        return False, "这是你自己建的计划，没有可以恢复的默认值"

    if sys.platform != "win32":
        return False, "只能在 Windows 上执行"

    try:
        import winreg
    except ImportError:
        return False, "winreg 不可用"

    copied = 0
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, _POWER_SCHEMES_KEY + "\\" + target
        ) as plan_key:
            for group_name in _enum_keys(winreg, plan_key):
                if group_name.lower().startswith("default"):
                    continue
                copied += _restore_group(winreg, plan_key, target, group_name)
    except OSError as exc:
        return False, f"读不到电源计划：{exc}"

    if copied == 0:
        return False, "没有找到可以恢复的设置"

    log.info("the plan %s was restored, %d settings written back", target, copied)
    return True, f"已恢复 {copied} 项设置"


def _enum_keys(winreg, key) -> list[str]:
    """Every subkey of *key*.

    Python 3.13 changed EnumKey to return the name on its own; before
    that it returned a (name, class) pair.  Both are handled so the
    program works on every supported Python.
    """
    names: list[str] = []
    index = 0
    while True:
        try:
            result = winreg.EnumKey(key, index)
        except OSError:
            break
        index += 1
        names.append(result if isinstance(result, str) else result[0])
    return names


def _restore_group(winreg, plan_key, guid: str, group_name: str) -> int:
    """Restore every setting of one group from its "Default" copy.

    The registry holds a group as a subkey of the plan, and every
    setting of it as a subkey of the group.  Each setting may have an
    ACSettingIndex and a DCSettingIndex value.  The default values
    live in the sibling subkey whose name starts with "Default".
    """
    copied = 0
    try:
        with winreg.OpenKey(plan_key, group_name) as group:
            for setting in _enum_keys(winreg, group):
                copied += _restore_setting(winreg, plan_key, guid, group_name, setting)
    except OSError:
        return 0
    return copied


def _restore_setting(winreg, plan_key, guid: str, group_name: str, setting: str) -> int:
    """Restore one setting from the "Default" group.

    A setting that has no value of its own already follows the default,
    so there is nothing to write.  When it has one, the value from the
    default group is written over it.
    """
    try:
        with winreg.OpenKey(plan_key, group_name) as group:
            with winreg.OpenKey(group, setting) as key:
                copied = 0
                for root in _SETTINGS_ROOTS:
                    try:
                        _, value_type = winreg.QueryValueEx(key, root)
                    except OSError:
                        # The setting has no value of its own, so it
                        # already follows the default.
                        continue
                    default_value = _read_default(winreg, guid, group_name, setting, root)
                    if default_value is None:
                        continue
                    try:
                        winreg.SetValueEx(key, root, 0, value_type, default_value)
                    except OSError as exc:
                        log.warning(
                            "the setting %s could not be restored: %s", setting, exc
                        )
                        continue
                    copied = 1
                return copied
    except OSError:
        return 0


def _read_default(
    winreg, guid: str, group_name: str, setting: str, root: str
) -> Any:
    """The value the "Default" group holds for one setting."""
    for prefix in ("Default", "default"):
        default_group = prefix + group_name
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                _POWER_SCHEMES_KEY + "\\" + guid + "\\" + default_group,
            ) as group:
                with winreg.OpenKey(group, setting) as key:
                    value, _ = winreg.QueryValueEx(key, root)
                    return value
        except OSError:
            continue
    return None
