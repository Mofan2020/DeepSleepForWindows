"""The hand-over between two copies of the window.

Two problems are pinned here, both seen on a real machine:

* double clicking the program did nothing at all, because a copy that
  found the lock simply gave up.  Nothing was opened, nothing was said,
  and the log only had "there is already a window" in it;
* a lock left by a copy that was killed looked like a live window,
  because the pid had been reused by an unrelated process.  Then the
  program refused to start for ever.

The tray side is here too: an icon that is dropped while Explorer stays
put has to be put back, and nothing else in the program can notice it.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from winsleep.core import single, tray, windowlink

# ------------------------------------------------------------ the port file


@pytest.fixture()
def port_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep the port file inside the test, not in the real program folder."""
    target = tmp_path / "window.port"
    monkeypatch.setattr(windowlink, "port_path", lambda: target)
    return target


def test_the_port_file_is_written_and_removed(port_file: Path) -> None:
    server = windowlink.ShowRequestServer(lambda: None)
    port = server.start()
    try:
        assert port > 0
        assert port_file.read_text(encoding="utf-8").strip() == str(port)
    finally:
        server.stop()
    assert not port_file.exists()


def test_a_running_window_answers(port_file: Path) -> None:
    shown: list[str] = []
    server = windowlink.ShowRequestServer(lambda: shown.append("shown"))
    server.start()
    try:
        assert windowlink.request_show(timeout=2) is True
        deadline = time.monotonic() + 2
        while not shown and time.monotonic() < deadline:
            time.sleep(0.01)
        assert shown == ["shown"]
    finally:
        server.stop()


def test_nobody_answers_when_there_is_no_port_file(port_file: Path) -> None:
    assert windowlink.request_show(timeout=0.3) is False


def test_a_dead_port_is_not_an_answer(port_file: Path) -> None:
    """A stale file whose port nobody listens on means "gone", not "busy"."""
    port_file.write_text("9", encoding="utf-8")
    assert windowlink.request_show(timeout=0.3) is False


def test_a_request_that_is_not_show_is_refused(port_file: Path) -> None:
    shown: list[str] = []
    server = windowlink.ShowRequestServer(lambda: shown.append("shown"))
    server.start()
    try:
        import json
        import socket

        with socket.create_connection(("127.0.0.1", server.port), timeout=2) as conn:
            conn.sendall(json.dumps({"kind": "quit"}).encode("utf-8") + b"\n")
            reply = conn.recv(4096)
        assert b"false" in reply.lower()
        assert shown == []
    finally:
        server.stop()


# ------------------------------------------------- what a second copy does


class FakeInstance:
    """Stands in for ``single.SingleInstance``."""

    def __init__(self, first: bool, takes_over: bool = False) -> None:
        self.is_first = first
        self._takes_over = takes_over
        self.take_over_calls = 0

    def take_over(self) -> bool:
        self.take_over_calls += 1
        return self._takes_over


def test_the_first_copy_opens_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from winsleep.gui import app

    called: list[int] = []
    monkeypatch.setattr(windowlink, "request_show", lambda **kw: called.append(1) or True)

    assert app.claim_window(FakeInstance(True)) == "first"
    assert called == [], "the only copy asked somebody else to show a window"


def test_a_second_copy_asks_the_owner_and_does_not_open_a_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winsleep.gui import app

    monkeypatch.setattr(windowlink, "request_show", lambda **kw: True)
    instance = FakeInstance(False)

    assert app.claim_window(instance) == "shown"
    assert instance.take_over_calls == 0, "the lock was taken while its owner lived"


def test_a_lock_of_a_gone_owner_is_taken_over(monkeypatch: pytest.MonkeyPatch) -> None:
    from winsleep.gui import app

    monkeypatch.setattr(windowlink, "request_show", lambda **kw: False)
    instance = FakeInstance(False, takes_over=True)

    assert app.claim_window(instance) == "first"


def test_a_live_owner_that_does_not_answer_is_not_displaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winsleep.gui import app

    monkeypatch.setattr(windowlink, "request_show", lambda **kw: False)
    assert app.claim_window(FakeInstance(False, takes_over=False)) == "refused"


# -------------------------------------------------------------- the lock


def _write_lock(path: Path, text: str, age: float = 0.0) -> None:
    path.write_text(text, encoding="utf-8")
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))


def test_a_dead_owner_is_noticed_at_once(tmp_path: Path) -> None:
    """No minute of waiting: a copy that died must not block the next one."""
    path = tmp_path / "window.lock"
    _write_lock(path, "999999999")

    with single.SingleInstance(path) as instance:
        assert instance.is_first is True
        assert path.read_text(encoding="utf-8").splitlines()[0] == str(os.getpid())


def test_a_reused_pid_is_not_the_owner(tmp_path: Path) -> None:
    """The pid is alive, but it is not the process that wrote the lock."""
    path = tmp_path / "window.lock"
    _write_lock(path, f"{os.getpid()}\n{time.time() - 99999:.3f}")

    with single.SingleInstance(path) as instance:
        assert instance.is_first is True


def test_a_live_owner_keeps_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "window.lock"
    with single.SingleInstance(path) as first:
        assert first.is_first is True
        with single.SingleInstance(path) as second:
            assert second.is_first is False


def test_a_lock_of_an_older_version_is_understood(tmp_path: Path) -> None:
    """The file used to hold the pid alone; that still means something."""
    path = tmp_path / "window.lock"
    _write_lock(path, str(os.getpid()), age=3600)

    with single.SingleInstance(path) as instance:
        assert instance.is_first is False


def test_releasing_does_not_steal_a_lock_from_a_new_owner(tmp_path: Path) -> None:
    path = tmp_path / "window.lock"
    instance = single.SingleInstance(path)
    instance.acquire()
    assert instance.is_first is True

    # Another copy took over while this one was still running.
    path.write_text("4242", encoding="utf-8")
    instance.release()
    assert path.exists(), "the lock of the copy that took over was deleted"


def test_the_daemon_only_clears_a_lock_nobody_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "window.lock"
    monkeypatch.setattr(single, "lock_path", lambda: path)

    _write_lock(path, "999999999")
    assert single.clear_if_stale() is True
    assert not path.exists()

    with single.SingleInstance(path):
        assert single.clear_if_stale() is False
        assert path.exists(), "a live window's lock was deleted"


# -------------------------------------------------------------- the icon


class RecordingIcon:
    def __init__(self) -> None:
        self._visible = True
        self.log: list[bool | None] = []

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._visible = value
        self.log.append(value)


def test_an_icon_that_is_gone_is_put_back() -> None:
    """Explorer staying put does not mean the icon is still there."""
    icon = RecordingIcon()
    stop = threading.Event()
    polls = {"n": 0}

    def ready() -> bool:
        polls["n"] += 1
        if polls["n"] >= 4:
            stop.set()
        return True

    tray.watch_notification_area(
        icon,
        ready=ready,
        stop=stop,
        present=lambda: False,
        interval=0.01,
    )
    assert icon.log[:2] == [False, True], f"the icon was not put back: {icon.log}"


def test_an_icon_that_is_there_is_left_alone() -> None:
    icon = RecordingIcon()
    stop = threading.Event()
    polls = {"n": 0}

    def ready() -> bool:
        polls["n"] += 1
        if polls["n"] >= 4:
            stop.set()
        return True

    tray.watch_notification_area(
        icon,
        ready=ready,
        stop=stop,
        present=lambda: True,
        interval=0.01,
    )
    assert icon.log == []


def test_a_presence_check_that_cannot_answer_changes_nothing() -> None:
    """``None`` means "cannot tell", which must not be read as "gone"."""
    icon = RecordingIcon()
    stop = threading.Event()
    polls = {"n": 0}

    def ready() -> bool:
        polls["n"] += 1
        if polls["n"] >= 4:
            stop.set()
        return True

    tray.watch_notification_area(
        icon,
        ready=ready,
        stop=stop,
        present=lambda: None,
        interval=0.01,
    )
    assert icon.log == []
