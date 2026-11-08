"""Tests for the power plan helpers.

The parsers are gone on purpose: the plan list now comes from the
registry and the active plan from the ``powrprof`` API, so there is no
localized command output left to parse.  What is tested here is the
logic that works on any machine - the GUID handling and the name
matching.
"""

from __future__ import annotations

import pytest

from winsleep.core import winapi
from winsleep.core.winpower import _looks_like_guid, _strip_exe


class TestGuid:
    def test_a_guid_survives_a_round_trip(self):
        text = "381b4222-f694-41f0-9685-ff5bb260df2e"
        assert winapi.guid_to_string(winapi.guid_from_string(text)) == text

    def test_the_parts_are_read_in_the_right_order(self):
        guid = winapi.guid_from_string("381b4222-f694-41f0-9685-ff5bb260df2e")
        assert guid.Data1 == 0x381B4222
        assert guid.Data2 == 0xF694
        assert guid.Data3 == 0x41F0

    def test_the_tail_is_little_endian(self):
        guid = winapi.guid_from_string("381b4222-f694-41f0-9685-ff5bb260df2e")
        assert bytes(guid.Data4) == bytes.fromhex("9685ff5bb260df2e")

    def test_uppercase_input_works(self):
        guid = winapi.guid_from_string("381B4222-F694-41F0-9685-FF5BB260DF2E")
        assert winapi.guid_to_string(guid) == "381b4222-f694-41f0-9685-ff5bb260df2e"

    def test_braces_are_tolerated(self):
        guid = winapi.guid_from_string("{381b4222-f694-41f0-9685-ff5bb260df2e}")
        assert guid.Data1 == 0x381B4222

    def test_rubbish_is_rejected(self):
        with pytest.raises(ValueError):
            winapi.guid_from_string("not a guid")


class TestLooksLikeGuid:
    def test_accepts_a_real_guid(self):
        assert _looks_like_guid("381b4222-f694-41f0-9685-ff5bb260df2e")

    def test_rejects_the_wrong_length(self):
        assert not _looks_like_guid("381b4222-f694-41f0-9685")

    def test_rejects_a_word(self):
        assert not _looks_like_guid("Balanced")

    def test_rejects_an_empty_string(self):
        assert not _looks_like_guid("")


class TestStripExe:
    def test_removes_the_suffix(self):
        assert _strip_exe("steam.exe") == "steam"

    def test_leaves_a_bare_name_alone(self):
        assert _strip_exe("steam") == "steam"


class TestOffWindows:
    def test_the_active_scheme_is_none_off_windows(self):
        if not winapi.is_supported():
            assert winapi.get_active_scheme() is None

    def test_the_idle_time_is_minus_one_off_windows(self):
        if not winapi.is_supported():
            assert winapi.idle_seconds() == -1.0

    def test_locking_does_nothing_off_windows(self):
        if not winapi.is_supported():
            assert winapi.lock_workstation() is False
