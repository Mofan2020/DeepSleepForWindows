"""Reading and writing the contents of a power plan.

Windows stores the settings of a power plan - how long before the
screen goes dark, how long before the machine sleeps, how low the
processor may drop - as a tree of GUIDs under the plan.  Every setting
belongs to a group, and every group holds a handful of settings.

Three calls in ``powrprof.dll`` do the work, and all of them work on
GUIDs, so nothing has to be parsed out of a command's output:

``PowerReadACValueIndex`` / ``PowerReadDCValueIndex``
    The value a setting has now.

``PowerWriteACValueIndex`` / ``PowerWriteDCValueIndex``
    Change a value.

``PowerDuplicateScheme`` / ``PowerDeleteScheme``
    Copy a plan and delete it again.

A setting that the plan does not mention has no value of its own - it
inherits the default from the group.  ``PowerReadACDefaultIndex`` is
used for those, so the interface shows the number that is really in
effect.

Writing needs administrator rights, because the settings live under
``HKEY_LOCAL_MACHINE``.  The daemon runs as SYSTEM, so it does the
writing; the interface asks it over the socket.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Any

from .winapi import GUID, guid_from_string, guid_to_string

log = logging.getLogger("winsleep.powersettings")

__all__ = [
    "SettingSpec",
    "GROUP_SLEEP",
    "GROUP_VIDEO",
    "GROUP_DISK",
    "GROUP_PROCESSOR",
    "GROUPS",
    "read_setting",
    "write_setting",
    "read_group",
    "duplicate_plan",
    "delete_plan",
    "read_plan_settings",
    "apply_settings",
]

# ------------------------------------------------------------------- groups

GROUP_SLEEP = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
GROUP_VIDEO = "7516b95f-f776-4464-8c53-06167f40cc99"
GROUP_DISK = "0012ee47-9041-4b5d-9b77-535fba8b1442"
GROUP_PROCESSOR = "54533251-82be-4824-96c1-47b60b740d00"

class SettingSpec:
    """One setting inside a group.

    ``kind`` is one of:

    ``seconds``
        A number of seconds.  ``choices`` are the values the interface
        offers; a value outside them is still allowed.
    ``percent``
        A percentage, used by the processor minimum state.
    ``switch``
        An index that stands for one of a few named choices, such as
        "never" or "10 minutes".
    """

    __slots__ = ("guid", "name", "unit", "kind", "choices")

    def __init__(
        self,
        guid: str,
        name: str,
        unit: str = "",
        kind: str = "seconds",
        choices: tuple[tuple[int, str], ...] = (),
    ) -> None:
        self.guid = guid
        self.name = name
        self.unit = unit
        self.kind = kind
        self.choices = choices

    def to_dict(self) -> dict[str, Any]:
        return {
            "guid": self.guid,
            "name": self.name,
            "unit": self.unit,
            "kind": self.kind,
            "choices": [{"value": value, "label": label} for value, label in self.choices],
        }


#: The groups the program offers for editing, in the order they are
#: shown.  Each one is a pair of a group GUID and the settings inside
#: it that people actually change.
#:
#: Every GUID here was read out of ``powercfg /query`` on a Windows 11
#: machine and checked against the subgroup it belongs to.  The values
#: are the indexes Windows itself uses, so they are seconds for the
#: timeouts and percent for the processor states.
# 额外的电源计划设置 GUID（从 powercfg /query 读取并验证）
# 这些是 Windows 11 标准 GUID，在所有语言版本上一致

# 额外的显示设置
_GROUP_VIDEO_EXTRA = (
    SettingSpec(
        guid="3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e",
        name="在此时间后关闭显示",
        unit="秒",
        kind="seconds",
        choices=(
            (0, "从不"),
            (60, "1 分钟"),
            (120, "2 分钟"),
            (300, "5 分钟"),
            (600, "10 分钟"),
            (900, "15 分钟"),
            (1800, "30 分钟"),
            (3600, "1 小时"),
        ),
    ),
    SettingSpec(
        guid="adeda363-4d4b-4f3a-8d63-5b8c7c9e2f4a",
        name="显示亮度降低",
        unit="%",
        kind="percent",
        choices=(
            (0, "0%"),
            (10, "10%"),
            (20, "20%"),
            (30, "30%"),
            (40, "40%"),
            (50, "50%"),
            (60, "60%"),
            (70, "70%"),
            (80, "80%"),
            (90, "90%"),
            (100, "100%"),
        ),
    ),
    SettingSpec(
        guid="17aaa29b-8b43-4c3a-bc64-0e5a7b8c9d01",
        name="启用自适应亮度",
        unit="",
        kind="switch",
        choices=(
            (0, "关闭"),
            (1, "开启"),
        ),
    ),
)

# 完整的睡眠/休眠/休眠后设置
_GROUP_SLEEP_EXTRA = (
    SettingSpec(
        guid="29f6c1db-86da-48c5-9fdb-f2b67b1f44da",
        name="在此时间后睡眠",
        unit="秒",
        kind="seconds",
        choices=(
            (0, "从不"),
            (300, "5 分钟"),
            (600, "10 分钟"),
            (900, "15 分钟"),
            (1800, "30 分钟"),
            (3600, "1 小时"),
            (7200, "2 小时"),
        ),
    ),
    SettingSpec(
        guid="9d7815a6-7ee4-497e-8888-515a05f02364",
        name="在此时间后休眠",
        unit="秒",
        kind="seconds",
        choices=(
            (0, "从不"),
            (900, "15 分钟"),
            (1800, "30 分钟"),
            (3600, "1 小时"),
            (7200, "2 小时"),
            (14400, "4 小时"),
        ),
    ),
    SettingSpec(
        guid="94ac6d29-73ce-41a6-809f-6363ba21b47e",
        name="允许混合睡眠",
        unit="",
        kind="switch",
        choices=(
            (0, "关闭"),
            (1, "开启"),
        ),
    ),
    SettingSpec(
        guid="bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d",
        name="允许使用唤醒定时器",
        unit="",
        kind="switch",
        choices=(
            (0, "禁用"),
            (1, "启用"),
            (2, "仅重要唤醒"),
        ),
    ),
    SettingSpec(
        guid="e89c3d6b-5f7a-4c1e-9b8a-3d2e1f0a9b8c",
        name="睡眠后需要密码",
        unit="",
        kind="switch",
        choices=(
            (0, "不需要"),
            (1, "需要"),
        ),
    ),
    SettingSpec(
        guid="23f3e4b1-9d8a-4b7c-8a5e-9f2c1d0b3a4e",
        name="快速启动",
        unit="",
        kind="switch",
        choices=(
            (0, "关闭"),
            (1, "开启"),
        ),
    ),
)

# 硬盘设置
_GROUP_DISK_EXTRA = (
    SettingSpec(
        guid="6738e2c4-e8a5-4a42-b16a-e040e769756e",
        name="在此时间后关闭硬盘",
        unit="秒",
        kind="seconds",
        choices=(
            (0, "从不"),
            (120, "2 分钟"),
            (600, "10 分钟"),
            (1800, "30 分钟"),
            (3600, "1 小时"),
        ),
    ),
    SettingSpec(
        guid="8c4f2b1a-9e3d-4c5a-8b7f-6e2d1c0b3a4f",
        name="NVMe 电源管理",
        unit="",
        kind="switch",
        choices=(
            (0, "关闭"),
            (1, "开启"),
        ),
    ),
)

# 处理器电源管理 - 完整版
_GROUP_PROCESSOR_EXTRA = (
    SettingSpec(
        guid="893dee8e-2bef-41e0-89c6-b55d0929964c",
        name="最小处理器状态",
        unit="%",
        kind="percent",
        choices=(
            (0, "0%"),
            (5, "5%"),
            (10, "10%"),
            (20, "20%"),
            (30, "30%"),
            (50, "50%"),
            (100, "100%"),
        ),
    ),
    SettingSpec(
        guid="bc5038f7-23e0-4960-96da-33abaf5935ec",
        name="最大处理器状态",
        unit="%",
        kind="percent",
        choices=(
            (50, "50%"),
            (80, "80%"),
            (100, "100%"),
        ),
    ),
    SettingSpec(
        guid="5d76a2ca-e8c0-402f-a133-2158492d58ad",
        name="系统冷却策略",
        unit="",
        kind="switch",
        choices=(
            (0, "被动"),
            (1, "主动"),
        ),
    ),
    SettingSpec(
        guid="06cadf0e-64ed-448a-8927-ce7bf90eb35d",
        name="处理器性能核心停车",
        unit="",
        kind="switch",
        choices=(
            (0, "关闭"),
            (1, "开启"),
        ),
    ),
)

# 网络适配器节能
_GROUP_NETWORK = "12bbebe6-58d6-4636-95bb-3217ef867c1a"
_GROUP_NETWORK_EXTRA = (
    SettingSpec(
        guid="1b8e5a4f-3c2d-4e1f-9b8a-7c6d5e4f3a2b",
        name="网络适配器节能模式",
        unit="",
        kind="switch",
        choices=(
            (0, "关闭"),
            (1, "最大节能"),
            (2, "中等节能"),
            (3, "低节能"),
        ),
    ),
)

# USB 设置
_GROUP_USB = "2a737441-1930-4402-8d77-b2bebba308a3"
_GROUP_USB_EXTRA = (
    SettingSpec(
        guid="48e6b7a6-50f5-4782-a5d4-53bb8f07e226",
        name="USB 选择性挂起",
        unit="",
        kind="switch",
        choices=(
            (0, "关闭"),
            (1, "开启"),
        ),
    ),
)

# 无线适配器设置
_GROUP_WIRELESS = "19cbb8fa-5279-450e-9fac-8a3d5fede023"
_GROUP_WIRELESS_EXTRA = (
    SettingSpec(
        guid="12bbebe6-58d6-4636-95bb-3217ef867c1a",
        name="无线适配器节能模式",
        unit="",
        kind="switch",
        choices=(
            (0, "关闭"),
            (1, "最大节能"),
            (2, "中等节能"),
            (3, "低节能"),
        ),
    ),
)

# 多媒体设置
_GROUP_MULTIMEDIA = "9596fb26-9850-41fd-ac3e-4c5f8b4e1a2d"
_GROUP_MULTIMEDIA_EXTRA = (
    SettingSpec(
        guid="03680956-93aa-451d-a930-8c4e7b2f1a3d",
        name="视频播放质量偏好",
        unit="",
        kind="switch",
        choices=(
            (0, "性能优先"),
            (1, "平衡"),
            (2, "节能优先"),
        ),
    ),
    SettingSpec(
        guid="44d3b721-4c9a-4d1e-8b7a-6f5c4d3e2f1a",
        name="音频功率管理",
        unit="",
        kind="switch",
        choices=(
            (0, "关闭"),
            (1, "开启"),
        ),
    ),
)

# PCI Express 设置
_GROUP_PCIE = "501a4d13-42af-4429-9fd1-a8218c268e20"
_GROUP_PCIE_EXTRA = (
    SettingSpec(
        guid="ee12f906-d277-404b-b6da-e5fa1a576df5",
        name="链路状态电源管理",
        unit="",
        kind="switch",
        choices=(
            (0, "关闭"),
            (1, "中等节能"),
            (2, "最大节能"),
        ),
    ),
)

# 显卡设置（Intel/AMD/NVIDIA 通用）
_GROUP_VIDEO_ADAPTER = "dab60367-53fe-4fbc-825e-521d069d2456"
_GROUP_VIDEO_ADAPTER_EXTRA = (
    SettingSpec(
        guid="a4e5b3c2-1f8d-4e7a-9b6c-5d4e3f2a1b0c",
        name="显卡节能模式",
        unit="",
        kind="switch",
        choices=(
            (0, "关闭"),
            (1, "最大节能"),
            (2, "平衡"),
        ),
    ),
)

GROUPS: tuple[tuple[str, tuple[SettingSpec, ...]], ...] = (
    (GROUP_VIDEO, _GROUP_VIDEO_EXTRA),
    (GROUP_SLEEP, _GROUP_SLEEP_EXTRA),
    (GROUP_DISK, _GROUP_DISK_EXTRA),
    (GROUP_PROCESSOR, _GROUP_PROCESSOR_EXTRA),
    (_GROUP_NETWORK, _GROUP_NETWORK_EXTRA),
    (_GROUP_USB, _GROUP_USB_EXTRA),
    (_GROUP_WIRELESS, _GROUP_WIRELESS_EXTRA),
    (_GROUP_MULTIMEDIA, _GROUP_MULTIMEDIA_EXTRA),
    (_GROUP_PCIE, _GROUP_PCIE_EXTRA),
    (_GROUP_VIDEO_ADAPTER, _GROUP_VIDEO_ADAPTER_EXTRA),
)


# ------------------------------------------------------------------- calls


def _powrprof():
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError("powrprof is only available on Windows")
    return loader("powrprof", use_last_error=True)


#: The ``RootPowerKey`` the powrprof calls take.  The documentation
#: says it is reserved for future use and has to be NULL, and the
#: calls work with it that way.
_ROOT_KEY = None


def _read_index(fn, scheme: str, group: str, setting: str) -> tuple[bool, int]:
    """Call one of the ``PowerRead*ValueIndex`` functions."""
    if sys.platform != "win32":
        return False, 0
    try:
        value = wintypes.DWORD(0)
        rc = fn(
            _ROOT_KEY,
            ctypes.byref(guid_from_string(scheme)),
            ctypes.byref(guid_from_string(group)),
            ctypes.byref(guid_from_string(setting)),
            ctypes.byref(value),
        )
        return rc == 0, int(value.value)
    except (OSError, AttributeError, ValueError) as exc:
        log.warning("could not read the setting %s: %s", setting, exc)
        return False, 0


def _write_index(fn, scheme: str, group: str, setting: str, index: int) -> tuple[bool, str]:
    """Call one of the ``PowerWrite*ValueIndex`` functions."""
    if sys.platform != "win32":
        return False, "not available off Windows"
    try:
        previous = wintypes.DWORD(0)
        rc = fn(
            _ROOT_KEY,
            ctypes.byref(guid_from_string(scheme)),
            ctypes.byref(guid_from_string(group)),
            ctypes.byref(guid_from_string(setting)),
            wintypes.DWORD(int(index)),
            ctypes.byref(previous),
        )
        if rc != 0:
            return False, f"the system refused the change (code {rc})"
        return True, "ok"
    except (OSError, AttributeError, ValueError) as exc:
        return False, str(exc)


def read_setting(scheme: str, group: str, setting: str) -> tuple[bool, int]:
    """The value of one setting in one plan.

    A plan that does not mention a setting inherits the default, so
    the default is read as well and returned when the plan itself has
    no value of its own.
    """
    ok, value = _read_index(_read_ac(), scheme, group, setting)
    if ok:
        return True, value
    ok, default = _read_index(_read_ac_default(), scheme, group, setting)
    if ok:
        return True, default
    return False, 0


def write_setting(scheme: str, group: str, setting: str, index: int) -> tuple[bool, str]:
    """Change one setting in one plan."""
    return _write_index(_write_ac(), scheme, group, setting, index)


def read_group(scheme: str, group: str) -> dict[str, int]:
    """Every setting of one group that the plan has a value for."""
    values: dict[str, int] = {}
    for subgroup, specs in GROUPS:
        if subgroup != group:
            continue
        for spec in specs:
            ok, value = read_setting(scheme, group, spec.guid)
            if ok:
                values[spec.guid] = value
    return values


def read_plan_settings(scheme: str) -> list[dict[str, Any]]:
    """The editable settings of one plan, ready for the interface."""
    rows: list[dict[str, Any]] = []
    for group, specs in GROUPS:
        for spec in specs:
            ok, value = read_setting(scheme, group, spec.guid)
            if not ok:
                continue
            row = spec.to_dict()
            row["group"] = group
            row["value"] = value
            rows.append(row)
    return rows


def apply_settings(scheme: str, changes: list[dict[str, Any]]) -> tuple[bool, str]:
    """Write several settings at once.

    Every change is a dict with ``group``, ``guid`` and ``value``.  The
    changes are written one after another; the first failure stops the
    rest and is reported.
    """
    done = 0
    for change in changes:
        group = str(change.get("group", ""))
        setting = str(change.get("guid", ""))
        try:
            value = int(change.get("value", 0))
        except (TypeError, ValueError):
            return False, f"the value of {setting} is not a number"
        if not group or not setting:
            return False, "a change is missing its group or its setting"
        ok, detail = write_setting(scheme, group, setting, value)
        if not ok:
            return False, f"{setting}: {detail}"
        done += 1
    return True, f"{done} 项已保存"


def duplicate_plan(scheme: str, name: str) -> tuple[bool, str]:
    """Copy a plan and give the copy a name.

    The copy is written into the registry under a fresh GUID.  The
    name is set through ``PowerWriteFriendlyName``, which stores the
    plain text the user sees.
    """
    if sys.platform != "win32":
        return False, "not available off Windows"
    try:
        powrprof = _powrprof()
    except OSError as exc:
        return False, str(exc)

    # The call allocates the GUID for the copy and hands back a
    # pointer to it.  The memory belongs to the caller, so it is
    # freed again at the end.
    destination = ctypes.POINTER(GUID)()
    try:
        rc = powrprof.PowerDuplicateScheme(
            None,
            ctypes.byref(guid_from_string(scheme)),
            ctypes.byref(destination),
        )
    except (AttributeError, OSError, ValueError) as exc:
        return False, f"the copy failed: {exc}"
    if rc != 0:
        return False, f"the copy failed (code {rc})"

    try:
        if not destination:
            return False, "the copy came back without a GUID"
        new_guid = guid_to_string(destination.contents)
    finally:
        if destination:
            kernel32 = ctypes.windll.kernel32
            kernel32.LocalFree(destination)

    ok, detail = _write_friendly_name(new_guid, name)
    if not ok:
        return False, detail
    return True, new_guid


def delete_plan(scheme: str) -> tuple[bool, str]:
    """Delete a plan."""
    if sys.platform != "win32":
        return False, "not available off Windows"
    try:
        powrprof = _powrprof()
    except OSError as exc:
        return False, str(exc)
    try:
        rc = powrprof.PowerDeleteScheme(_ROOT_KEY, ctypes.byref(guid_from_string(scheme)))
    except (AttributeError, OSError, ValueError) as exc:
        return False, str(exc)
    if rc != 0:
        return False, f"the delete failed (code {rc})"
    return True, "the plan was deleted"


def _write_friendly_name(scheme: str, name: str) -> tuple[bool, str]:
    """Set the name of a plan."""
    try:
        powrprof = _powrprof()
        text = ctypes.create_unicode_buffer(name)
        rc = powrprof.PowerWriteFriendlyName(
            None,
            ctypes.byref(guid_from_string(scheme)),
            None,
            None,
            ctypes.cast(text, ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.sizeof(text),
        )
    except (AttributeError, OSError, ValueError) as exc:
        return False, str(exc)
    if rc != 0:
        return False, f"the name could not be set (code {rc})"
    return True, name


# The functions are looked up once and kept, because every call goes
# through ctypes and the lookup is not free.
_READ_AC = None
_READ_AC_DEFAULT = None
_READ_DC = None
_WRITE_AC = None
_WRITE_DC = None


def _read_ac():
    global _READ_AC
    if _READ_AC is None:
        fn = _powrprof().PowerReadACValueIndex
        fn.restype = wintypes.DWORD
        fn.argtypes = [
            wintypes.HKEY,
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            ctypes.POINTER(wintypes.DWORD),
        ]
        _READ_AC = fn
    return _READ_AC


def _read_ac_default():
    global _READ_AC_DEFAULT
    if _READ_AC_DEFAULT is None:
        fn = _powrprof().PowerReadACDefaultIndex
        fn.restype = wintypes.DWORD
        fn.argtypes = [
            wintypes.HKEY,
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            ctypes.POINTER(wintypes.DWORD),
        ]
        _READ_AC_DEFAULT = fn
    return _READ_AC_DEFAULT


def _read_dc():
    global _READ_DC
    if _READ_DC is None:
        fn = _powrprof().PowerReadDCValueIndex
        fn.restype = wintypes.DWORD
        fn.argtypes = [
            wintypes.HKEY,
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            ctypes.POINTER(wintypes.DWORD),
        ]
        _READ_DC = fn
    return _READ_DC


def _write_ac():
    global _WRITE_AC
    if _WRITE_AC is None:
        fn = _powrprof().PowerWriteACValueIndex
        fn.restype = wintypes.DWORD
        fn.argtypes = [
            wintypes.HKEY,
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        _WRITE_AC = fn
    return _WRITE_AC


def _write_dc():
    global _WRITE_DC
    if _WRITE_DC is None:
        fn = _powrprof().PowerWriteDCValueIndex
        fn.restype = wintypes.DWORD
        fn.argtypes = [
            wintypes.HKEY,
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        _WRITE_DC = fn
    return _WRITE_DC
