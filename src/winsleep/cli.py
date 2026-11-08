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
import sys

from .core import config as config_mod
from .core import paths, winapi, winpower
from .version import APP_NAME, DISPLAY_NAME, __version__


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


def _cmd_gui(_args: argparse.Namespace) -> int:
    from .gui.app import run

    return run()


def _cmd_daemon(_args: argparse.Namespace) -> int:
    from .core.daemon import main as daemon_main

    return daemon_main()


def _cmd_agent(_args: argparse.Namespace) -> int:
    from .core.agent import main as agent_main

    return agent_main()


def _cmd_install(_args: argparse.Namespace) -> int:
    from .core import install

    ok, detail = install.install_all()
    print(detail)
    return 0 if ok else 1


def _cmd_uninstall(_args: argparse.Namespace) -> int:
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    _setup_logging(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
