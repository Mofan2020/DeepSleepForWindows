"""Shared test setup.

The package lives under ``src/`` and is not installed into the
environment the tests run in, so the path is added here.  That keeps the
tests runnable straight from a checkout with nothing but the
development dependencies.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _forget_the_chosen_directory():
    """Make each test work out the program directory for itself.

    ``paths.app_dir()`` keeps the answer it worked out, because working
    it out means writing a probe file and the answer cannot change
    while the process runs.  A test that points ``WINSLEEP_HOME`` at a
    temporary directory would otherwise get whatever the test before
    it chose.
    """
    from winsleep.core import paths

    paths._CHOSEN = None
    yield
    paths._CHOSEN = None
