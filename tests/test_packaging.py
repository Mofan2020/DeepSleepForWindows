"""The bundle has to contain what the program imports.

A frozen build that is missing a module does not fail while it is
being built - it fails later, on a machine that has nothing but the
executable.  These tests look at the build configuration itself, so
the mistake is caught before the release rather than after it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC = PROJECT_ROOT / "main.spec"


def _spec_source() -> str:
    return SPEC.read_text(encoding="utf-8")


def _spec_literal(name: str) -> list[str]:
    """The list that the spec file gives to ``name``."""
    tree = ast.parse(_spec_source())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != name:
                continue
            value = keyword.value
            if not isinstance(value, (ast.List, ast.Tuple)):
                continue
            items = []
            for element in value.elts:
                if isinstance(element, ast.Constant) and isinstance(
                    element.value, str
                ):
                    items.append(element.value)
            return items
    return []


def _hidden_imports() -> list[str]:
    """The hidden imports, including the ones built at run time."""
    source = _spec_source()
    found = re.findall(r'"([A-Za-z_][\w.]*)"', source)
    # The webview helper adds the pywebview modules at build time.
    return [name for name in found if "." in name or name in {"pystray", "clr"}]


def test_the_spec_file_is_there() -> None:
    assert SPEC.exists()


def test_nothing_that_is_hidden_is_excluded() -> None:
    """An exclude beats a hidden import.

    PyInstaller refuses to look for a module that is on the exclude
    list, so the hidden import is reported as "not found" and the
    module never lands in the bundle.  Pillow used to be on that list
    while the tray icon needs PIL.Image.
    """
    excluded = set(_spec_literal("excludes"))
    assert excluded, "the spec should still exclude the heavy GUI toolkits"

    for name in _hidden_imports():
        root = name.split(".")[0]
        assert root not in excluded, (
            f"{root} is excluded but {name} is a hidden import - "
            "the exclude wins and the module stays out of the bundle"
        )


def test_the_tray_icon_is_shipped() -> None:
    source = _spec_source()
    assert "icon.png" in source
    assert (PROJECT_ROOT / "assets" / "icon.png").exists()


def test_pystray_is_not_left_out() -> None:
    excluded = set(_spec_literal("excludes"))
    assert "pystray" not in excluded
    assert "pystray._win32" in _hidden_imports()


@pytest.mark.parametrize(
    "module",
    [
        "winsleep.core.elevate",
        "winsleep.core.gpu",
        "winsleep.core.logs",
        "winsleep.core.ownplans",
        "winsleep.core.planrestore",
        "winsleep.core.single",
        "winsleep.core.tray",
    ],
)
def test_the_new_modules_are_in_the_bundle(module: str) -> None:
    """The selftest command checks these at run time.

    Here they are checked against the list in the source, so a module
    that is added later cannot be forgotten there either.
    """
    source = (PROJECT_ROOT / "src" / "winsleep" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert module in source, f"{module} is not checked by the selftest"


def test_the_version_in_the_package_matches_the_program() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    assert version is not None

    source = (PROJECT_ROOT / "src" / "winsleep" / "version.py").read_text(
        encoding="utf-8"
    )
    other = re.search(r'__version__ = "([^"]+)"', source)
    assert other is not None

    assert version.group(1) == other.group(1), (
        "the version in pyproject.toml and the one the program prints are "
        "different - the package and the executable would disagree"
    )
