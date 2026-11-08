"""The agent: the half of DeepSleep For Windows that lives in the user's session.

This is the part that matters most and is the smallest.  A process in
session 0 has no keyboard and no mouse, so it cannot tell whether anyone
is at the computer.  The agent runs in the user's own session, reads the
real idle time, and reports it to the daemon over the loopback socket.

It does one more thing: when the daemon asks it to, it locks the screen.
Locking has to happen in the user's session too, otherwise the lock
applies to the wrong window station.

The agent is deliberately dumb.  It does not evaluate rules, it does not
touch the power plan, it does not keep a config of its own.  It reads one
number and writes one number.
"""

from __future__ import annotations

import json
import logging
import socket
import time

from . import paths, winapi

log = logging.getLogger("winsleep.agent")

#: How often the idle time is reported, in seconds.
REPORT_INTERVAL_SECONDS = 5.0


def _read_port() -> int:
    """The daemon's port, taken from the file it wrote."""
    try:
        raw = paths.socket_path().read_text(encoding="utf-8").strip()
        return int(raw)
    except (OSError, ValueError) as exc:
        log.warning("could not read the port file: %s", exc)
        return 0


def report_once(port: int, idle: float) -> dict | None:
    """Send one report to the daemon and return its answer.

    The answer may carry a ``lock`` flag, which means the daemon wants
    the screen locked.  The reply is returned so the caller can act on
    it; ``None`` means the daemon could not be reached.
    """
    payload = json.dumps({"kind": "report", "idle_seconds": idle}).encode("utf-8")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5.0) as conn:
            conn.sendall(payload + b"\n")
            conn.settimeout(5.0)
            reply = conn.recv(4096)
    except OSError as exc:
        log.debug("could not reach the daemon: %s", exc)
        return None
    try:
        return json.loads(reply.decode("utf-8").splitlines()[0])
    except (ValueError, IndexError):
        return None


def handle_reply(reply: dict | None) -> None:
    """Act on what the daemon said.

    A ``lock`` flag means the screen should be locked.  This has to
    happen here, in the user's own session: ``LockWorkStation`` called
    from session 0 does nothing.
    """
    if not reply:
        return
    if reply.get("lock"):
        if winapi.lock_workstation():
            log.info("the screen was locked")
        else:
            log.warning("could not lock the screen")


def ping(port: int) -> bool:
    """True when the daemon answers on that port."""
    payload = json.dumps({"kind": "ping"}).encode("utf-8")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5.0) as conn:
            conn.sendall(payload + b"\n")
            conn.settimeout(5.0)
            reply = conn.recv(4096)
            return b"pong" in reply
    except OSError:
        return False


def run_forever() -> None:
    """Report the idle time until the process is stopped."""
    port = 0
    while port == 0:
        port = _read_port()
        if port == 0:
            time.sleep(2.0)

    log.info("the agent is reporting to 127.0.0.1:%d", port)
    while True:
        idle = winapi.idle_seconds()
        if idle < 0:
            log.warning("could not read the idle time")
            idle = 0.0
        report_once(port, idle)
        time.sleep(REPORT_INTERVAL_SECONDS)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        run_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
