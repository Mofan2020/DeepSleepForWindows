"""The log file: reading it, trimming it and clearing it.

The log is written by three processes at once, so the rules about
reading it incrementally and rewriting it in place are the interesting
part.
"""

from __future__ import annotations

import time
from pathlib import Path

from winsleep.core import logs


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _stamp(when: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))


def test_read_log_returns_every_line(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    _write(path, ["one", "two", "three"])
    assert logs.read_log(path) == ["one", "two", "three"]


def test_read_log_keeps_the_newest_lines(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    _write(path, ["one", "two", "three"])
    assert logs.read_log(path, 2) == ["two", "three"]


def test_read_log_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert logs.read_log(tmp_path / "nope.log") == []


def test_trim_drops_the_old_lines(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    _write(path, ["one", "two", "three", "four"])
    result = logs.trim_log(path, max_lines=2)
    assert result["changed"] is True
    assert result["removed"] == 2
    assert logs.read_log(path) == ["three", "four"]


def test_trim_leaves_the_file_alone_when_it_is_short_enough(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    _write(path, ["one", "two"])
    result = logs.trim_log(path, max_lines=10)
    assert result["changed"] is False
    assert result["removed"] == 0
    assert logs.read_log(path) == ["one", "two"]


def test_trim_drops_the_lines_that_are_too_old(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    old = _stamp(time.time() - 10 * 86400)
    new = _stamp(time.time())
    _write(path, [f"{old} old line", f"{new} new line"])
    result = logs.trim_log(path, max_age_days=1)
    assert result["changed"] is True
    assert logs.read_log(path) == [f"{new} new line"]


def test_trim_keeps_a_line_whose_time_cannot_be_read(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    _write(path, ["not a timestamp at all", "2020-01-01 00:00:00 very old"])
    logs.trim_log(path, max_age_days=1)
    kept = logs.read_log(path)
    assert "not a timestamp at all" in kept
    assert "2020-01-01 00:00:00 very old" not in kept


def test_trim_uses_both_limits(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    old = _stamp(time.time() - 10 * 86400)
    new = _stamp(time.time())
    _write(path, [f"{old} old", f"{new} a", f"{new} b", f"{new} c"])
    logs.trim_log(path, max_lines=2, max_age_days=1)
    assert logs.read_log(path) == [f"{new} b", f"{new} c"]


def test_clear_empties_the_file_and_keeps_it_usable(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    _write(path, ["one", "two"])
    result = logs.clear_log(path)
    assert result["ok"] is True
    assert result["removed"] == 2
    assert path.exists()
    assert logs.read_log(path) == []
    _write(path, ["three"])
    assert logs.read_log(path) == ["three"]


def test_tailer_gives_only_the_new_lines(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    _write(path, ["one"])
    tailer = logs.LogTailer(path)
    assert tailer.read_new() == ["one"]
    _write(path, ["one", "two"])
    assert tailer.read_new() == ["two"]


def test_tailer_gives_nothing_when_nothing_was_written(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    _write(path, ["one"])
    tailer = logs.LogTailer(path)
    tailer.read_new()
    assert tailer.read_new() == []


def test_tailer_starts_over_when_the_file_was_cleared(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    _write(path, ["one", "two"])
    tailer = logs.LogTailer(path)
    assert tailer.read_new() == ["one", "two"]
    logs.clear_log(path)
    _write(path, ["fresh"])
    assert tailer.read_new() == ["fresh"]


def test_tailer_waits_for_a_line_that_is_not_finished(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    path.write_text("2026-11-09 08:00:00 done\n2026-11-09 08:00:01 half", encoding="utf-8")
    tailer = logs.LogTailer(path)
    assert tailer.read_new() == ["2026-11-09 08:00:00 done"]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(" finished\n")
    assert tailer.read_new() == ["2026-11-09 08:00:01 half finished"]


def test_tailer_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    tailer = logs.LogTailer(tmp_path / "nope.log")
    assert tailer.read_new() == []


def test_prune_removes_the_old_rotated_files(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    for index in range(1, 5):
        (tmp_path / f"winsleep.log.{index}").write_text("x", encoding="utf-8")
    removed = logs.prune_rotated(path, keep=2)
    assert removed == 2
    assert (tmp_path / "winsleep.log.1").exists()
    assert (tmp_path / "winsleep.log.2").exists()
    assert not (tmp_path / "winsleep.log.3").exists()
    assert not (tmp_path / "winsleep.log.4").exists()


def test_tailer_does_not_give_the_same_lines_twice(tmp_path: Path) -> None:
    """The tailer has to move its position on.

    Without that the window would show the same lines again and again
    every second.
    """
    path = tmp_path / "winsleep.log"
    _write(path, ["one"])
    tailer = logs.LogTailer(path)
    assert tailer.read_new() == ["one"]
    # Nothing was written, so nothing comes back - even though the
    # file still holds the line.
    assert tailer.read_new() == []


def test_tailer_only_gives_each_line_once(tmp_path: Path) -> None:
    path = tmp_path / "winsleep.log"
    _write(path, ["one", "two"])
    tailer = logs.LogTailer(path)
    first = tailer.read_new()
    assert first == ["one", "two"]
    second = tailer.read_new()
    assert second == []
