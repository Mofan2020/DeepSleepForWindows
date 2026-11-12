"""The icon in the notification area, and the two races it loses.

``pystray`` drops the return value of ``Shell_NotifyIcon``, so an icon
that was never added looks exactly like one that was.  These tests pin
the two situations where that happens: the area is not up yet at login,
and Explorer restarts and takes every icon with it.

The tests do not need ``pystray`` or Windows: the waiting and watching
are plain functions that take a "is the area there?" callable and an
object with a ``visible`` property.
"""

from __future__ import annotations

import threading

import pytest

from winsleep.core import tray, winapi

# --------------------------------------------------------------- fakes


class FakeIcon:
    """Stands in for ``pystray.Icon``.

    Only ``visible`` matters here - that is the public property the
    watchdog drives to make Explorer redraw its icons.
    """

    def __init__(self) -> None:
        self._visible = True
        self.log: list[object] = []

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._visible = value
        self.log.append(value)


def stopper(sequence: list[bool], stop: threading.Event, after: int = 5):
    """A ready() that answers from *sequence* and sets *stop* after *after* polls.

    Every test ends the loop through ready(), never through the
    heartbeat.  Stopping it from the heartbeat means a mutation that
    removes the heartbeat also removes the only thing that ends the
    loop - the test then hangs instead of failing, and a hang does not
    say which assertion broke.
    """
    answers = list(sequence)
    polls = {"n": 0}

    def ready() -> bool:
        polls["n"] += 1
        if polls["n"] >= after:
            stop.set()
        if len(answers) > 1:
            return answers.pop(0)
        return answers[0]

    return ready


def scripted_ready(sequence: list[bool]):
    """A ready() that walks a fixed script, then repeats the last answer."""
    answers = list(sequence)

    def ready() -> bool:
        if len(answers) > 1:
            return answers.pop(0)
        return answers[0]

    return ready


# ----------------------------------------------------- waiting for the area


def test_waits_returns_true_when_already_there() -> None:
    assert tray.wait_for_notification_area(lambda: True) is True


def test_waits_until_it_appears() -> None:
    ready = scripted_ready([False, False, True])
    assert tray.wait_for_notification_area(ready, timeout=10, sleep=lambda _s: None) is True


def test_gives_up_after_timeout() -> None:
    """Polling stops at the deadline instead of spinning forever.

    Time only moves when the loop sleeps - that is what sleeping means.
    Advancing the clock on every read would make the loop end for a
    reason that does not exist on a real machine.
    """
    calls = {"n": 0}

    def ready() -> bool:
        calls["n"] += 1
        return False

    state = {"t": 0.0}

    def clock() -> float:
        return state["t"]

    def sleep(seconds: float) -> None:
        state["t"] += seconds

    assert (
        tray.wait_for_notification_area(
            ready, timeout=3, interval=0.5, sleep=sleep, clock=clock
        )
        is False
    )
    # 3 seconds at half a second a poll, plus the check before the loop.
    assert calls["n"] == 7
    assert state["t"] >= 3


def test_timeout_is_bounded() -> None:
    """The wait must not be unbounded - a broken Explorer cannot hang startup."""
    state = {"t": 0.0}
    tray.wait_for_notification_area(
        lambda: False,
        timeout=2,
        interval=0.5,
        sleep=lambda s: state.__setitem__("t", state["t"] + s),
        clock=lambda: state["t"],
    )
    assert state["t"] <= 2 + 0.5


# ------------------------------------------------------- watching the area


def test_heartbeat_is_written_more_than_once() -> None:
    """A heartbeat written once at startup goes stale while the icon is up.

    That is the bug this loop exists for: the icon is on screen, and
    everything that asks "is the tray there?" answers no.

    The loop is stopped from *ready*, not from *beat*.  Stopping it from
    the heartbeat would mean a mutation that removes the heartbeat also
    removes the only thing that ends the loop, and the test would hang
    instead of failing - a hang says nothing about what broke.
    """
    beats: list[int] = []
    stop = threading.Event()
    polls = {"n": 0}

    def ready() -> bool:
        polls["n"] += 1
        if polls["n"] >= 4:
            stop.set()
        return True

    tray.watch_notification_area(
        FakeIcon(), ready=ready, stop=stop, beat=lambda: beats.append(1), interval=0.01
    )
    assert len(beats) >= 3


def test_no_beat_is_allowed() -> None:
    stop = threading.Event()
    calls = {"n": 0}

    def ready() -> bool:
        calls["n"] += 1
        if calls["n"] >= 2:
            stop.set()
        return True

    tray.watch_notification_area(FakeIcon(), ready=ready, stop=stop, interval=0.01)


def test_icon_is_added_again_after_explorer_returns() -> None:
    """Area goes away and comes back: the icon has to be removed and re-added.

    A single ``visible = True`` does nothing, because pystray already
    thinks the icon is visible.  The False/True pair is what produces
    the ``NIM_DELETE`` + ``NIM_ADD`` that Explorer needs.
    """
    icon = FakeIcon()
    stop = threading.Event()
    ready = stopper([True, True, False, True, True], stop, after=5)

    tray.watch_notification_area(
        icon, ready=ready, stop=stop, beat=lambda: None, interval=0.01
    )

    assert icon.log == [False, True], f"expected one remove/add pair, got {icon.log}"


def test_the_area_going_away_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """The only trace of the icon being dropped is this log line.

    Without it, "the icon vanished after Explorer restarted" has no
    evidence anywhere - the add/remove pair happens in the next branch
    and leaves nothing behind.
    """
    import logging

    caplog.set_level(logging.INFO, logger="winsleep.tray")
    stop = threading.Event()

    tray.watch_notification_area(
        FakeIcon(),
        ready=stopper([True, False, False], stop, after=3),
        stop=stop,
        interval=0.01,
    )
    assert "went away" in caplog.text


def test_icon_is_left_alone_when_the_area_stays() -> None:
    icon = FakeIcon()
    stop = threading.Event()

    tray.watch_notification_area(
        icon, ready=stopper([True], stop, after=4), stop=stop, interval=0.01
    )
    assert icon.log == [], "the icon was touched although the area never moved"


def test_area_that_never_comes_back_does_not_raise() -> None:
    icon = FakeIcon()
    stop = threading.Event()

    tray.watch_notification_area(
        icon, ready=stopper([False], stop, after=4), stop=stop, interval=0.01
    )
    assert icon.log == []


def test_broken_beat_is_not_fatal() -> None:
    """The icon matters more than the heartbeat."""
    stop = threading.Event()
    calls = {"n": 0}

    def beat() -> None:
        calls["n"] += 1
        raise OSError("disk went away")

    tray.watch_notification_area(
        FakeIcon(),
        ready=stopper([True], stop, after=3),
        stop=stop,
        beat=beat,
        interval=0.01,
    )
    assert calls["n"] >= 2


def test_stops_when_the_event_is_set_before_start() -> None:
    icon = FakeIcon()
    stop = threading.Event()
    stop.set()
    tray.watch_notification_area(icon, ready=lambda: True, stop=stop, interval=0.01)
    assert icon.log == []


# ------------------------------------------------------------- the platform call


def test_notification_area_is_not_ready_off_windows() -> None:
    """On macOS and Linux there is no such thing, and saying so is correct.

    The assertion only holds where Windows is absent - a Windows CI
    runner does have a taskbar.  Bracketing the two platforms catches
    both "always True" and "always False".
    """
    if winapi.is_supported():
        pytest.skip("this machine has a notification area")
    assert winapi.notification_area_ready() is False


def test_notification_area_is_ready_on_windows() -> None:
    """A normal Windows desktop does have one - the function must find it."""
    if not winapi.is_supported():
        pytest.skip("not Windows")
    assert winapi.notification_area_ready() is True


def test_notification_area_is_exported() -> None:
    assert "notification_area_ready" in winapi.__all__


# ------------------------------------------------------------- icon drawing


def test_icon_path_returns_none_when_the_asset_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "winsleep.gui.app._assets_dir",
        lambda: __import__("pathlib").Path("/definitely/not/here"),
    )
    assert tray.icon_path() is None


def test_icon_path_finds_the_shipped_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / tray.TRAY_ICON_NAME).write_bytes(b"not really a png")
    monkeypatch.setattr("winsleep.gui.app._assets_dir", lambda: tmp_path)
    assert tray.icon_path() == tmp_path / tray.TRAY_ICON_NAME
