"""Allow the frozen build to be started as ``DeepSleep For Windows.exe``.

PyInstaller needs a real script as the entry point.  This one hands
over to :func:`winsleep.cli.main`, so the same executable serves the
window, the daemon, the agent and the command line.
"""

from __future__ import annotations

import multiprocessing
import sys

from winsleep.cli import main

if __name__ == "__main__":
    # A frozen build that starts child processes must not restart
    # itself; without this the agent would be launched twice.
    multiprocessing.freeze_support()
    sys.exit(main())
