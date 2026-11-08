"""Tests for the command line.

The command line is the part a user types into, so it has to answer
the same way on every machine, whatever the console code page is.
"""

from __future__ import annotations

import pytest

from winsleep import cli


class TestTheSelfTest:
    """The self test is the guard against an incomplete bundle."""

    def test_it_passes_when_everything_is_there(self, monkeypatch, capsys):
        assert cli._cmd_selftest(None) == 0
        assert "everything" in capsys.readouterr().out

    def test_it_reports_a_missing_module(self, monkeypatch, capsys):
        import importlib

        real_import = importlib.import_module

        def fake_import(name, *args, **kwargs):
            if name == "webview":
                raise ImportError("no module named 'webview'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", fake_import)

        assert cli._cmd_selftest(None) == 1
        out = capsys.readouterr().out
        assert "missing" in out
        assert "webview" in out

    def test_it_names_every_missing_module(self, monkeypatch, capsys):
        import importlib

        real_import = importlib.import_module

        def fake_import(name, *args, **kwargs):
            if name in ("webview", "psutil"):
                raise ImportError(f"no module named {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", fake_import)

        assert cli._cmd_selftest(None) == 1
        out = capsys.readouterr().out
        assert "webview" in out
        assert "psutil" in out


class TestTheParser:
    def test_every_command_has_a_handler(self):
        parser = cli.build_parser()
        for name in ("gui", "daemon", "agent", "install", "uninstall",
                     "status", "plans", "lock", "selftest"):
            args = parser.parse_args([name])
            assert callable(args.func), name

    def test_switch_takes_a_plan(self):
        args = cli.build_parser().parse_args(["switch", "节能"])
        assert args.plan == "节能"

    def test_the_version_is_reported(self):
        with pytest.raises(SystemExit) as caught:
            cli.build_parser().parse_args(["--version"])
        assert caught.value.code == 0
