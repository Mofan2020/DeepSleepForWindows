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
*   The driver has to be asked.  Windows records the preference in
    the registry, and the program writes it there.

**Where the preference is written.**  Windows has two places for it:

``HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences\\DirectXUserGlobalSettings``
    The setting for the whole machine.  This is a list of settings
    separated by semicolons, for example
    ``GpuPreference=2;SwapEffectUpgradeEnable=0;``.  This is what
    the program writes.

``HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences\\<adapter LUID>``
    The setting for one graphics adapter, keyed by the LUID that the
    DXGI interface reports for it.

The program writes the machine-wide setting, because that is the one
that applies to every program and does not depend on a LUID that
changes with the display layout.  The per-adapter key is read when it
is there, so the interface can show what the adapter itself says.

**How the card is found.**  The adapters are read from the registry
under ``Control\\Video``.  Each adapter has a subkey per driver entry,
and the NVIDIA one has ``ProviderName`` set to ``NVIDIA``.  The old
version of this program looked for a ``Settings`` subkey with a
``GUID`` value in it - that key does not exist on current drivers, so
the card was never found and the interface always said the feature
does not apply.

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

#: Where Windows keeps the preference for the whole machine.
_PREFERRED_GRAPHICS_KEY = "SOFTWARE\\Microsoft\\DirectX\\UserGpuPreferences"

#: The name of the value that holds the machine-wide settings.
_GLOBAL_SETTINGS_VALUE = "DirectXUserGlobalSettings"

#: The name of the setting inside that value.
_GPU_PREFERENCE_SETTING = "GpuPreference"

#: The same name, in lower case.  The comparison against the settings
#: in the value is made in lower case, so both sides have to be.
_GPU_PREFERENCE_KEY = _GPU_PREFERENCE_SETTING.lower()


#: Where the display adapters are described.
_VIDEO_KEY = "SYSTEM\\CurrentControlSet\\Control\\Video"

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


# ------------------------------------------------------------- the adapter


def nvidia_adapters() -> list[str]:
    """The registry keys of the NVIDIA display adapters.

    Every adapter under ``Control\\Video`` has a subkey per driver
    entry.  The entry of an NVIDIA card has ``ProviderName`` set to
    ``NVIDIA``; the entries of the other cards do not.  The path of
    the entry is what identifies the card.

    The list is empty when there is no NVIDIA card, and also when the
    registry cannot be read - the two are told apart by the caller.
    """
    if sys.platform != "win32":
        return []

    try:
        import winreg
    except ImportError:
        return []

    found: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _VIDEO_KEY) as root:
            index = 0
            while True:
                try:
                    adapter = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(root, adapter) as adapter_key:
                        entry_index = 0
                        while True:
                            try:
                                entry = winreg.EnumKey(adapter_key, entry_index)
                            except OSError:
                                break
                            entry_index += 1
                            if _is_nvidia_entry(winreg, adapter_key, entry):
                                found.append(f"{_VIDEO_KEY}\\{adapter}\\{entry}")
                except OSError:
                    continue
    except OSError as exc:
        log.warning("the graphics adapters could not be read: %s", exc)
    return found


def _is_nvidia_entry(winreg, adapter_key, entry: str) -> bool:
    """True when the driver entry belongs to an NVIDIA card."""
    try:
        with winreg.OpenKey(adapter_key, entry) as entry_key:
            value, _ = winreg.QueryValueEx(entry_key, "ProviderName")
    except OSError:
        return False
    return "NVIDIA" in str(value).upper()


def adapter_description(path: str) -> str:
    """The name of the card, for example ``NVIDIA GeForce RTX 3060``."""
    if sys.platform != "win32":
        return ""
    try:
        import winreg
    except ImportError:
        return ""

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            value, _ = winreg.QueryValueEx(key, "DriverDesc")
    except OSError:
        return ""
    return str(value)


def is_nvidia_present() -> bool:
    """True when an NVIDIA card is in the machine."""
    return bool(nvidia_adapters())


# ------------------------------------------------------------ the settings


def _read_global_settings() -> str:
    """The machine-wide graphics settings, as one string."""
    try:
        import winreg
    except ImportError:
        return ""

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PREFERRED_GRAPHICS_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _GLOBAL_SETTINGS_VALUE)
    except (OSError, ImportError):
        return ""
    return str(value)


def _global_preference_from(settings: str) -> str:
    """The preference inside the machine-wide settings, or an empty string.

    The value holds several settings, separated by semicolons, for
    example ``GpuPreference=2;SwapEffectUpgradeEnable=0;``.  Only the
    one about the graphics preference is of interest here.
    """
    for part in settings.split(";"):
        part = part.strip()
        if not part.lower().startswith(f"{_GPU_PREFERENCE_KEY}="):
            continue
        value = part.split("=", 1)[1].strip()
        if value:
            return value
    return ""


def _global_preference() -> str:
    """The preference that is set for the whole machine, or an empty string."""
    return _global_preference_from(_read_global_settings())


def _write_global_preference(preference: str) -> None:
    """Set the preference for the whole machine.

    The other settings in the value are kept: the value holds more
    than one setting, and dropping them would change something the
    user did not ask about.
    """
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        _PREFERRED_GRAPHICS_KEY,
        0,
        winreg.KEY_SET_VALUE | winreg.KEY_READ,
    ) as key:
        try:
            current, _ = winreg.QueryValueEx(key, _GLOBAL_SETTINGS_VALUE)
            current = str(current)
        except OSError:
            current = ""

        parts = []
        for part in current.split(";"):
            part = part.strip()
            if not part:
                continue
            if part.lower().startswith(f"{_GPU_PREFERENCE_KEY}="):
                continue
            parts.append(part)

        parts.append(f"{_GPU_PREFERENCE_SETTING}={preference}")
        winreg.SetValueEx(
            key,
            _GLOBAL_SETTINGS_VALUE,
            0,
            winreg.REG_SZ,
            ";".join(parts) + ";",
        )


# -------------------------------------------------------------- the state


def gpu_state() -> GPUState:
    """What the program can find out about the graphics card."""
    if sys.platform != "win32":
        return GPUState(message="只能在 Windows 上执行")

    adapters = nvidia_adapters()
    if not adapters:
        return GPUState(message="没有找到 NVIDIA 显卡，这一项不适用")

    current = _global_preference()
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

    adapters = nvidia_adapters()
    if not adapters:
        return GPUState(message="没有找到 NVIDIA 显卡，这一项不适用")

    try:
        _write_global_preference(value)
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

    The number comes from the driver.  Two sources are tried, in this
    order:

    *   ``nvidia-smi``, which is part of the NVIDIA driver and answers
        in a few milliseconds.
    *   the performance counters of the driver, through the
        performance data helper.

    The counters are the second choice because the call that adds a
    counter crashes the process on some machines - not because of the
    counter that is asked for, but because the call itself goes wrong
    there.  A crash of the window is a lot worse than a load that is
    not known.

    When neither source answers, the result is -1, which means "not
    known".  The interface says that; it does not make a number up.
    """
    if sys.platform != "win32":
        return -1

    value = _load_from_nvidia_smi()
    if value >= 0:
        return value

    value = _load_from_counters()
    if value >= 0:
        return value

    return -1


def _load_from_nvidia_smi() -> int:
    """The load from ``nvidia-smi``, or -1 when it does not answer.

    The tool is part of the driver.  It prints the numbers it knows
    as a list of values separated by commas, and the first one is the
    load of the graphics engine.
    """
    try:
        import subprocess
    except ImportError:
        return -1

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            timeout=5,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001 - the tool may not be there
        log.debug("nvidia-smi could not be run: %s", exc)
        return -1

    if result.returncode != 0:
        return -1

    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = int(float(line.split(",")[0]))
        except (ValueError, IndexError):
            continue
        if 0 <= value <= 100:
            return value
    return -1


def _load_from_counters() -> int:
    """The load from the performance counters of the driver.

    This is the second choice.  The call that adds the counter can
    crash the process on some machines, so it is done in a way that
    does not take the program down with it when it fails.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return -1

    try:
        pdh = ctypes.WinDLL("pdh.dll", use_last_error=True)
    except (AttributeError, OSError):
        return -1

    # The performance data helper uses its own handle types.  They are
    # not in ``ctypes.wintypes`` - not on any platform - so they are
    # declared here.  They are pointers.
    #
    # The argument types have to be declared as well: without them
    # ctypes guesses, and a string argument that is passed as a
    # pointer to a pointer ends up as a wild address.
    query = ctypes.c_void_p()
    counter = ctypes.c_void_p()
    try:
        pdh.PdhOpenQueryW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        pdh.PdhOpenQueryW.restype = ctypes.c_long
        pdh.PdhAddEnglishCounterW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        pdh.PdhAddEnglishCounterW.restype = ctypes.c_long
        pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        pdh.PdhCollectQueryData.restype = ctypes.c_long
        pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
        pdh.PdhCloseQuery.restype = ctypes.c_long
        pdh.PdhGetFormattedCounterArrayW.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        pdh.PdhGetFormattedCounterArrayW.restype = ctypes.c_long
    except AttributeError:
        # An older Windows without the English counter names.
        return -1

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

        buffer = (ctypes.c_double * 32)()
        size = wintypes.DWORD(len(buffer))
        if (
            pdh.PdhGetFormattedCounterArrayW(
                counter,
                0x400,  # PDH_FMT_DOUBLE
                ctypes.byref(size),
                ctypes.cast(buffer, ctypes.c_void_p),
            )
            != 0
        ):
            return -1
    except Exception:  # noqa: BLE001 - the counters are optional
        return -1

    best = -1.0
    count = min(size.value, len(buffer))
    for index in range(count):
        value = buffer[index]
        if value > best:
            best = value
    if best < 0:
        return -1
    return int(best)
