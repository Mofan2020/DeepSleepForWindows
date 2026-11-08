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
import ctypes
import json
import logging
import logging.handlers
import sys

from .core import config as config_mod
from .core import paths, winapi, winpower
from .version import APP_NAME, DISPLAY_NAME, __version__

log = logging.getLogger("winsleep.cli")


def _is_admin() -> bool:
    """Check if the current process has administrator privileges."""
    if sys.platform != "win32":
        return True  # Non-Windows doesn't need elevation
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def _elevate_and_exit(args: list[str]) -> None:
    """Re-launch the current executable with administrator privileges and exit.

    Uses ShellExecuteW with "runas" verb to trigger UAC prompt.
    """
    if sys.platform != "win32":
        return
    try:
        executable = sys.executable
        params = " ".join(f'"{a}"' if " " in a else a for a in args)
        # SW_SHOWNORMAL = 1
        ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
    except Exception:
        pass
    sys.exit(0)


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
    ]
    if sys.platform == "win32":
        # The Windows backend is loaded by name at runtime.
        wanted.append("webview.platforms.winforms")
        wanted.append("clr")
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
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("gui", help="open the window").set_defaults(func=_cmd_gui)
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
