# DeepSleep For Windows(Beta)

> **A Windows power and sleep manager, written in Python.** Idle-aware power plans, keep-awake, server mode, and rule-based automation.

![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2B%2011-blue.svg)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)
![Release](https://img.shields.io/badge/release/Mofan2020/DeepSleepForWindows)

- **Version**: 0.2.0
- **Language**: Python 3.11+, pywebview
- **License**: MIT

[中文文档](./README.zh-CN.md) · [Releases](https://github.com/Mofan2020/DeepSleepForWindows/releases)

## What it does

Windows ships with a power settings panel and a `powercfg` command line. Neither one
can answer the question people actually have: *switch my machine to the quiet plan
when I walk away, and back to the fast one when I sit down again.*

DeepSleep For Windows does that, and it lets you write your own rules on top.

- **Idle-aware power plans** — after N minutes without a keypress or a mouse move,
  and with the CPU below a threshold, switch to a quiet power plan. Come back and it
  switches straight back.
- **Game detection** — while a game is running, stay on the fast plan and hold the
  machine awake.
- **Server mode** — keep the machine awake indefinitely and lock the screen once
  nobody has been at the keyboard for a while.
- **Automation rules** — combine idle time, CPU load, a running process, a time
  window or a weekday, and run an action when they all hold.
- **Runs in the background** — two scheduled tasks, one for the daemon and one for
  the agent, so it keeps working with the window closed. You can install, start and
  stop them from the window.
- **Your own power plans** — copy a plan and change what is inside it: when the
  screen goes dark, when the machine sleeps, when it hibernates, how far the
  processor may drop.
- **A window, if you want one** — a small pywebview interface for the settings and
  the live state.

## How it works

Windows runs background services in a session that has no keyboard and no mouse.
A process there cannot tell whether anyone is at the computer - it always looks
idle. DeepSleep For Windows therefore runs in two halves:

```
DeepSleepForWindows daemon    session 0    power plans, rules, keep-awake
      ▲                            (loopback TCP, newline-delimited JSON)
      ▼
DeepSleepForWindows agent     session 1    reads the real idle time, locks the screen
```

The agent is the only part that talks to the Win32 API. The rule engine, the
config store and the power plan handling are plain Python. The system data comes
from the Windows API, the registry or `psutil` - never from the output of a
command line tool, which is localized and would read differently on a German
machine than on a Chinese one. There is no pywin32, no .NET, no PowerShell.

## Install

Download the zip from [Releases](https://github.com/Mofan2020/DeepSleepForWindows/releases).
Unpacking it gives you **one `DeepSleepForWindows.exe`**, nothing else. Put it
wherever you like and start it by double clicking it.

To register the background tasks, press **Install** in the window. You can also
do it from a terminal:

```bat
DeepSleepForWindows.exe install
```

That registers two scheduled tasks. The daemon starts at logon, and the agent
starts in your own session so it can read the real idle time.

To open the window: double click the exe, or

```bat
DeepSleepForWindows.exe gui
```

To remove everything:

```bat
DeepSleepForWindows.exe uninstall
```

The program has no console window, so double clicking it does not flash a black
box. When something goes wrong it shows a message box with the reason and the
place of the log file.

## Command line

```bat
DeepSleepForWindows gui                 open the window
DeepSleepForWindows status          print the current state as JSON
DeepSleepForWindows plans           list the power plans
DeepSleepForWindows switch "Balanced"   switch the power plan now
DeepSleepForWindows lock            lock the screen now
DeepSleepForWindows install             register the scheduled tasks
DeepSleepForWindows uninstall           remove them
```

## Configuration

The settings live in `%ProgramData%\DeepSleepForWindows\config.json` (or
`%LOCALAPPDATA%\DeepSleepForWindows\config.json` when running from source). The window edits
them; the file is plain JSON and can be edited by hand.

```json
{
  "idle_plan": "Power saver",
  "active_plan": "Balanced",
  "idle_seconds": 600,
  "idle_cpu_percent": 15.0,
  "game_processes": ["steam.exe", "yysls.exe"],
  "game_keeps_awake": true,
  "server_mode": false,
  "server_lock_seconds": 300,
  "keep_awake": false,
  "rules": [],
  "last_plan_guid": ""
}
```

### Rule conditions

| Condition | Value | Meaning |
| --- | --- | --- |
| `idle_at_least` | seconds | The user has been idle for at least this long. A unit is understood too: `10m`, `2h`, `1d`. |
| `idle_below` | seconds | The user has been idle for less than this long. A unit is understood too. |
| `cpu_below` | percent | Total CPU usage is below this. |
| `cpu_above` | percent | Total CPU usage is at or above this. |
| `process_running` | name | A process with this name is running. `.exe` is optional. |
| `time_window` | `HH:MM-HH:MM` | The local time is inside this window. Wraps past midnight. |
| `day_of_week` | `mon,tue,…` | Today is one of these days. |

### Rule actions

| Action | Value | Meaning |
| --- | --- | --- |
| `set_power_plan` | plan name | Switch the power plan. |
| `keep_awake` | `on` / `off` | Hold the machine awake. |
| `lock_screen` | – | Lock the workstation. |
| `run_command` | command line | Run a command. |

## Building from source

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

The Windows build is a PyInstaller bundle; see `main.spec`.

## Notes

- The idle time can only be read from inside the user's own session. That is why
  the agent exists, and why a tool that only runs as a service can never tell
  whether the machine is idle.
- Remote control software that injects synthetic input (AnyDesk, some other
  remote tools) keeps the system looking busy. DeepSleep For Windows's own rules can work
  around that by using a `process_running` condition instead of an idle one.

## Signing

The released `.exe` is signed with a code signing certificate and carries a
DigiCert RFC 3161 timestamp, so the signature stays valid after the
certificate expires.

The certificate is issued by a private CA (ServBay User CA), which Windows does
not trust by default, so the first run may say "Unknown publisher". Install the
root certificate from the repository to get rid of that:

```bat
certutil -addstore Root certificates\ServBay-User-CA-ECC-Root.crt
```

Have a look at the fingerprint before you install it:

```bat
certutil -dump certificates\ServBay-User-CA-ECC-Root.crt
```

## License

MIT. See [LICENSE](./LICENSE).
