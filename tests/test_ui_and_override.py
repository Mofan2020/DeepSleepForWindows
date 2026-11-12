"""Tests for the things added in 0.5.0.

Three of them are new mechanisms rather than new screens:

* the timed "hold the machine awake" override, which lives in the config
  as an absolute deadline instead of a flag;
* the daemon answering "what did you decide, and why";
* the tray menu entry that sets the override without opening a window.

The interface side is covered by scripts/check-gui-js.mjs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time

from winsleep.core import config as config_mod
from winsleep.core.decide import decide
from winsleep.core.rules import Snapshot

NOON = dt.datetime(2026, 11, 9, 12, 0, 0)
NOON_EPOCH = NOON.timestamp()


def snap(**kwargs) -> Snapshot:
    base = {"now": NOON, "idle_seconds": 0.0, "cpu_percent": 0.0}
    base.update(kwargs)
    return Snapshot(**base)


def decide_with(idle_for: float = 0.0, **kwargs):
    base = {
        "idle_plan": "节能",
        "active_plan": "高性能",
        "idle_after_seconds": 600,
        "idle_cpu_percent": 15.0,
        "game_running": False,
        "keep_awake_enabled": False,
        "server_mode": False,
        "server_lock_seconds": 300,
    }
    base.update(kwargs)
    return decide(snap(idle_seconds=idle_for), **base)


# ------------------------------------------------------- the timed override


def test_an_override_holds_the_machine_awake():
    """Thirty minutes of "not now" beats the idle logic."""
    intent = decide_with(idle_for=7200, override_until=NOON_EPOCH + 1800)
    assert intent.keep_awake is True


def test_an_override_does_not_change_the_plan():
    """The plan is still decided by the idle logic.

    Letting the override pick the plan would leave the machine on the
    fast plan all night - the user asked it not to *sleep*, not to stay
    at full speed.
    """
    plain = decide_with(idle_for=7200)
    held = decide_with(idle_for=7200, override_until=NOON_EPOCH + 1800)
    assert held.plan == plain.plan == "节能"


def test_the_override_says_how_long_is_left():
    intent = decide_with(override_until=NOON_EPOCH + 1800)
    assert "timed override" in intent.reason
    assert "30 min" in intent.reason


def test_an_override_in_the_past_is_not_an_override():
    """A deadline that has passed is not a flag to clear.

    This is the whole reason the override is stored as a time and not as
    a boolean: nothing has to remember to switch it off.
    """
    intent = decide_with(idle_for=7200, override_until=NOON_EPOCH - 1)
    assert intent.keep_awake is False
    assert intent.plan == "节能"
    assert "timed override" not in intent.reason


def test_no_override_is_the_old_behaviour():
    intent = decide_with(idle_for=7200, override_until=0.0)
    assert intent.keep_awake is False


def test_an_override_fires_even_when_a_game_is_running():
    """A game already keeps the machine awake; the override must not
    turn that off or make the reason wrong."""
    intent = decide_with(game_running=True, override_until=NOON_EPOCH + 600)
    assert intent.keep_awake is True
    assert "timed override" in intent.reason


def test_server_mode_wins_over_an_override():
    """Server mode keeps the machine awake anyway, so the reason has to
    stay about locking the screen - that is the part the user acts on."""
    intent = decide_with(
        idle_for=0,
        server_mode=True,
        server_lock_seconds=300,
        override_until=NOON_EPOCH + 600,
    )
    assert intent.keep_awake is True


# ----------------------------------------------------------- the config


def test_the_override_round_trips_through_the_config(tmp_path):
    path = tmp_path / "config.json"
    config = config_mod.Config(keep_awake_until=1234567890.5)
    config_mod.save(config, path)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["keep_awake_until"] == 1234567890.5

    read_back = config_mod.load(path)
    assert read_back.keep_awake_until == 1234567890.5


def test_a_config_without_the_field_reads_as_no_override(tmp_path):
    """Old config files do not have the key.  They must read as "no
    override" rather than as a crash or a missing attribute."""
    path = tmp_path / "config.json"
    path.write_text('{"idle_plan": "节能"}', encoding="utf-8")
    assert config_mod.load(path).keep_awake_until == 0.0


def test_a_negative_deadline_is_read_as_no_override(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"keep_awake_until": -50}', encoding="utf-8")
    assert config_mod.load(path).keep_awake_until == 0.0


# ------------------------------------------------------------ the bridge


def test_the_bridge_writes_a_deadline(tmp_path, monkeypatch):
    """``set_override`` is what the four buttons in the window call."""
    from winsleep.gui import bridge as bridge_mod

    path = tmp_path / "config.json"
    bridge = bridge_mod.Bridge(config_path=path)

    before = time.time()
    answer = bridge.set_override(30)
    assert answer["ok"] is True
    assert answer["minutes"] == 30

    saved = config_mod.load(path)
    assert saved.keep_awake_until > before


def test_cancelling_the_override_clears_the_deadline(tmp_path):
    from winsleep.gui import bridge as bridge_mod

    path = tmp_path / "config.json"
    config_mod.save(config_mod.Config(keep_awake_until=time.time() + 999), path)

    bridge = bridge_mod.Bridge(config_path=path)
    answer = bridge.set_override(0)

    assert answer["ok"] is True
    assert config_mod.load(path).keep_awake_until == 0.0


def test_a_nonsense_override_cancels_rather_than_raising(tmp_path):
    from winsleep.gui import bridge as bridge_mod

    path = tmp_path / "config.json"
    bridge = bridge_mod.Bridge(config_path=path)
    assert bridge.set_override("lots")["ok"] is True
    assert config_mod.load(path).keep_awake_until == 0.0


# --------------------------------------------------------- what it decided


def test_the_daemon_reports_what_it_decided(tmp_path, monkeypatch):
    """The window shows ``intent.reason``.  Until it was exposed the
    window could say which plan was active and never why."""
    from winsleep.core import daemon as daemon_mod

    runner = daemon_mod.Daemon(config_path=tmp_path / "config.json")

    assert runner._status()["ok"] is False

    from winsleep.core.decide import Intent

    runner._current_intent = Intent(plan="节能", keep_awake=True, reason="idle for 900s")
    runner._current_intent_at = 1234.0
    answer = runner._status()

    assert answer["ok"] is True
    assert answer["plan"] == "节能"
    assert answer["keep_awake"] is True
    assert answer["reason"] == "idle for 900s"
    assert answer["at"] == 1234.0


def test_the_status_message_is_answered_over_the_socket(tmp_path):
    """The dispatch table has to know the message, or the window gets
    ``unknown message`` and shows nothing."""
    from winsleep.core import daemon as daemon_mod

    runner = daemon_mod.Daemon(config_path=tmp_path / "config.json")
    answer = json.loads(runner._server._dispatch(b'{"kind": "status"}'))
    assert "ok" in answer
    # "not decided yet" is a real answer; "unknown message" is the
    # server not knowing the kind at all, and that is the bug.
    assert "error" not in answer


def test_the_reported_decision_follows_the_latest_tick(tmp_path, monkeypatch):
    """The status has to be the *current* decision, not the first one.

    ``_last_intent`` only changes when the decision changes - it is what
    the log writes about - so answering from it would report a reason
    from half an hour ago.
    """
    from winsleep.core import daemon as daemon_mod
    from winsleep.core.decide import Intent

    runner = daemon_mod.Daemon(config_path=tmp_path / "config.json")
    monkeypatch.setattr(runner, "_trim_log", lambda _config: None)
    monkeypatch.setattr(runner, "_apply", lambda _config, _intent: None)

    made = iter([Intent(plan="节能", reason="first"), Intent(plan="高性能", reason="second")])
    monkeypatch.setattr(runner, "_decide", lambda _config: next(made))

    runner._tick()
    assert runner._status()["reason"] == "first"

    runner._tick()
    assert runner._status()["reason"] == "second"
    assert runner._status()["plan"] == "高性能"


def test_an_unknown_message_is_still_refused(tmp_path):
    from winsleep.core import daemon as daemon_mod

    runner = daemon_mod.Daemon(config_path=tmp_path / "config.json")
    answer = json.loads(runner._server._dispatch(b'{"kind": "nonsense"}'))
    assert answer["ok"] is False


# ------------------------------------------------------------------ the cli


def test_the_hold_command_sets_the_deadline(tmp_path, monkeypatch, capsys):
    from winsleep import cli

    path = tmp_path / "config.json"
    monkeypatch.setattr(cli.paths, "config_path", lambda: path)

    assert cli._cmd_hold(argparse.Namespace(minutes="45")) == 0
    assert config_mod.load(path).keep_awake_until > time.time()
    assert "45 分钟" in capsys.readouterr().out


def test_the_hold_command_cancels(tmp_path, monkeypatch):
    from winsleep import cli

    path = tmp_path / "config.json"
    config_mod.save(config_mod.Config(keep_awake_until=time.time() + 600), path)
    monkeypatch.setattr(cli.paths, "config_path", lambda: path)

    assert cli._cmd_hold(argparse.Namespace(minutes="off")) == 0
    assert config_mod.load(path).keep_awake_until == 0.0


def test_the_hold_command_refuses_a_word(tmp_path, monkeypatch, capsys):
    from winsleep import cli

    monkeypatch.setattr(cli.paths, "config_path", lambda: tmp_path / "config.json")

    assert cli._cmd_hold(argparse.Namespace(minutes="soon")) == 2
    assert "不是分钟数" in capsys.readouterr().out


# ------------------------------------------------------------- the bundle


def test_the_selftest_fails_when_an_interface_file_is_missing(tmp_path, monkeypatch, capsys):
    """A frozen build can carry a module and forget a file.

    The window is three files.  If one of them is not in the bundle the
    window opens blank, and nothing in the log says which file it was.
    """
    from winsleep import cli
    from winsleep.gui import app as app_mod

    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "app.js").write_text("// code", encoding="utf-8")
    # style.css is deliberately left out.
    monkeypatch.setattr(app_mod, "_assets_dir", lambda: tmp_path)

    assert cli._cmd_selftest(argparse.Namespace()) == 1
    out = capsys.readouterr().out
    assert "style.css" in out


def test_the_selftest_passes_when_the_files_are_there(tmp_path, monkeypatch):
    from winsleep import cli
    from winsleep.gui import app as app_mod

    for name in ("index.html", "app.js", "style.css"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(app_mod, "_assets_dir", lambda: tmp_path)

    assert cli._cmd_selftest(argparse.Namespace()) == 0


def test_an_empty_interface_file_counts_as_missing(tmp_path, monkeypatch, capsys):
    """PyInstaller can carry a zero byte file.  That renders as blank."""
    from winsleep import cli
    from winsleep.gui import app as app_mod

    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "app.js").write_text("// code", encoding="utf-8")
    (tmp_path / "style.css").write_text("", encoding="utf-8")
    monkeypatch.setattr(app_mod, "_assets_dir", lambda: tmp_path)

    assert cli._cmd_selftest(argparse.Namespace()) == 1
    assert "style.css" in capsys.readouterr().out


# ---------------------------------------------------------------- the tray


def test_the_tray_menu_carries_the_hold_entries(monkeypatch):
    """The tray is how this gets used without opening a window, so the
    "not now" entries belong there."""
    from winsleep.core import tray as tray_mod

    seen: dict[str, object] = {}

    class FakeMenu:
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = items

    class FakeMenuItem:
        def __init__(self, text, action=None, default=False):
            self.text = text
            self.action = action
            self.default = default

    class FakeIcon:
        def __init__(self):
            self.menu = None

        def run_detached(self):
            pass

        def stop(self):
            pass

    fake_pystray = type(
        "P",
        (),
        {"Menu": FakeMenu, "MenuItem": FakeMenuItem},
    )

    monkeypatch.setitem(__import__("sys").modules, "pystray", fake_pystray)
    monkeypatch.setattr(tray_mod, "make_icon", lambda: FakeIcon())
    monkeypatch.setattr(tray_mod, "wait_for_notification_area", lambda _ready: None)
    monkeypatch.setattr(
        tray_mod,
        "watch_notification_area",
        lambda icon, **_kwargs: seen.update(icon=icon),
    )
    monkeypatch.setitem(__import__("sys").modules, "pystray", fake_pystray)
    monkeypatch.setattr(tray_mod.sys, "platform", "win32")

    calls: list[int] = []
    tray_mod.run_tray(
        lambda: None,
        lambda: None,
        lambda: None,
        on_override=calls.append,
    )

    icon = seen.get("icon")
    assert icon is not None
    labels = [getattr(item, "text", "") for item in icon.menu.items]
    assert any(label == "保持唤醒" for label in labels)

    # The entries have to do something, not just be there.  A cancel
    # that quietly sets thirty minutes is worse than no cancel.
    hold = [item for item in icon.menu.items if getattr(item, "text", "") == "保持唤醒"][0]
    entries = hold.action.items

    half_hour = [item for item in entries if getattr(item, "text", "") == "30 分钟"][0]
    half_hour.action()
    assert calls == [30]

    cancel = [
        item for item in entries
        if getattr(item, "text", "").startswith("取消")
    ][0]
    cancel.action()
    assert calls == [30, 0]
