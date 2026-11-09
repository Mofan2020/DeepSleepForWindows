# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for DeepSleep For Windows.

The build is a **one-file** build: the result is a single
``DeepSleepForWindows.exe`` that can be copied anywhere and started by
double clicking it.  Nothing else has to sit next to it.

The price of one file is that the bundle is unpacked into a temporary
directory every time it starts, so the first start takes a moment
longer and the program needs a writable temporary directory.  Both are
acceptable for a tool that runs in the background and is started once
per logon.

Run it with:

    pyinstaller --clean --noconfirm main.spec
"""

from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve()

block_cipher = None


def _webview_modules():
    """Every module of pywebview, including the ones it imports late.

    pywebview picks its platform backend at runtime, so the backend
    modules are never imported by the code that PyInstaller can see.
    Without them the bundle starts and then says "no module named
    webview", which is exactly what a missing hidden import looks like
    from the outside.
    """
    try:
        from PyInstaller.utils.hooks import collect_submodules
    except ImportError:
        return ["webview"]
    modules = collect_submodules("webview")
    # pythonnet is what the Windows backend talks to the .NET runtime
    # through; it is loaded by name at runtime as well.
    return sorted(set(modules) | {"webview", "clr"})

a = Analysis(
    ["src/winsleep/__main__.py"],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=[
        (str(PROJECT_ROOT / "src" / "winsleep" / "gui" / "assets"),
         "winsleep/gui/assets"),
        # The icon of the notification area.  Without it the tray has
        # to draw one, which works but looks worse.
        (str(PROJECT_ROOT / "assets" / "icon.png"),
         "winsleep/gui/assets"),
    ],
    hiddenimports=_webview_modules() + [
        "winsleep.core.winapi",
        "psutil",
        # The notification area icon.  pystray picks its backend at
        # runtime, so the modules are never imported by the code that
        # PyInstaller can see.
        "pystray",
        "pystray._win32",
        "PIL.Image",
        "PIL.ImageDraw",
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
        # Pillow must NOT be excluded here.  The tray icon is drawn
        # with PIL.Image, and an exclude beats a hidden import - the
        # build then says "Hidden import 'PIL.Image' not found" and the
        # frozen program has no way to draw the icon.  Pillow only
        # pulls in a couple of megabytes, which is cheaper than a
        # broken tray.
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# One file: the binaries and the data go into the executable itself.
# There is no COLLECT step, so nothing is left next to the .exe.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DeepSleepForWindows",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # No console window: the program is started by double clicking it
    # and a black rectangle behind the window would look like a
    # mistake.  The command line commands still work because the
    # program attaches to the console of the shell that started it.
    console=False,
    # The program shows its own error dialog, with the reason and the
    # place of the log file, so the raw traceback window is not wanted.
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "icon.ico"),
)
