"""The icon in the notification area.

The program stays in the notification area of the taskbar, so it can
be reached without opening a window.  The menu holds the things that
are worth doing in one click: open the window, lock the screen, switch
the power plan, and leave the program.

The icon itself is drawn with Pillow when the shipped file cannot be
read - a frozen build unpacks its data into a temporary directory, and
the path of the icon has to be found from there.

Pystray is imported inside a function, so the module can be imported
(and unit tested) on a machine that does not have it.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = [
    "TRAY_ICON_NAME",
    "icon_path",
    "make_icon",
    "run_tray",
]

log = logging.getLogger("winsleep.tray")

#: The name of the icon file inside the assets directory.
TRAY_ICON_NAME = "icon.png"


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


def run_tray(
    on_show: Callable[[], None],
    on_lock: Callable[[], None],
    on_quit: Callable[[], None],
    plan_choices: Callable[[], list[tuple[str, str]]] | None = None,
    on_switch: Callable[[str], None] | None = None,
) -> int:
    """Show the icon and stay in the notification area until it is left.

    *on_show* opens the window, *on_lock* locks the screen and
    *on_quit* ends the program.  *plan_choices* gives the power plans
    that are put into the menu, and *on_switch* switches to the one
    that was picked.
    """
    if sys.platform != "win32":
        log.info("the notification area is only available on Windows")
        return 1

    try:
        import pystray
    except ImportError:
        log.warning("pystray is not installed; the program cannot stay in the notification area")
        return 1

    icon = make_icon()
    if icon is None:
        return 1

    def _quit(icon_obj: Any = None, item: Any = None) -> None:
        try:
            on_quit()
        finally:
            # The icon has to stop either way, or the program keeps
            # running with no way to get out of it.
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
            items.append(
                pystray.MenuItem("切换电源计划", pystray.Menu(*entries))
            )
            items.append(pystray.Menu.SEPARATOR)

    items.append(pystray.MenuItem("退出", _quit))
    icon.menu = pystray.Menu(*items)
    icon.run()
    return 0

