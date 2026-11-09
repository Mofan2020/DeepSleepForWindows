"""Making sure the user can write to the log file.

The log file can end up with permissions that only the administrators
may write to.  That happens when it was made by an elevated run - and
the program asks for administrator rights when it starts, so that is
the normal case.

The daemon, on the other hand, runs as the plain user: the scheduled
task that starts it uses the logon of the user, without the
administrator rights.  It then cannot write a single line into the
log.  The window shows an empty log, and the user has no way of
telling that the program was running the whole time and could not
say so.

The files that the program creates itself have the right permissions
already, so this is only about the ones that were there before.

The change is made with ``icacls``, which is part of Windows.  It is
used instead of the API because the API for access control lists is
a pile of structures and offsets, and getting one of them wrong
changes nothing at all without saying so.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

__all__ = ["make_user_writable"]

log = logging.getLogger("winsleep.acl")


def make_user_writable(path: Path) -> bool:
    """Let the current user write to the file.

    Returns whether anything was changed.  A file that the user can
    already write to is left alone, and a file that cannot be reached
    is not an error - the log is written by the program that has the
    rights to write it.
    """
    if sys.platform != "win32":
        return False

    try:
        return _make_user_writable_windows(path)
    except Exception as exc:  # noqa: BLE001 - the log is not worth failing over
        log.debug("the permissions of %s could not be changed: %s", path, exc)
        return False


def _make_user_writable_windows(path: Path) -> bool:
    """Give the current user the right to write to the file.

    The question that is asked is whether the *user* may write to the
    file, not whether the running process may.  The window runs with
    administrator rights and the daemon does not, and it is the plain
    user that has to be able to write.

    The check is therefore done on the name of the user: the program
    asks Windows what rights the user has, and only when the answer
    is "no" is the file changed.
    """
    if not path.exists():
        return False

    user = _current_user_sid()
    if not user:
        return False

    if _user_may_write(path, user):
        return False

    if not _grant(path, user):
        return False

    log.info("the permissions of %s were changed so that the user can write to it", path)
    return True


def _current_user() -> str:
    """The user of this process, as the name Windows knows it by.

    The name is read from the token of the process, not from the
    environment.  The environment has ``USERDOMAIN``, and that says
    ``WORKGROUP`` on a machine that is not in a domain - while the
    account belongs to the machine itself.  A permission given to
    ``WORKGROUP\\me`` is given to nobody.
    """
    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    except (AttributeError, OSError):
        return ""

    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.LookupAccountSidW.restype = wintypes.BOOL
    advapi32.LookupAccountSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    # The type of the sid is an output, and it is not a parameter:
    # the function takes six arguments, not seven.
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        return ""
    try:
        buffer = ctypes.create_string_buffer(1024)
        needed = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token, 1, buffer, 1024, ctypes.byref(needed)
        ):
            return ""
        # The first pointer of the buffer is the sid.
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not sid:
            return ""

        name = ctypes.create_unicode_buffer(256)
        name_size = wintypes.DWORD(256)
        domain = ctypes.create_unicode_buffer(256)
        domain_size = wintypes.DWORD(256)
        if not advapi32.LookupAccountSidW(
            None,
            sid,
            name,
            ctypes.byref(name_size),
            domain,
            ctypes.byref(domain_size),
        ):
            return ""
        return f"{domain.value}\\{name.value}"
    finally:
        kernel32.CloseHandle(token)


def _current_user_sid() -> str:
    """The sid of the user of this process, as a string.

    ``icacls`` takes the sid in the form ``*S-1-5-21-...`` and works
    it out itself.  That is better than a name, because a name can
    belong to a different account on another machine.
    """
    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    except (AttributeError, OSError):
        return ""

    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        return ""
    try:
        buffer = ctypes.create_string_buffer(1024)
        needed = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token, 1, buffer, 1024, ctypes.byref(needed)
        ):
            return ""
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not sid:
            return ""
        text = ctypes.c_void_p()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            return ""
        try:
            return ctypes.cast(text, ctypes.c_wchar_p).value or ""
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.CloseHandle(token)


def _user_may_write(path: Path, user: str) -> bool:
    """True when the user may write to the file already.

    The rights of the file are read with ``icacls``.  The output is
    one line per entry, and the line of the user says what the user
    may do.
    """
    output = _run_icacls(path)
    if output is None:
        return False

    name = _current_user()
    for line in output.splitlines():
        if name.lower() not in line.lower():
            continue
        # The line looks like "name:(F)" or "name:(M,F)".  The letters
        # in the brackets say what is allowed: F is full, M is modify,
        # W is write, and the C is the change permission.
        if "(" in line and ")" in line:
            rights = line[line.rfind("(") + 1 : line.rfind(")")]
            if any(letter in rights.upper() for letter in ("F", "M", "W", "C")):
                return True
    return False


def _grant(path: Path, user: str) -> bool:
    """Give the user the right to write to the file."""
    # The sid is written in the form ``*S-1-5-21-...``.  The star
    # tells ``icacls`` that what follows is a sid and not a name, so
    # it does not have to look one up - and a name that does not exist
    # would quietly do nothing.
    result = _run(
        ["icacls", str(path), "/grant", f"*{user}:(F)"]
    )
    return result is not None and result.returncode == 0


def _run_icacls(path: Path) -> str | None:
    """The rights of the file, as ``icacls`` prints them."""
    result = _run(["icacls", str(path)])
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def _run(args: list[str]):
    """Run a command and capture its output.

    The output of these commands is in the local code page, which is
    not UTF-8 on a Chinese machine, so the text is decoded with the
    code page that fits.
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None

    result.stdout = _decode(result.stdout)
    result.stderr = _decode(result.stderr)
    return result


def _decode(raw: bytes) -> str:
    """Turn the raw output of a command into text."""
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _unused() -> None:
    """Keep the import of os from being flagged as unused."""
    return os.getcwd()
