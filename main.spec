# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for DeepSleep For Windows.

The bundle is a one-folder build rather than a one-file build: the GUI
loads its HTML from disk, and keeping the assets as real files means
they can be read with ordinary file calls and are easy to replace.

Run it with:

    pyinstaller --clean --noconfirm main.spec
"""

from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve()

block_cipher = None

a = Analysis(
    ["src/winsleep/__main__.py"],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=[
        (str(PROJECT_ROOT / "src" / "winsleep" / "gui" / "assets"),
         "winsleep/gui/assets"),
    ],
    hiddenimports=[
        "webview",
        "winsleep.core.winapi",
        "psutil",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # The GUI does not use Qt or GTK; pywebview picks the Windows
        # backend on its own.  Leaving these out keeps the bundle small.
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "gtk3",
        "wx",
        "tkinter",
        "matplotlib",
        "numpy",
        "PIL",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DeepSleepForWindows",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # the command line has to work; the GUI is opened with "gui"
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DeepSleepForWindows",
)
