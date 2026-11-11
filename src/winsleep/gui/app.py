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
