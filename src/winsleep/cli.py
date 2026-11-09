"""The command line.

One entry point for everything:

    winsleep gui        open the window
    winsleep daemon     run the background half (the task starts this)
    winsleep agent      run the session half (the task starts this)
    winsleep install    register the scheduled tasks
    winsleep uninstall  remove them
    winsleep status     print what the machine is doing
    winsleep plans      list the power plans
    winsleep switch <plan>
                       switch the power plan now
    winsleep lock       lock the screen now
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys

from .core import config as config_mod
from .core import paths, winapi, winpower
from .version import APP_NAME, DISPLAY_NAME, __version__

log = logging.getLogger("winsleep.cli")


def _is_admin() -> bool:
    """Check if the current process has administrator privileges."""
    from .core import elevate

    return elevate.is_admin()


def _elevate_and_exit(args: list[str]) -> None:
    """Re-launch the current executable with administrator privileges and exit."""
    from .core import elevate

    elevate.elevate_and_exit(args)


def _attach_parent_console() -> None:
    """Use the console of the shell that started the program.

    The program is built without a console window, so a command run
    from a terminal has nowhere to print to.  Windows will give the
    console of the parent process to a program that asks for it, and
    the output then lands in the terminal the user typed the command
    into.  Started from the Explorer there is no console to take and
    nothing happens - which is the point.
    """
    if sys.platform != "win32":
        return
    # If the output was redirected to a file there is nothing to do;
    # the redirection is more specific than the parent console.
    if sys.stdout is not None:
        return
    try:
        import ctypes  # noqa: PLC0415 - Windows only

        kernel32 = ctypes.windll.kernel32
        # ATTACH_PARENT_PROCESS
        if not kernel32.AttachConsole(-1):
            return
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
    except Exception:  # noqa: BLE001 - no console is not a reason to stop
        pass


def _show_error(reason: str) -> None:
    """Tell the user what went wrong and where the log file is.

    There is no console window to print to, so a message box is the
    only way the user finds out that nothing happened.
    """
    text = (
        f"{DISPLAY_NAME} 启动失败。\n\n"
        f"原因：{reason}\n\n"
        f"详细信息在日志文件里：\n{paths.log_path()}"
    )
    if sys.platform != "win32":
        print(text, file=sys.stderr)
        return
    try:
        import ctypes  # noqa: PLC0415 - Windows only

        # MB_OK | MB_ICONERROR
        ctypes.windll.user32.MessageBoxW(None, text, DISPLAY_NAME, 0x10)
    except Exception:  # noqa: BLE001 - a failed dialog is the last resort
        pass


def _use_utf8_output() -> None:
    """Make sure printing the plan names cannot kill the program.

    The power plan names are the ones Windows shows, so they are in the
    language of the machine.  A console that cannot encode them would
    make the program die on the first print with a UnicodeEncodeError.

    The console keeps its own code page: a Chinese machine writes GBK
    into a GBK console and reads it back correctly, while forcing UTF-8
    there makes the characters unreadable.  So the encoding is only
    changed when the current one really cannot hold the text.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue

        encoding = getattr(stream, "encoding", None) or "utf-8"
        try:
            "平衡 高性能".encode(encoding)
        except (LookupError, UnicodeEncodeError):
            # The console cannot hold the names of the power plans.
            # UTF-8 with a replacement is better than dying.
            encoding = "utf-8"

        try:
            reconfigure(encoding=encoding, errors="replace")
        except (OSError, ValueError):
            # A stream that does not want to be reconfigured is not a
            # reason to refuse to run.
            pass


def _setup_logging(verbose: bool) -> None:
    root = logging.getLogger("winsleep")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    try:
        paths.app_dir().mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            paths.log_path(), maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except OSError as exc:
        print(f"warning: cannot write the log file: {exc}", file=sys.stderr)

    if verbose:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)



def _open_window() -> int:
    """Open the window, and tell the user if that does not work.

    There is no console window in a release build, so a failure has to
    be shown in a message box.  The reason and the place of the log
    file are in it, because a user who cannot see the window has no
    other way to find out what happened.
    """
    try:
        from .gui.app import run

        return run()
    except Exception as exc:  # noqa: BLE001 - the user has to be told
        log.exception("the window could not be opened")
        _show_error(str(exc))
        return 2


def _cmd_tray(_args: argparse.Namespace) -> int:
    """Stay in the notification area without showing a window."""
    from .core import tray, winapi, winpower

    def _show() -> None:
        # The window is opened by a second copy of the program, so that
        # only one window exists at a time.  It is started as its own
        # process: this one is the tray and has to stay alive, so it
        # cannot be the one that opens the window.
        _open_gui_copy()

    def _lock() -> None:
        winapi.lock_workstation()

    def _quit() -> None:
        log.info("the program was left from the notification area")

    def _plans() -> list[tuple[str, str]]:
        return winpower.list_plans()

    def _switch(guid: str) -> None:
        winpower.set_active_plan(guid)

    return tray.run_tray(_show, _lock, _quit, _plans, _switch)


def _open_gui_copy() -> None:
    """Start the window as a separate process.

    The tray has to keep running, so the window cannot be opened by
    this process - a program that opens a window and then ends would
    take the tray with it.
    """
    import subprocess

    try:
        if getattr(sys, "frozen", False):
            command = [sys.executable, "gui"]
        else:
            command = [sys.executable, "-m", "winsleep.cli", "gui"]
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(command, creationflags=flags, close_fds=True)
    except Exception as exc:  # noqa: BLE001 - the tray has to survive
        log.warning("the window could not be opened: %s", exc)



def _cmd_gui(_args: argparse.Namespace) -> int:
    return _open_window()


def _cmd_daemon(_args: argparse.Namespace) -> int:
    from .core.daemon import main as daemon_main

    return daemon_main()


def _cmd_agent(_args: argparse.Namespace) -> int:
    from .core.agent import main as agent_main

    return agent_main()


def _cmd_install(_args: argparse.Namespace) -> int:
    # Creating scheduled tasks requires administrator privileges.
    if not _is_admin():
        log.info("install needs administrator privileges; re-launching elevated")
        _elevate_and_exit(sys.argv)
        return 0  # the elevated instance will do the work

    from .core import install

    ok, detail = install.install_all()
    print(detail)
    return 0 if ok else 1


def _cmd_uninstall(_args: argparse.Namespace) -> int:
    # Removing scheduled tasks requires administrator privileges.
    if not _is_admin():
        log.info("uninstall needs administrator privileges; re-launching elevated")
        _elevate_and_exit(sys.argv)
        return 0

    from .core import install

    ok, detail = install.uninstall_all()
    print(detail)
    return 0 if ok else 1


def _cmd_status(_args: argparse.Namespace) -> int:
    config = config_mod.load(paths.config_path())
    active = winpower.active_plan()
    state = {
        "version": __version__,
        "name": DISPLAY_NAME,
        "active_plan": active[0] if active else None,
        "idle_plan": config.idle_plan,
        "idle_seconds": config.idle_seconds,
        "idle_cpu_percent": config.idle_cpu_percent,
        "game_processes": list(config.game_processes),
        "server_mode": config.server_mode,
        "keep_awake": config.keep_awake,
        "rules": len(config.rules),
        "user_idle_seconds": winapi.idle_seconds(),
    }
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


def _cmd_plans(_args: argparse.Namespace) -> int:
    active = winpower.active_plan()
    active_guid = active[1] if active else ""
    for name, guid in winpower.list_plans():
        mark = " *" if guid == active_guid else "  "
        print(f"{mark} {name}  [{guid}]")
    return 0


def _cmd_switch(args: argparse.Namespace) -> int:
    ok, detail = winpower.set_active_plan(args.plan)
    print(detail if ok else f"error: {detail}")
    return 0 if ok else 1


def _cmd_sleep(args: argparse.Namespace) -> int:
    """Put the machine to sleep now.

    The scheduled task of a timed sleep runs this, so the machine goes
    to sleep the same way no matter how it was triggered.
    """
    ok = winapi.suspend(hibernate=args.hibernate)
    if ok:
        print("the machine is going to sleep")
        return 0
    print("error: the machine could not be put to sleep")
    return 1


def _cmd_selftest(_args: argparse.Namespace) -> int:
    """Check that everything the program needs is really in the bundle.

    A frozen build can be missing a module without anything complaining
    until the moment the window is opened.  This command imports the
    pieces that matter and reports the ones that are not there, so the
    build can be checked before it is handed out.
    """
    import importlib

    wanted = [
        "webview",
        "psutil",
        "winsleep.core.winapi",
        "winsleep.core.winpower",
        "winsleep.core.daemon",
        "winsleep.core.agent",
        "winsleep.core.elevate",
        "winsleep.core.gpu",
        "winsleep.core.logs",
        "winsleep.core.ownplans",
        "winsleep.core.planrestore",
        "winsleep.core.single",
        "winsleep.core.tray",
    ]
    if sys.platform == "win32":
        # The Windows backend is loaded by name at runtime.
        wanted.append("webview.platforms.winforms")
        wanted.append("clr")
        # The notification area icon needs these as well.
        wanted.append("pystray")
        wanted.append("pystray._win32")
        wanted.append("PIL.Image")
    missing = []
    for name in wanted:
        try:
            importlib.import_module(name)
        except ImportError as exc:
            missing.append(f"{name} ({exc})")

    if missing:
        print("these modules are missing from the bundle:")
        for name in missing:
            print(f"  - {name}")
        return 1

    print("everything the program needs is in the bundle")
    return 0


def _cmd_update(_args: argparse.Namespace) -> int:
    """Check for a newer version and offer to install it.

    The check needs the network, so it is not done at every start.
    When a newer version is there, it is downloaded, checked, and the
    program replaces itself with it.
    """
    from .core import update

    print(f"current version: {update.current_version()}")
    print(f"releases:        {update.release_url()}")

    if not update.is_frozen():
        print("this is a run from the source, so it cannot replace itself")
        return 1

    ok, detail = _download_and_install_update()
    print(detail)
    return 0 if ok else 1


def _download_and_install_update() -> tuple[bool, str]:
    """Fetch the newest release and put it in place.

    The whole chain is here rather than in the update module, because
    it is the one place that talks to the network.
    """
    import json
    import urllib.request
    import zipfile

    from .core import update

    api = (
        f"https://api.github.com/repos/{update.RELEASE_OWNER}/"
        f"{update.RELEASE_REPO}/releases?per_page=30"
    )
    request = urllib.request.Request(
        api, headers={"User-Agent": "DeepSleepForWindows", "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:
            releases = json.loads(reply.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - the reason has to show
        return False, f"连不上 GitHub：{exc}"

    candidates = [
        item
        for item in releases
        if not item.get("draft") and update.parse_version(item.get("tag_name", ""))
    ]
    if not candidates:
        return False, "没有找到可用的发布版本"

    newest = max(candidates, key=lambda item: update.parse_version(item["tag_name"]))
    tag = newest["tag_name"]
    if not update.is_newer(tag):
        return True, f"已经是最新版本（{tag}）"

    asset = None
    for item in newest.get("assets", []):
        if item.get("name") == update.ASSET_NAME:
            asset = item
            break
    if asset is None:
        return False, f"{tag} 里没有 {update.ASSET_NAME}"

    work = update.work_directory()
    work.mkdir(parents=True, exist_ok=True)
    archive = work / update.ASSET_NAME
    try:
        request = urllib.request.Request(
            asset["browser_download_url"], headers={"User-Agent": "DeepSleepForWindows"}
        )
        with urllib.request.urlopen(request, timeout=300) as reply:
            with open(archive, "wb") as out:
                while True:
                    chunk = reply.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
    except Exception as exc:  # noqa: BLE001 - the reason has to show
        return False, f"下载失败：{exc}"

    try:
        source = update.extract_executable(archive, work / "new")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return False, f"压缩包有问题：{exc}"

    target = update.executable_path()
    from .core import install

    # Each task runs the program with its own sub command, so the
    # command line has to be built for each one of them.
    tasks = [
        (install.DAEMON_TASK, install.task_command("daemon")),
        (install.AGENT_TASK, install.task_command("agent")),
        (install.TRAY_TASK, install.task_command("tray")),
    ]
    script = update.write_updater_script(
        target=target,
        source=source,
        work_directory=work,
        pid=os.getpid(),
        version=tag,
        tasks=tasks,
    )
    if not update.start_updater(script):
        return False, "更新程序没能启动"

    return True, f"正在更新到 {tag}，程序会自己重启"


def _cmd_doctor(_args: argparse.Namespace) -> int:
    """Check that the program is set up the way it should be.

    The window can only work when the daemon is running and both of
    them look in the same directory.  This command says which of those
    is missing, so a broken setup can be seen without guessing.
    """
    from .core import install, paths
    from .gui.bridge import Bridge

    print(f"version:      {__version__}")
    print(f"directory:    {paths.app_dir()}")
    print(f"settings:     {paths.config_path()}")
    print(f"log:          {paths.log_path()}")

    print("")
    print("the background service:")
    try:
        status = install.service_status()
    except Exception as exc:  # noqa: BLE001 - the reason has to show
        print(f"  could not be read: {exc}")
        status = {}
    print(f"  installed:   {status.get('installed')}")
    print(f"  running:     {status.get('running')}")
    print(f"  agent:       {status.get('agent_running')}")

    print("")
    print("the daemon:")
    bridge = Bridge()
    state = bridge.get_state()
    print(f"  answering:   {state.get('daemon_running')}")
    print(f"  power plan:  {state.get('active_plan')}")
    print(f"  idle time:   {state.get('idle_seconds')} s")

    print("")
    print("the settings:")
    cfg = state.get("config") or {}
    print(f"  idle after:  {cfg.get('idle_seconds')} s")
    print(f"  cpu below:   {cfg.get('idle_cpu_percent')} %")
    print(f"  idle plan:   {cfg.get('idle_plan') or '(not set)'}")
    print(f"  active plan: {cfg.get('active_plan') or '(not set)'}")
    print(f"  server mode: {cfg.get('server_mode')}")
    print(f"  keep awake:  {cfg.get('keep_awake')}")

    print("")
    print(f"plans: {len(state.get('plans') or [])}")
    for name, guid in zip(
        state.get("plans") or [], state.get("plan_guids") or [], strict=False
    ):
        print(f"  {name}  {guid}")

    print("")
    ok = bool(state.get("daemon_running")) and bool(status.get("installed"))
    print("everything is in place" if ok else "something is missing - see above")
    return 0 if ok else 1


def _cmd_lock(_args: argparse.Namespace) -> int:
    ok = winapi.lock_workstation()
    print("the screen was locked" if ok else "error: could not lock the screen")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="DeepSleep For Windows - a power and sleep manager.",
    )
    parser.add_argument("--version", action="version", version=f"{DISPLAY_NAME} {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log to the console")
    # The marker is added to the command line by the elevation code.
    # It has to be accepted here, or the elevated copy refuses to
    # start with "unrecognized arguments" and dies before it does
    # anything at all - which looks exactly like a program that does
    # nothing and writes no log.
    from .core.elevate import ELEVATION_MARKER

    parser.add_argument(
        ELEVATION_MARKER,
        action="store_true",
        help=argparse.SUPPRESS,
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("gui", help="open the window").set_defaults(func=_cmd_gui)
    sub.add_parser(
        "tray", help="stay in the notification area without a window"
    ).set_defaults(func=_cmd_tray)
    sub.add_parser("daemon", help="run the background half").set_defaults(func=_cmd_daemon)
    sub.add_parser("agent", help="run the session half").set_defaults(func=_cmd_agent)
    sub.add_parser("install", help="register the scheduled tasks").set_defaults(func=_cmd_install)
    sub.add_parser("uninstall", help="remove the scheduled tasks").set_defaults(
        func=_cmd_uninstall
    )
    sub.add_parser("status", help="print the current state").set_defaults(func=_cmd_status)
    sub.add_parser("plans", help="list the power plans").set_defaults(func=_cmd_plans)

    switch = sub.add_parser("switch", help="switch the power plan")
    switch.add_argument("plan", help="the plan name or GUID")
    switch.set_defaults(func=_cmd_switch)

    sub.add_parser("lock", help="lock the screen").set_defaults(func=_cmd_lock)

    sub.add_parser(
        "selftest", help="check that the bundle is complete"
    ).set_defaults(func=_cmd_selftest)

    sub.add_parser(
        "doctor", help="check that everything is set up the way it should be"
    ).set_defaults(func=_cmd_doctor)

    sub.add_parser(
        "update", help="check for a newer version and install it"
    ).set_defaults(func=_cmd_update)

    sleep = sub.add_parser("sleep", help="put the machine to sleep now")
    sleep.add_argument(
        "--hibernate", action="store_true", help="hibernate instead of sleep"
    )
    sleep.set_defaults(func=_cmd_sleep)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "func", None):
        # Started without a command - that is what happens when the
        # program is double clicked, so the window is what opens.
        _setup_logging(args.verbose)
        return _open_window()

    # A command was given, so the output goes to the shell that started
    # the program.
    _attach_parent_console()
    # This has to happen before anything is printed, because the plan
    # names are in the language of the machine and the console may not
    # be able to encode them.
    _use_utf8_output()
    _setup_logging(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
