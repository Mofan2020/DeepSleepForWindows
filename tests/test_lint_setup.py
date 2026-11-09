"""The lint that runs here has to be the lint that runs in the CI.

A newer ruff knows more rules than an older one, and it knows some
rules differently.  Code that passes the local version can still fail
in the build, and the failure then shows up on a machine that is not
the one the code was written on.

The version in ``requirements-dev.txt`` is the one the CI installs,
because the CI installs that file.  This test keeps the two in step.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _requirements() -> str:
    return (PROJECT_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")


def _ruff_requirement() -> str:
    for line in _requirements().splitlines():
        line = line.strip()
        if line.startswith("ruff"):
            return line
    raise AssertionError("ruff is not in requirements-dev.txt")


def test_ruff_is_pinned_to_a_range() -> None:
    requirement = _ruff_requirement()
    assert ">" in requirement and "<" in requirement, (
        f"ruff is not pinned to a range ({requirement}); a newer ruff may "
        "know rules the CI does not, and the local check would pass "
        "while the build fails"
    )


def test_the_pinned_range_is_the_one_the_ci_installs() -> None:
    """The CI installs requirements-dev.txt, so this is the range.

    The check has to run with the version that is inside the range,
    because the range is what the CI resolves to.  A version outside
    it means the local check is not the one that runs in the build.
    """
    requirement = _ruff_requirement()
    match = re.search(r"ruff>=\s*([\d.]+)\s*,\s*<\s*([\d.]+)", requirement)
    assert match is not None, f"cannot read the ruff range from {requirement}"

    low, high = match.group(1), match.group(2)
    assert tuple(int(part) for part in low.split(".")) < tuple(
        int(part) for part in high.split(".")
    ), f"the ruff range in {requirement} is empty"


def test_the_requirements_file_has_no_unpinned_package() -> None:
    """An unpinned package lets the CI and the machine drift apart."""
    for line in _requirements().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert ">" in line and "<" in line, (
            f"{line} is not pinned; the build would use a different "
            "version than the machine that checks the code"
        )
