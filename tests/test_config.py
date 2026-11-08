"""Tests for the config store."""

from __future__ import annotations

import json

from winsleep.core import config as config_mod
from winsleep.core.rules import Action, Condition, Rule


def test_a_missing_file_gives_the_defaults(tmp_path):
    config = config_mod.load(tmp_path / "nope.json")
    assert config.idle_plan == ""
    assert config.idle_seconds == 600
    assert config.idle_cpu_percent == 15.0
    assert config.rules == []


def test_a_corrupt_file_gives_the_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json at all", encoding="utf-8")
    config = config_mod.load(path)
    assert config.idle_seconds == 600


def test_a_file_holding_a_list_gives_the_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    config = config_mod.load(path)
    assert config.rules == []


def test_a_config_survives_a_round_trip(tmp_path):
    path = tmp_path / "config.json"
    config = config_mod.Config(
        idle_plan="节能",
        active_plan="Bitsum Highest Performance",
        idle_seconds=900,
        idle_cpu_percent=20.0,
        game_processes=("steam.exe", "yysls.exe"),
        game_keeps_awake=False,
        server_mode=True,
        server_lock_seconds=120,
        keep_awake=True,
        rules=[
            Rule(
                "晚间",
                (Condition("time_window", "22:00-07:00"),),
                Action("set_power_plan", "节能"),
                priority=2,
            )
        ],
    )
    config_mod.save(config, path)
    loaded = config_mod.load(path)
    assert loaded.idle_plan == "节能"
    assert loaded.idle_seconds == 900
    assert loaded.game_processes == ("steam.exe", "yysls.exe")
    assert loaded.server_mode is True
    assert loaded.rules[0].name == "晚间"
    assert loaded.rules[0].conditions[0].value == "22:00-07:00"


def test_saving_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "config.json"
    config_mod.save(config_mod.Config(), path)
    config_mod.save(config_mod.Config(idle_plan="节能"), path)
    leftovers = [p for p in tmp_path.iterdir() if p.name != "config.json"]
    assert leftovers == []


def test_saving_creates_the_directory(tmp_path):
    path = tmp_path / "nested" / "deeper" / "config.json"
    config_mod.save(config_mod.Config(), path)
    assert path.is_file()


def test_the_written_file_is_readable_json(tmp_path):
    path = tmp_path / "config.json"
    config_mod.save(config_mod.Config(idle_plan="节能"), path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["idle_plan"] == "节能"


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"idle_plan": "节能", "something_new": 42}), encoding="utf-8"
    )
    config = config_mod.load(path)
    assert config.idle_plan == "节能"


def test_the_rules_keep_their_order(tmp_path):
    path = tmp_path / "config.json"
    config = config_mod.load(path)
    config.rules = [
        Rule("first", (), Action("lock_screen")),
        Rule("second", (), Action("lock_screen")),
    ]
    config_mod.save(config, path)
    loaded = config_mod.load(path)
    assert [r.name for r in loaded.rules] == ["first", "second"]


class TestZeroValues:
    """A zero in the config is a zero, not a missing value."""

    def test_a_zero_cpu_threshold_stays_zero(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"idle_cpu_percent": 0}), encoding="utf-8"
        )
        config = config_mod.load(path)
        assert config.idle_cpu_percent == 0.0

    def test_a_zero_idle_time_stays_zero(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"idle_seconds": 0}), encoding="utf-8")
        config = config_mod.load(path)
        assert config.idle_seconds == 0

    def test_a_zero_lock_time_stays_zero(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"server_lock_seconds": 0}), encoding="utf-8"
        )
        config = config_mod.load(path)
        assert config.server_lock_seconds == 0

    def test_a_missing_value_still_gets_the_default(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({}), encoding="utf-8")
        config = config_mod.load(path)
        assert config.idle_cpu_percent == 15.0
        assert config.idle_seconds == 600

    def test_a_value_that_is_not_a_number_gets_the_default(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"idle_cpu_percent": "soon"}), encoding="utf-8"
        )
        config = config_mod.load(path)
        assert config.idle_cpu_percent == 15.0

    def test_a_zero_survives_a_round_trip(self, tmp_path):
        path = tmp_path / "config.json"
        config_mod.save(config_mod.Config(idle_cpu_percent=0.0), path)
        assert config_mod.load(path).idle_cpu_percent == 0.0
