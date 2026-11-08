"""DeepSleep For Windows - a Windows power and sleep manager.

A small, dependency-light tool that keeps a Windows machine in the right
power state: awake when it should be, asleep when it should be, and off
the fast path when nothing is happening.

The layout mirrors the split that Deep Sleep uses on macOS:

    daemon (session 0)   background service: power plan, rules, keep-awake
    agent  (session 1)   reads real user idle time, locks the screen
    gui    (session 1)   the pywebview front end, opened on demand

Everything that can be written without touching the Win32 API is written
that way: the rule engine, the config store and the state machines are
plain Python and are unit tested off Windows.
"""

from .version import APP_NAME, DISPLAY_NAME, __version__

__all__ = ["APP_NAME", "DISPLAY_NAME", "__version__"]
