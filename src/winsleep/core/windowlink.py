"""Asking the window that is already open to come forward.

A second copy of the program cannot open a window - one window is
enough, and two of them write the same settings.  What it can do is tell
the copy that owns the window to show it, which is what a double click
is asking for.

The channel is a loopback port, the same shape the daemon uses for the
agent: the owner listens on ``127.0.0.1``, writes the port into a file,
and answers one line of JSON per request.  A loopback port crosses the
session boundary between the window and the daemon without privileges,
which is why the program already uses one there.

The port file is what makes a dead owner detectable.  A stale file (or a
port nobody is listening on) means the lock in ``single`` is wrong, and
the caller is free to take it over.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from collections.abc import Callable
from pathlib import Path

__all__ = ["ShowRequestServer", "port_path", "request_show"]

log = logging.getLogger("winsleep.windowlink")

#: How long one show request may take.  The answer is a single line, so
#: anything slower than this means nobody is home.
REQUEST_TIMEOUT = 3.0

#: A copy that is started twice in a row can race with a window that is
#: still coming up.  The request is retried for this long before the
#: caller is told nobody answered.
RETRY_SECONDS = 6.0


def port_path() -> Path:
    """The file holding the port the window listens on."""
    from . import paths

    return paths.app_dir() / "window.port"


class ShowRequestServer:
    """Answers "show yourself" on a loopback port.

    The listener is started by the process that owns the window.  ``on_show``
    runs on a worker thread, so a slow window cannot hold up the reply.
    """

    def __init__(self, on_show: Callable[[], None], host: str = "127.0.0.1", port: int = 0) -> None:
        self._on_show = on_show
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._sock is not None:
            try:
                return int(self._sock.getsockname()[1])
            except OSError:
                pass
        return self._port

    def start(self) -> int:
        """Bind, listen, write the port file and serve.  Returns the port."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        self._sock.listen(4)
        self._port = self.port
        try:
            port_path().parent.mkdir(parents=True, exist_ok=True)
            port_path().write_text(str(self._port), encoding="utf-8")
        except OSError as exc:
            log.warning("the window port could not be written: %s", exc)
        self._thread = threading.Thread(
            target=self._serve, name="winsleep-windowlink", daemon=True
        )
        self._thread.start()
        return self._port

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        try:
            port_path().unlink()
        except OSError:
            pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                conn, _ = sock.accept()
            except OSError:
                if self._stop.is_set():
                    return
                continue
            try:
                self._handle(conn)
            except Exception:  # noqa: BLE001 - one bad request is not fatal
                log.exception("a show request failed")
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(REQUEST_TIMEOUT)
        try:
            raw = conn.recv(4096)
        except OSError:
            return
        if not raw:
            return
        try:
            message = json.loads(raw.decode("utf-8").splitlines()[0])
        except (ValueError, IndexError, UnicodeDecodeError):
            message = {}

        if str(message.get("kind", "")) != "show":
            self._reply(conn, {"ok": False, "message": "unknown request"})
            return

        # The window has to be shown on its own thread; pywebview marshals
        # to the interface thread, but asking it from here must not hold up
        # the answer.
        threading.Thread(target=self._on_show, name="winsleep-show", daemon=True).start()
        log.info("the running window was asked to come forward")
        self._reply(conn, {"ok": True})

    def _reply(self, conn: socket.socket, payload: dict) -> None:
        try:
            conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        except OSError:
            pass


def request_show(timeout: float = RETRY_SECONDS) -> bool:
    """Ask the copy that owns the window to show it.  True when it answered.

    ``False`` means nobody is listening: the owner is gone, or it is still
    starting up.  The caller decides what to do with that - ``single`` can
    tell a dead owner from a slow one.
    """
    import time

    deadline = time.monotonic() + timeout
    while True:
        try:
            port = int(port_path().read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            port = 0

        if port:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=REQUEST_TIMEOUT) as conn:
                    conn.sendall(json.dumps({"kind": "show"}).encode("utf-8") + b"\n")
                    conn.settimeout(REQUEST_TIMEOUT)
                    reply = conn.recv(4096)
                if reply:
                    answer = json.loads(reply.decode("utf-8").splitlines()[0])
                    return bool(answer.get("ok"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                log.debug("the running window did not answer: %s", exc)

        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)
