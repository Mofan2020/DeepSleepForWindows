"""Shared test setup.

The package lives under ``src/`` and is not installed into the
environment the tests run in, so the path is added here.  That keeps the
tests runnable straight from a checkout with nothing but the
development dependencies.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
