"""The icon in the notification area.

The program stays in the notification area of the taskbar, so it can
be reached without opening a window.  The menu holds the things that
are worth doing in one click: open the window, lock the screen, switch
the power plan, and leave the program.

**Why this file waits and watches.**

``pystray`` calls ``Shell_NotifyIcon(NIM_ADD, ...)`` and throws the
return value away.  That call returns zero when the notification area
is not ready yet, and nothing is raised, retried or logged - the
process keeps running with no icon and no way to tell why.  Two
situations hit it:

* the task starts on ``ONLOGON``, which fires before Explorer has
  drawn the notification area.  The tray loses that race once per
  login and the icon never appears for the rest of the session;
* Explorer restarts (a crash, or ``explorer.exe`` being restarted on
  purpose).  Every icon in the area is destroyed with it, and programs
  that do not listen for the ``TaskbarCreated`` broadcast never come
  back.

So the icon is not added until the area is there, and it is taken down
and put up again when the area goes away and comes back.  Both use the
public ``Icon.visible`` property, so no private parts of ``pystray``
are touched.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = [
    "TRAY_ICON_NAME",
    "icon_path",
    "make_icon",
    "run_tray",
    "wait_for_notification_area",
    "watch_notification_area",
]

log = logging.getLogger("winsleep.tray")

#: The name of the icon file inside the assets directory.
TRAY_ICON_NAME = "icon.png"

#: How long to wait for the notification area to appear before giving up
#: and adding the icon anyway.  A login can take a while on a slow
#: machine; a minute is long enough for that and short enough that a
#: broken Explorer is noticed.
_AREA_WAIT_SECONDS = 60.0

#: How often the area is checked, and the heartbeat written, once the
#: icon is up.
_WATCH_INTERVAL = 5.0


def icon_path() -> Path | None:
    """The icon of the program, when it can be found.

    The assets are unpacked into a temporary directory in a frozen
    build, so the place to look is the same one the interface uses.
    The icon is put into the assets directory of the package by the
    build, so that is where it is found.
    """
    from ..gui.app import _assets_dir

    try:
        assets = _assets_dir()
    except Exception:  # noqa: BLE001 - the path is known, but be safe
        return None

    if not assets.is_dir():
        return None

    for name in (TRAY_ICON_NAME, "icon.png", "icon.ico"):
        option = assets / name
        if option.is_file():
            return option
    return None


def make_icon() -> Any:
    """The icon for the notification area.

    A small blue circle is drawn when the shipped file cannot be read,
    so the program still shows something.
    """
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    path = icon_path()
    if path is not None:
        try:
            image = Image.open(path)
            # The tray wants a small square image.
            image = image.convert("RGBA")
            image.thumbnail((64, 64))
            return pystray.Icon("winsleep", image)
        except Exception as exc:  # noqa: BLE001 - a broken file is not fatal
            log.warning("the icon file could not be read: %s", exc)

    try:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, 60, 60), fill=(58, 130, 247, 255))
        draw.ellipse((20, 16, 44, 40), fill=(255, 255, 255, 235))
        return pystray.Icon("winsleep", image)
    except Exception as exc:  # noqa: BLE001
        log.warning("the tray icon could not be drawn: %s", exc)
        return None


def wait_for_notification_area(
    ready: Callable[[], bool],
    *,
    timeout: float = _AREA_WAIT_SECONDS,
    interval: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Wait until the notification area exists.  True when it did.

    The caller adds the icon either way - a taskbar that never shows up
    is worth a look, but it is not a reason to give up on the program.
    """
    if ready():
        return True

    deadline = clock() + timeout
    started = clock()
    while clock() < deadline:
        sleep(interval)
        if ready():
            waited = clock() - started
            log.info("the notification area appeared after %.1fs", waited)
            return True

    log.warning(
        "the notification area did not appear within %.0fs; "
        "adding the icon anyway, it may not be visible",
        timeout,
    )
    return False


def watch_notification_area(
    icon: Any,
    *,
    ready: Callable[[], bool],
    stop: threading.Event,
    beat: Callable[[], None] | None = None,
    interval: float = _WATCH_INTERVAL,
) -> None:
    """Keep the icon in the area, and keep the heartbeat fresh.

    Called on the main thread after the icon has been started in the
    background.  Two jobs, both of which only make sense over time:

    * *beat* is called every *interval* seconds.  The tray is a long
      running process, and a heartbeat written once at startup goes
      stale a few seconds later - at which point everything that asks
      "is the tray there?" answers "no" while the icon is on screen.
    * when the area goes away and comes back, the icon is removed and
      added again.  ``Icon.visible`` is the public way to do that; a
      ``False``/``True`` pair forces the ``NIM_DELETE`` + ``NIM_ADD``
      that Explorer needs to redraw its icons.

    Returns when *stop* is set.
    """
    was_ready = ready()

    while not stop.is_set():
        if beat is not None:
            try:
                beat()
            except Exception as exc:  # noqa: BLE001 - the icon outranks it
                log.debug("the heartbeat could not be written: %s", exc)

        now_ready = ready()
        if was_ready and not now_ready:
            log.info("the notification area went away")
        elif not was_ready and now_ready:
            # Explorer came back.  Every icon it held is gone, including
            # this one, so it has to be put back.
            log.info("the notification area came back; adding the icon again")
            try:
                icon.visible = False
                icon.visible = True
            except Exception as exc:  # noqa: BLE001
                log.warning("the icon could not be added again: %s", exc)
        was_ready = now_ready

        # The loop condition re-reads the event, so there is nothing to
        # break out of here.
        stop.wait(interval)


def run_tray(
    on_show: Callable[[], None],
    on_lock: Callable[[], None],
    on_quit: Callable[[], None],
    plan_choices: Callable[[], list[tuple[str, str]]] | None = None,
    on_switch: Callable[[str], None] | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> int:
    """Show the icon and stay in the notification area until it is left.

    *on_show* opens the window, *on_lock* locks the screen and
    *on_quit* ends the program.  *plan_choices* gives the power plans
    that are put into the menu, and *on_switch* switches to the one
    that was picked.  *heartbeat* is called while the icon is up, so
    the rest of the program can tell the tray is alive.
    """
    if sys.platform != "win32":
        log.info("the notification area is only available on Windows")
        return 1

    try:
        import pystray
    except ImportError:
        log.warning("pystray is not installed; the program cannot stay in the notification area")
        return 1

    from . import winapi

    icon = make_icon()
    if icon is None:
        log.warning("the icon for the notification area could not be made")
        return 1

    # Windows 11 hides tray icons by default in the overflow area; a
    # tooltip helps users find it.
    icon.title = "DeepSleep For Windows"

    def _quit(icon_obj: Any = None, item: Any = None) -> None:
        try:
            on_quit()
        finally:
            # The icon has to stop either way, or the program keeps
            # running with no way to get out of it.
            stop.set()
            if icon_obj is not None:
                icon_obj.stop()
            else:
                icon.stop()

    items: list[Any] = [
        pystray.MenuItem("打开主界面", lambda: on_show(), default=True),
        pystray.MenuItem("立即锁屏", lambda: on_lock()),
        pystray.Menu.SEPARATOR,
    ]

    if plan_choices is not None and on_switch is not None:
        try:
            plans = plan_choices()
        except Exception:  # noqa: BLE001 - the menu is still useful
            plans = []
        if plans:
            entries = []
            for name, guid in plans:
                entries.append(
                    pystray.MenuItem(
                        name,
                        lambda _icon=None, _item=None, target=guid: on_switch(target),
                    )
                )
            items.append(pystray.MenuItem("切换电源计划", pystray.Menu(*entries)))
            items.append(pystray.Menu.SEPARATOR)

    items.append(pystray.MenuItem("退出", _quit))
    icon.menu = pystray.Menu(*items)

    stop = threading.Event()

    # The area has to be there before the icon is added, or the add is
    # silently dropped (see the module docstring).
    wait_for_notification_area(winapi.notification_area_ready)

    # Started detached so the main thread can watch the area and write
    # the heartbeat.  A blocking ``icon.run()`` would leave no thread to
    # do either.
    icon.run_detached()
    log.info("the icon is in the notification area")

    try:
        watch_notification_area(
            icon,
            ready=winapi.notification_area_ready,
            stop=stop,
            beat=heartbeat,
        )
    finally:
        stop.set()
        try:
            icon.stop()
        except Exception:  # noqa: BLE001 - it is already going away
            pass

    return 0
