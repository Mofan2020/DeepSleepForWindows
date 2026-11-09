"""Making the graphics card use less power when it has nothing to do.

An NVIDIA card draws a lot of power even when the screen is still.
The way to stop that is to let the driver know that the card should
prefer to save power - then it is allowed to drop into a lower state,
and on the cards that support it, into a sleep state where almost
nothing is drawn.

Two things have to be true for that to work:

*   The card has to be idle.  A card that is drawing something cannot
    be put to sleep, and forcing it would make the picture stutter.
    The load is read from the performance counters of the driver.
*   The driver has to be asked.  Windows records the preference per
    graphics engine in the registry, and the program writes it there.

What the program can promise is this: it tells the driver which
preference to use, and it reports what it could read back.  Whether
the card really falls asleep is the driver's decision - Windows does
not offer an interface that forces it.  The interface shows what was
measured, so the user can see whether it worked.

Everything here is plain Python and works off Windows, so it is unit
tested on any machine.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

__all__ = [
    "GPUState",
    "gpu_load",
    "gpu_state",
    "is_nvidia_present",
    "set_gpu_preference",
]

log = logging.getLogger("winsleep.gpu")

_PREFERRED_GRAPHICS_KEY = "SOFTWARE\\Microsoft\\DirectX\\UserGpuPreferences"

PREFERENCE_SAVER = "2"
PREFERENCE_PERFORMANCE = "3"

_PREFERENCE_NAMES = {
    "1": "跟随系统",
    PREFERENCE_SAVER: "省电",
    PREFERENCE_PERFORMANCE: "最高性能",
}


class GPUState:
    """What the program found out about the graphics card.

    ``supported`` is False when there is no NVIDIA card.  ``applied``
    says whether the preference was written.  ``utilisation`` is the
    load the driver reported, or -1 when it could not be read.
    """

    __slots__ = ("supported", "applied", "utilisation", "message", "preference")

    def __init__(
        self,
        supported: bool = False,
        applied: bool = False,
        utilisation: int = -1,
        message: str = "",
        preference: str = "",
    ) -> None:
        self.supported = supported
        self.applied = applied
        self.utilisation = utilisation
        self.message = message
        self.preference = preference

    def to_dict(self) -> dict[str, Any]:
        return {
            "supported": self.supported,
            "applied": self.applied,
            "utilisation": self.utilisation,
            "message": self.message,
            "preference": self.preference,
        }


def _engine_guid() -> str:
    """The GUID of the graphics engine of the NVIDIA card.

    It is read from the registry, where Windows records the engine of
    every adapter it knows.  When there is no NVIDIA card the string
    is empty, and the program does nothing.
    """
    if sys.platform != "win32":
        return ""

    try:
        import winreg
    except ImportError:
        return ""

    key_path = "SYSTEM\\CurrentControlSet\\Control\\Video"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as root:
            index = 0
            while True:
                try:
                    adapter = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                found = _adapter_engines(winreg, root, adapter)
                if found:
                    return found
    except OSError as exc:
        log.warning("the graphics adapters could not be read: %s", exc)
    return ""


def _adapter_engines(winreg, root, adapter: str) -> str:
    """The engine GUID of one adapter, when it is an NVIDIA one."""
    try:
        with winreg.OpenKey(root, adapter) as adapter_key:
            index = 0
            while True:
                try:
                    entry = winreg.EnumKey(adapter_key, index)
                except OSError:
                    break
                index += 1
                found = _entry_engine(winreg, adapter_key, entry)
                if found:
                    return found
    except OSError:
        pass
    return ""


def _entry_engine(winreg, adapter_key, entry: str) -> str:
    """The engine GUID of one adapter entry, when it is an NVIDIA one."""
    try:
        with winreg.OpenKey(adapter_key, entry) as entry_key:
            try:
                value, _ = winreg.QueryValueEx(entry_key, "ProviderName")
            except OSError:
                return ""
            if "NVIDIA" not in str(value).upper():
                return ""
            try:
                with winreg.OpenKey(entry_key, "Settings") as settings:
                    stamp, _ = winreg.QueryValueEx(settings, "GUID")
                    return str(stamp)
            except OSError:
                return ""
    except OSError:
        return ""


def is_nvidia_present() -> bool:
    """True when an NVIDIA card is in the machine."""
    return bool(_engine_guid())


def _current_preference(guid: str) -> str:
    """The preference Windows has recorded for that engine."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _PREFERRED_GRAPHICS_KEY
        ) as key:
            value, _ = winreg.QueryValueEx(key, guid)
            return str(value)
    except (OSError, ImportError):
        return ""


def gpu_state() -> GPUState:
    """What the program can find out about the graphics card."""
    if sys.platform != "win32":
        return GPUState(message="只能在 Windows 上执行")

    guid = _engine_guid()
    if not guid:
        return GPUState(message="没有找到 NVIDIA 显卡，这一项不适用")

    current = _current_preference(guid)
    name = _PREFERENCE_NAMES.get(current, current)
    label = f"{name} ({current})" if current else "跟随系统"
    return GPUState(
        supported=True,
        applied=False,
        utilisation=gpu_load(),
        message="",
        preference=label,
    )


def set_gpu_preference(preference: str) -> GPUState:
    """Tell the driver which way to lean when the card is idle.

    *preference* is "saver" or "performance".  The value is written to
    the registry, where Windows reads it from.

    The result says whether the write went through.  It does not claim
    that the card fell asleep - that is the driver's business.
    """
    values = {"saver": PREFERENCE_SAVER, "performance": PREFERENCE_PERFORMANCE}
    wanted = preference.strip().lower()
    value = values.get(wanted)
    if value is None:
        # The option is checked before the platform, so the answer is
        # the same on every machine.
        return GPUState(message="没有这个选项")

    guid = _engine_guid()
    if not guid:
        return GPUState(message="没有找到 NVIDIA 显卡，这一项不适用")

    try:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            _PREFERRED_GRAPHICS_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, guid, 0, winreg.REG_SZ, value)
    except (OSError, ImportError) as exc:
        log.warning("the graphics preference could not be written: %s", exc)
        return GPUState(supported=True, message=f"写不进去：{exc}")

    log.info("the graphics preference was set to %s", wanted)
    return GPUState(
        supported=True,
        applied=True,
        utilisation=gpu_load(),
        message="",
        preference=f"{_PREFERENCE_NAMES[value]} ({value})",
    )


def gpu_load() -> int:
    """How busy the graphics card is, as a percentage.

    The number comes from the performance counters of the driver.  When
    they cannot be read - a card that is not NVIDIA, or a driver that
    does not publish them - the answer is -1, which means "not known".

    The counters are read through the performance data helper, which is
    part of Windows.  The library is looked up by name so that the
    module stays importable off Windows.
    """
    if sys.platform != "win32":
        return -1

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return -1

    try:
        pdh = ctypes.WinDLL("pdh.dll", use_last_error=True)
    except (AttributeError, OSError):
        return -1

    query = wintypes.HQUERY()
    counter = wintypes.HCOUNTER()
    try:
        if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
            return -1
        if pdh.PdhAddEnglishCounterW(query, "GPU Engine", ctypes.byref(counter)) != 0:
            pdh.PdhCloseQuery(query)
            return -1

        pdh.PdhCollectQueryData(query)
        # One sample is not enough to work out a rate, so the counters
        # are read twice with a short pause between them.
        import time

        time.sleep(0.2)
        pdh.PdhCollectQueryData(query)

        values = _read_counters(pdh, counter)
        pdh.PdhCloseQuery(query)
    except Exception as exc:  # noqa: BLE001 - the counters are optional
        log.debug("the graphics load could not be read: %s", exc)
        return -1

    return values


def _read_counters(pdh, counter) -> int:
    """The highest load of any engine, as a percentage."""
    try:
        import ctypes
        from ctypes import wintypes

        buffer = (ctypes.c_double * 8)()
        size = wintypes.DWORD(len(buffer))
        if (
            pdh.PdhGetFormattedCounterArrayW(
                counter,
                0x400,  # PDH_FMT_DOUBLE
                ctypes.byref(size),
                buffer,
            )
            != 0
        ):
            return -1
    except Exception:  # noqa: BLE001 - the counters are optional
        return -1

    best = -1.0
    for value in buffer:
        if value > best:
            best = value
    if best < 0:
        return -1
    return int(best)
