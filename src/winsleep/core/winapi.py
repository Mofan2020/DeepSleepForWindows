"""The Win32 calls DeepSleep For Windows needs.

Everything that talks to Windows at the API level lives here, so the
rest of the program can stay plain Python.

The rule this module follows: **never parse the output of a command
line tool.**  Windows localizes its output, and the same command prints
different shapes on an English and a Chinese machine -
``powercfg /list`` writes ``Balanced   <guid>   AC Power Scheme`` on
one and ``电源方案 GUID: <guid>  (平衡)`` on the other.  Reading a
GUID out of either of those is a guess.  The API returns the GUID
itself, so the API is used.

Three groups of calls:

* ``powrprof`` - which power plan is active, and switching to another.
* ``kernel32`` - the "keep the system awake" execution state.
* ``user32`` - the idle time, and locking the screen.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

__all__ = [
    "GUID",
    "guid_from_string",
    "guid_to_string",
    "get_active_scheme",
    "read_friendly_name",
    "set_active_scheme",
    "idle_seconds",
    "keep_awake",
    "release_execution_state",
    "lock_workstation",
    "notification_area_ready",
    "notify_icon_present",
    "is_supported",
]


def is_supported() -> bool:
    """True when running on Windows."""
    return sys.platform == "win32"


# ------------------------------------------------------------------- GUIDs


class GUID(ctypes.Structure):
    """A Windows GUID.

    The layout is little-endian: a ``DWORD``, two ``WORD``s and eight
    bytes.  ``ctypes.c_ulong`` is four bytes on Windows, which is what
    makes this correct there.
    """

    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __str__(self) -> str:
        return guid_to_string(self)


def guid_from_string(text: str) -> GUID:
    """Build a GUID from its usual ``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`` form."""
    raw = text.strip().strip("{}").replace("-", "")
    if len(raw) != 32:
        raise ValueError(f"not a GUID: {text}")
    data1 = int(raw[0:8], 16)
    data2 = int(raw[8:12], 16)
    data3 = int(raw[12:16], 16)
    tail = bytes.fromhex(raw[16:32])
    return GUID(data1, data2, data3, (ctypes.c_ubyte * 8)(*tail))


def guid_to_string(guid: GUID) -> str:
    """The usual text form of a GUID."""
    tail = bytes(guid.Data4)
    return (
        f"{guid.Data1:08x}-{guid.Data2:04x}-{guid.Data3:04x}-"
        f"{tail[0:2].hex()}-{tail[2:8].hex()}"
    )


# ------------------------------------------------------------------- DLLs


def _dll(name: str):
    """Load a Windows DLL, or fail with a clear error off Windows.

    ``ctypes.WinDLL`` only exists on Windows, so it is looked up
    dynamically rather than referenced directly - that keeps this module
    importable on a non-Windows machine, which is what lets the test
    suite run on Linux and macOS.
    """
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError(f"{name} is only available on Windows")
    return loader(name, use_last_error=True)


def _powrprof():
    return _dll("powrprof")


def _kernel32():
    return _dll("kernel32")


def _user32():
    return _dll("user32")


# ------------------------------------------------------- power plan (GUID)


def get_active_scheme() -> GUID | None:
    """The GUID of the power plan that is active right now."""
    if not is_supported():
        return None
    try:
        powrprof = _powrprof()
        pointer = ctypes.POINTER(GUID)()
        result = powrprof.PowerGetActiveScheme(None, ctypes.byref(pointer))
        if result != 0:
            return None
        return pointer.contents
    except (OSError, AttributeError, ValueError):
        return None


def set_active_scheme(guid: GUID | str) -> bool:
    """Switch the active power plan.  True when it worked.

    The GUID is taken as a string or as a :class:`GUID`; a plan name is
    resolved by the caller, because only the registry knows the names.
    """
    if not is_supported():
        return False
    try:
        target = guid if isinstance(guid, GUID) else guid_from_string(guid)
    except ValueError:
        return False
    try:
        powrprof = _powrprof()
        result = powrprof.PowerSetActiveScheme(None, ctypes.byref(target))
        return result == 0
    except (OSError, AttributeError, ValueError):
        return False


def read_friendly_name(guid: str) -> str:
    """The name of a power plan, in the language the machine is set to.

    Windows stores the built-in plans' names as a resource reference -
    ``@%SystemRoot%\\system32\\powrprof.dll,-13,High performance`` - so
    the registry value is not the name the user sees.  The
    ``PowerReadFriendlyName`` call resolves that reference and returns
    the localized name.

    An empty string is returned when the name cannot be read.
    """
    if not is_supported():
        return ""
    try:
        scheme = guid_from_string(guid)
    except ValueError:
        return ""

    try:
        powrprof = _powrprof()
        buffer = ctypes.create_string_buffer(512)
        result = powrprof.PowerReadFriendlyName(
            None,  # the current user's power settings
            ctypes.byref(scheme),
            None,
            None,
            buffer,
            ctypes.sizeof(buffer),
        )
    except (OSError, AttributeError):
        return ""

    if result != 0:
        return ""
    return buffer.value.decode("utf-16-le", errors="replace").rstrip("\x00").strip()


# ------------------------------------------------------- execution state


#: The documented values of the ``EXECUTION_STATE`` enum.  ctypes does
#: not export them, so they are spelled out.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def set_execution_state(state: int) -> int:
    """Set the calling thread's execution state.  Returns the previous one.

    ``ES_CONTINUOUS`` on its own clears a previously held state, so
    calling it again with the same flags is how "keep awake" is
    refreshed.
    """
    if not is_supported():
        return 0
    try:
        return int(_kernel32().SetThreadExecutionState(state))
    except (OSError, AttributeError):
        return 0


def keep_awake(disable_display: bool = False) -> bool:
    """Hold a "the system must stay on" execution state.

    The state is dropped when the calling thread exits, and Windows
    resets it if it is not refreshed, so this has to be called again
    every few seconds.
    """
    state = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if disable_display:
        state |= ES_DISPLAY_REQUIRED
    previous = set_execution_state(state)
    return previous != 0


def release_execution_state() -> None:
    """Let go of the execution state so the machine may sleep again."""
    set_execution_state(ES_CONTINUOUS)


# ------------------------------------------------------------- idle time


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    """Seconds since the user last moved the mouse or pressed a key.

    Returns ``-1.0`` when the value cannot be read.  This is only
    meaningful when called from the user's own session: a process in
    session 0 has no keyboard and no mouse, so it always reports a
    huge idle time.
    """
    if not is_supported():
        return -1.0
    try:
        user32 = _user32()
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return -1.0
        # GetTickCount wraps after ~49 days.  The subtraction is done in
        # unsigned arithmetic, so the wrap is handled correctly for any
        # pair of values less than 49 days apart.
        now = _kernel32().GetTickCount()
        return max(0.0, (now - info.dwTime) / 1000.0)
    except (OSError, AttributeError, ValueError):
        return -1.0


# ----------------------------------------------------------------- lock


def lock_workstation() -> bool:
    """Lock the screen.  True when the call went through.

    This only works inside the user's own session.  From session 0 it
    fails, which is why the agent - not the daemon - does the locking.
    """
    if not is_supported():
        return False
    try:
        return bool(_user32().LockWorkStation())
    except (OSError, AttributeError):
        return False


# -------------------------------------------------------- notification area


def notification_area_ready() -> bool:
    """True when the taskbar notification area exists and can take icons.

    ``Shell_NotifyIcon(NIM_ADD, ...)`` returns zero when there is no
    notification area to add to, and it says nothing about why.  The
    two windows it needs are checked by name before an icon is added:

    * ``Shell_TrayWnd`` - the taskbar itself;
    * ``TrayNotifyWnd`` - the little window inside it that holds icons.

    Both are found with ``FindWindow`` rather than by asking anything
    else, so this works the same on every language of Windows.

    ``FindWindow`` returns a pointer-sized handle.  The result type has
    to be declared, otherwise ``ctypes`` assumes a C ``int`` and
    truncates the handle on 64-bit Windows - the non-zero check would
    then be right by accident, and the pointer unusable.
    """
    if not is_supported():
        return False

    try:
        user32 = _user32()
        find_window = user32.FindWindowW
        find_window.restype = wintypes.HWND
        find_window.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]

        find_child = user32.FindWindowExW
        find_child.restype = wintypes.HWND
        find_child.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
        ]

        taskbar = find_window("Shell_TrayWnd", None)
        if not taskbar:
            return False
        return bool(find_child(taskbar, None, "TrayNotifyWnd", None))
    except (OSError, AttributeError, ValueError):
        return False

class _NOTIFYICONIDENTIFIER(ctypes.Structure):
    """The handle and id of one icon, for ``Shell_NotifyIconGetRect``."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("guidItem", GUID),
    ]


class _ICONRECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def notify_icon_present(hwnd: int, uid: int = 0) -> bool | None:
    """True when that icon is in the notification area right now.

    ``None`` means the question cannot be answered on this system, which
    is different from "no" and must not be treated as one.

    **Why this is needed at all.**  ``Shell_NotifyIcon(NIM_ADD)`` returns
    a ``BOOL`` and pystray throws it away, so an icon that was never added
    looks exactly like one that was: the tray is empty, the program is
    happy, and nothing is logged.  Asking the shell afterwards is the only
    way to tell the two apart.

    **The id is zero.**  pystray passes ``hID=id(self)`` when it builds
    ``NOTIFYICONDATAW``, but the field is called ``uID``; ``ctypes`` takes
    an unknown keyword as a plain attribute and leaves the real field at
    its default.  Every pystray icon therefore has ``uID == 0``.  That is
    pystray's business, but it is what has to be asked for here.
    """
    if not is_supported():
        return None
    if not hwnd:
        return None

    try:
        shell = _dll("shell32")
        get_rect = shell.Shell_NotifyIconGetRect
        get_rect.restype = ctypes.c_long
        get_rect.argtypes = [
            ctypes.POINTER(_NOTIFYICONIDENTIFIER),
            ctypes.POINTER(_ICONRECT),
        ]
    except (OSError, AttributeError, ValueError):
        return None

    ident = _NOTIFYICONIDENTIFIER(
        cbSize=ctypes.sizeof(_NOTIFYICONIDENTIFIER),
        hWnd=hwnd,
        uID=uid,
        guidItem=GUID(),
    )
    rect = _ICONRECT()
    try:
        return get_rect(ctypes.byref(ident), ctypes.byref(rect)) == 0
    except (OSError, AttributeError, ValueError):
        return None



# ---------------------------------------------------------------- suspend


def suspend(hibernate: bool = False) -> bool:
    """Put the machine to sleep.  True when the call went through.

    The privilege to do this has to be taken first; a plain user
    process does not have it, and without it the call fails with no
    useful message.
    """
    if not is_supported():
        return False
    try:
        kernel32 = _kernel32()
        advapi32 = ctypes.windll.advapi32

        # SeShutdownPrivilege
        #
        # **这两个调用必须声明 argtypes。** 句柄从 ctypes 里读出来是
        # 普通 Python int（64 位能到 1e18），不声明就按 C 的 int 处理，
        # 直接 OverflowError: argument 1...
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL
        advapi32.AdjustTokenPrivileges.argtypes = [
            wintypes.HANDLE,
            wintypes.BOOL,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]

        privilege = wintypes.LUID()
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            0x0008 | 0x0020,  # TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY
            ctypes.byref(token),
        ):
            return False

        if not advapi32.LookupPrivilegeValueW(
            None, "SeShutdownPrivilege", ctypes.byref(privilege)
        ):
            return False

        entry = wintypes.LUID_AND_ATTRIBUTES()
        entry.Luid = privilege
        entry.Attributes = 0x00000002  # SE_PRIVILEGE_ENABLED

        if not advapi32.AdjustTokenPrivileges(
            token, False, ctypes.byref(entry), 0, None, None
        ):
            return False

        # The last argument has to be a critical section, so one is
        # made here.  It is never entered, the call only checks that
        # the pointer is valid.
        critical = ctypes.create_string_buffer(64)

        # 0 = hibernate, 1 = sleep
        state = 0 if hibernate else 1
        return bool(kernel32.SetSuspendState(state, 0, ctypes.byref(critical)))
    except (OSError, AttributeError, ValueError):
        return False
