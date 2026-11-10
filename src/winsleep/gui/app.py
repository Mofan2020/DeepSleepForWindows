"""The graphical front end.

A pywebview window showing the assets in this directory, wired to the
Python bridge.  The window is optional: the daemon does the actual work
and the interface is only a way to look at it and change the settings.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

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


def _ensure_admin() -> bool:
    """Make sure the program runs with administrator rights.

    The window changes the power plan and registers the scheduled tasks,
    so it needs administrator rights.  When it is started by double
    clicking the executable and does not have them, Windows is asked to
    give them - and this copy ends, letting the new one take over.

    The marker in the command line keeps the new copy from asking a
    second time, and it also keeps the program from looping when the
    user says "no" to the prompt.

    Returns True when the program may carry on.
    """
    from ..core import elevate

    if elevate.is_admin():
        return True

    if elevate.was_elevated():
        # The user said no to the prompt.  Carrying on without
        # administrator rights is better than asking forever.
        log.warning("the program is not running as administrator")
        return True

    log.info("administrator rights are needed; asking Windows for them")
    if not elevate.elevate_and_exit():
        log.warning("the elevation was refused; carrying on without it")
    return False


def run() -> int:
    """Open the window.  Returns the process exit code."""
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "the window needs pywebview, which is not part of this build"
        ) from exc

    # Set WebView2 user data folder BEFORE creating the window.
    # This prevents error 0x80070578 (E_INVALIDARG) when the default
    # location cannot be used.
    # Use environment variable which is read by WebView2 loader directly.
    try:
        from ..core import paths
        webview2_dir = paths.app_dir() / "webview2"
        webview2_dir.mkdir(parents=True, exist_ok=True)
        os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(webview2_dir)
        webview.settings["WEBVIEW2_USER_DATA_FOLDER"] = str(webview2_dir)
    except Exception:
        pass

    assets = _assets_dir()
    if not assets.is_dir():
        raise RuntimeError(f"the interface files are missing: {assets}")

    # The window needs administrator rights.  When it does not have
    # them, a second copy is started with them and this one ends.
    if not _ensure_admin():
        return 0

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
    try:
        webview.start()
    finally:
        instance.release()
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
