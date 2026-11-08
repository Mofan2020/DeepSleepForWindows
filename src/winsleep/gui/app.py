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

WINDOW_TITLE = "DeepSleep For Windows(Beta)"
WINDOW_SIZE = (940, 720)


def run() -> int:
    """Open the window.  Returns the process exit code."""
    try:
        import webview
    except ImportError:
        print(
            "pywebview is not installed.  Install it with: pip install pywebview",
            file=sys.stderr,
        )
        return 2

    if not ASSETS_DIR.is_dir():
        print(f"the interface files are missing: {ASSETS_DIR}", file=sys.stderr)
        return 2

    webview.create_window(
        WINDOW_TITLE,
        str(ASSETS_DIR / "index.html"),
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
