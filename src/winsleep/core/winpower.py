"""Power plans and running processes.

Two things live here: finding out which power plans exist, and finding
out which processes are running.

Both are done without parsing command line output.  Windows localizes
what its tools print - ``powercfg /list`` writes one shape on an
English machine and a different one on a Chinese one - and a regex
that has to cope with both is a bug waiting for the next language.  The
registry and the ``powrprof`` API answer the same questions with
structured data.

The registry is read with :mod:`winreg`, which is in the standard
library.  The processes are read with :mod:`psutil`, which is a normal
Python package.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable

from . import winapi

log = logging.getLogger("winsleep.power")

__all__ = [
    "active_plan",
    "list_plans",
    "set_active_plan",
    "plan_name_of",
    "plan_guid_of",
    "processes_running",
]

#: Where Windows keeps the power schemes.  Each subkey is one plan and
#: its ``FriendlyName`` value is the name the user sees.  The path goes
#: through ``User`` - ``Control\Power\PowerSchemes`` does not exist.
_POWER_SCHEMES_KEY = r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"

#: The plans Windows ships with.  Their GUIDs are the same on every
#: Windows 10 and 11 machine, whatever the language, so the names can
#: be written down here instead of being read out of a localized
#: resource.  The Chinese name comes first because that is what a
#: Chinese Windows shows in its own settings panel.
_BUILT_IN_PLANS = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": ("平衡", "Balanced"),
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": ("高性能", "High performance"),
    "a1841308-3541-4fab-bc81-f71556f20b4a": ("节能", "Power saver"),
    "e9a42b02-d5df-448d-aa00-03f14749eb61": ("卓越性能", "Ultimate Performance"),
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": ("卓越性能（叠加）", "High Performance Overlay"),
    "961cc777-2547-4f9d-8174-7d86181b8a7a": ("节能（叠加）", "Better Battery-life Overlay"),
    "ded574b5-45a0-4f42-8737-46345c09c238": ("最高性能（叠加）", "Max Performance Overlay"),
}


def list_plans() -> list[tuple[str, str]]:
    """Every power plan on the machine, as ``(name, guid)`` pairs.

    The names come from the ``powrprof`` API, so they are the localized
    names the user actually sees - "节能", "高性能", and so on, or "Power
    saver" on an English machine.
    """
    if sys.platform != "win32":
        return []

    # ``winreg`` only exists on Windows.  The import sits behind a
    # platform check so the module stays importable elsewhere, which is
    # what lets the test suite run on Linux and macOS.
    import winreg

    plans: list[tuple[str, str]] = []
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, _POWER_SCHEMES_KEY
        ) as key:
            index = 0
            while True:
                try:
                    guid = _enum_key_name(winreg, key, index)
                except OSError:
                    break
                index += 1
                if guid is None:
                    continue
                if not _looks_like_guid(guid):
                    continue
                plans.append((_plan_name(guid), guid.lower()))
    except OSError as exc:
        log.warning("could not read the power schemes: %s", exc)
        return []

    return plans


def _enum_key_name(winreg, key, index: int) -> str | None:
    """The name of the subkey at *index*.

    Python 3.13 changed ``winreg.EnumKey`` to return the name on its
    own; before that it returned a ``(name, class)`` pair.  Both are
    handled so the program works on every supported Python.
    """
    result = winreg.EnumKey(key, index)
    if isinstance(result, str):
        return result
    return result[0]


def _looks_like_guid(text: str) -> bool:
    stripped = text.strip().strip("{}")
    if len(stripped) != 36:
        return False
    return all(part.strip("0123456789abcdefABCDEF-") == "" for part in stripped)


def _plan_name(guid: str) -> str:
    """The name of one plan.

    Three sources, in order:

    1. A plan the user made themselves has a plain name in the
       registry - that name is theirs and is used as it is.
    2. The built-in plans have a resource reference in the registry.
       Their GUIDs are fixed on every Windows, so the names come from
       the table above rather than from a localized string.
    3. Anything else falls back to the GUID, which is still usable.
    """
    key = guid.lower()

    if sys.platform != "win32":
        return _BUILT_IN_PLANS.get(key, (guid, guid))[0]

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, _POWER_SCHEMES_KEY + "\\" + guid
        ) as plan_key:
            value, _ = winreg.QueryValueEx(plan_key, "FriendlyName")
            text = str(value).strip()
            if text and not text.startswith("@"):
                return text
    except OSError:
        pass

    built_in = _BUILT_IN_PLANS.get(key)
    if built_in:
        return built_in[0]
    return guid


def active_plan() -> tuple[str, str] | None:
    """The power plan in use, as ``(name, guid)``."""
    guid = winapi.get_active_scheme()
    if guid is None:
        return None
    guid_text = winapi.guid_to_string(guid)
    for name, candidate in list_plans():
        if candidate == guid_text.lower():
            return name, guid_text.lower()
    return guid_text, guid_text.lower()


def plan_name_of(guid: str) -> str:
    """The friendly name of a plan GUID, or the GUID itself."""
    wanted = guid.strip().lower()
    for name, candidate in list_plans():
        if candidate == wanted:
            return name
    return guid


def set_active_plan(guid_or_name: str) -> tuple[bool, str]:
    """Switch the active power plan.

    A GUID or a plan name is accepted; a name is resolved through the
    registry first.  Returns ``(ok, message)``.
    """
    target = guid_or_name.strip()
    if not target:
        return False, "no power plan given"

    resolved = plan_guid_of(target)
    if resolved is None:
        return False, f"unknown power plan: {target}"
    target = resolved

    if winapi.set_active_scheme(target):
        return True, target.lower()
    return False, f"could not switch to {target}"


def plan_guid_of(name: str) -> str | None:
    """The GUID of the plan with that name, or ``None``.

    Both the Chinese and the English name of a built-in plan are
    accepted, as is the GUID itself.  A plan the user made themselves
    is matched on the name they gave it.
    """
    wanted = name.strip()
    if not wanted:
        return None

    if _looks_like_guid(wanted):
        return wanted.lower()

    folded = wanted.casefold()
    for plan_name, guid in list_plans():
        if plan_name.casefold() == folded:
            return guid

    # The built-in plans can also be named in the other language.
    for guid, names in _BUILT_IN_PLANS.items():
        if any(part.casefold() == folded for part in names):
            if any(plan_guid == guid for _, plan_guid in list_plans()):
                return guid

    return None


def processes_running(names: Iterable[str]) -> set[str]:
    """The subset of *names* that currently has a running process.

    Matching ignores case and the ``.exe`` suffix, so ``"steam"`` and
    ``"steam.exe"`` mean the same thing.
    """
    wanted = {_strip_exe(n.strip().lower()) for n in names if n.strip()}
    wanted.discard("")
    if not wanted:
        return set()

    running: set[str] = set()
    try:
        import psutil
    except ImportError:
        log.warning("psutil is not installed; the process list is not available")
        return running

    try:
        for process in psutil.process_iter(["name"]):
            try:
                image = process.info.get("name") or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            candidate = _strip_exe(image.lower())
            if candidate in wanted:
                running.add(candidate)
    except Exception as exc:  # noqa: BLE001 - psutil raises many things
        log.warning("could not list the processes: %s", exc)
    return running


def _strip_exe(name: str) -> str:
    """The image name without its ``.exe`` suffix."""
    if name.endswith(".exe"):
        return name[:-4]
    return name
