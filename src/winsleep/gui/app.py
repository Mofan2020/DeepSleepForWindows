"""The graphical front end.

A pywebview window showing the assets in this directory, wired to the
Python bridge.  The window is optional: the daemon does the actual work
and the interface is only a way to look at it and change the settings.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .bridge import Bridge

log = logging.getLogger("winsleep.gui")

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _assets_dir() -> Path:
    """Where the interface files are.

    In a one-file build everything is unpacked into a temporary
    directory first, and the module path points into it, so the usual
    ``next to the file`` rule still works.  The fallback covers the
    case where the module itself was kept inside the archive and only
    the data was unpacked.
    """
    if ASSETS_DIR.is_dir():
        return ASSETS_DIR
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "winsleep" / "gui" / "assets"
        if candidate.is_dir():
            return candidate
    return ASSETS_DIR

WINDOW_TITLE = "DeepSleep For Windows(Beta)"
WINDOW_SIZE = (940, 720)


def make_close_handler(window: Any) -> Callable[[], bool]:
    """Closing the window hides it instead of ending the program.

    pywebview 的 closing 事件里**返回 False 才会取消关闭**（返回 None
    不算）。窗口藏起来，托盘图标还在，点一下就能回来。

    藏不起来的时候返回 True，让关闭照常发生——宁可关掉，也不要留下
    一个既没有窗口又没有图标的进程。
    """

    def _hide_instead_of_closing() -> bool:
        try:
            window.hide()
        except Exception as exc:  # noqa: BLE001 - never block a close
            log.debug("the window could not be hidden: %s", exc)
            return True
        return False

    return _hide_instead_of_closing


def _start_tray(window: object) -> object:
    """Put the icon in the notification area, owned by this process.

    **图标挂在窗口进程里。** 以前它是个独立的计划任务进程，只在登录
    那一刻启动，装完之后不注销就永远没有图标；就算起来了，那个进程
    跟窗口也是两回事，「打开主界面」只好再拉一个新进程。

    现在跟窗口同一个进程：关掉窗口只是把窗口收起来，图标还在，点一下
    就回来。返回的东西有 ``stop()``，退出时用它把图标摘掉。
    """
    import time

    from ..core import config as config_mod
    from ..core import install, paths, tray, winapi, winpower

    def _show() -> None:
        try:
            window.show()
            window.restore()
        except Exception as exc:  # noqa: BLE001 - the icon stays useful
            log.debug("the window could not be shown: %s", exc)

    def _lock() -> None:
        winapi.lock_workstation()

    def _override(minutes: int) -> None:
        """The deadline goes into the config; the daemon reads it."""
        config = config_mod.load(paths.config_path())
        config.keep_awake_until = (
            0.0 if minutes <= 0 else time.time() + minutes * 60
        )
        config_mod.save(config, paths.config_path())

    def _plans() -> list[tuple[str, str]]:
        return winpower.list_plans()

    def _switch(guid: str) -> None:
        winpower.set_active_plan(guid)

    def _quit() -> None:
        try:
            window.destroy()
        except Exception as exc:  # noqa: BLE001 - ending anyway
            log.debug("the window could not be closed: %s", exc)

    def _beat() -> None:
        install.write_heartbeat(install.TRAY_HEARTBEAT)

    return tray.start_icon(
        on_show=_show,
        on_lock=_lock,
        on_quit=_quit,
        plan_choices=_plans,
        on_switch=_switch,
        heartbeat=_beat,
        on_override=_override,
    )


def run() -> int:
    """Open the window.  Returns the process exit code.

    The window does **not** need administrator rights.  It talks to the
    daemon — which runs as SYSTEM via the scheduled task — for anything
    that does, like changing the power plan.  Install and uninstall are
    done through an elevated helper, not by elevating the window itself.

    This is what fixes the "double-click and nothing happens" problem:
    the old code asked for elevation before the window could open, and
    when the user said no (or the elevation silently failed) there was
    no window at all.
    """

    # Set WebView2 user data folder BEFORE importing webview.
    # This prevents error 0x80070578 (E_INVALIDARG) when the default
    # location cannot be used.  The env var must be set before webview
    # is imported because WebView2 initializes its environment early.
    try:
        from ..core import paths
        webview2_dir = paths.app_dir() / "webview2"
        webview2_dir.mkdir(parents=True, exist_ok=True)
        os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(webview2_dir)
    except Exception:
        pass

    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "the window needs pywebview, which is not part of this build"
        ) from exc

    # Also set in webview settings as fallback
    try:
        webview.settings["WEBVIEW2_USER_DATA_FOLDER"] = str(webview2_dir)
    except Exception:
        pass

    assets = _assets_dir()
    if not assets.is_dir():
        raise RuntimeError(f"the interface files are missing: {assets}")

    # Only one copy of the window may be open.  When there already is
    # one, this copy gives up instead of opening a second window.
    from ..core import single

    instance = single.SingleInstance()
    instance.acquire()
    if not instance.is_first:
        log.info("there is already a window; this copy gives up")
        return 0

    bridge = Bridge()
    window = webview.create_window(
        WINDOW_TITLE,
        str(assets / "index.html"),
        js_api=bridge,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=(720, 560),
    )
    # The bridge needs the window to open the file dialog, and the
    # window only exists after it has been created.
    bridge.attach_window(window)
    # 老版本留下的独立托盘任务要清掉，不然登录时它会再挂一个图标出来。
    try:
        from ..core import install as install_mod

        install_mod.retire_tray_task()
    except Exception as exc:  # noqa: BLE001 - the window outranks the old task
        log.debug("the old tray task could not be retired: %s", exc)

    tray_icon = None
    try:
        tray_icon = _start_tray(window)
    except Exception as exc:  # noqa: BLE001 - the window still opens
        log.warning("the notification area icon is not available: %s", exc)

    if tray_icon is not None:
        # 有图标的时候，关窗口只是收起来。没有这一条，用户一关窗口进程
        # 就结束，图标跟着没了——那正是这一版要修的毛病。
        window.events.closing += make_close_handler(window)

    try:
        webview.start()
    finally:
        if tray_icon is not None:
            try:
                tray_icon.stop()
            except Exception:  # noqa: BLE001 - ending anyway
                pass
        instance.release()
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
