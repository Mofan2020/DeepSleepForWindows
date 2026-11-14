"""写设置：成功要说，失败更要说。

用户原话：「无法保存设置，设置好后过一会就跳回原设置」。窗口每五秒按
配置文件刷一次输入框，所以**保存悄悄失败**在用户眼里就是「设置跳回去了」。
这里钉住：失败必须返回 ``ok: False`` 并带上原因。
"""
from __future__ import annotations

from winsleep.core import config as config_mod
from winsleep.gui.bridge import Bridge


def test_a_failed_save_is_reported(tmp_path, monkeypatch) -> None:
    def broken(config, path):
        raise PermissionError("the file is held open by another process")

    monkeypatch.setattr(config_mod, "save", broken)
    bridge = Bridge(config_path=tmp_path / "config.json")

    answer = bridge.save_config({"idle_seconds": 900})

    assert answer["ok"] is False
    assert "held open" in answer["message"]


def test_a_successful_save_writes_and_says_so(tmp_path) -> None:
    bridge = Bridge(config_path=tmp_path / "config.json")

    answer = bridge.save_config({"idle_seconds": 900})

    assert answer["ok"] is True
    assert answer["config"]["idle_seconds"] == 900
    assert config_mod.load(tmp_path / "config.json").idle_seconds == 900


def test_server_mode_reports_a_failed_save(tmp_path, monkeypatch) -> None:
    def broken(config, path):
        raise OSError("disk full")

    monkeypatch.setattr(config_mod, "save", broken)
    bridge = Bridge(config_path=tmp_path / "config.json")

    answer = bridge.set_server_mode(True)

    assert answer["ok"] is False
    assert "disk full" in answer["message"]
