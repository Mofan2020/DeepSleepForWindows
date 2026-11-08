"""The graphical front end.

A pywebview window showing the assets in this directory, wired to the
Python bridge.  The window is optional: the daemon does the actual work
and the interface is only a way to look at it and change the settings.
"""

from __future__ import annotations

import logging
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
    """Open the window.  Returns the process exit code."""
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "the window needs pywebview, which is not part of this build"
        ) from exc

    assets = _assets_dir()
    if not assets.is_dir():
        raise RuntimeError(f"the interface files are missing: {assets}")

    webview.create_window(
        WINDOW_TITLE,
        str(assets / "index.html"),
        js_api=Bridge(),
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=(720, 560),
    )
    webview.start()
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
