"""Updating the program, and the things that made it impossible before.

The updater is the part that had the most trouble: the program is one
file, the file is running, and the tasks point at it.  These tests
cover the parts that can be checked without Windows - the version
comparison, the archive handling, and the script that does the
replacement.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from winsleep.core import update

# ------------------------------------------------------------------ version


def test_a_version_is_read_from_the_tag() -> None:
    assert update.parse_version("v0.3.1") == (0, 3, 1)
    assert update.parse_version("0.3.1") == (0, 3, 1)


def test_a_tag_that_is_not_a_version_is_not_a_version() -> None:
    # A tag that cannot be read must never be treated as "newer" -
    # otherwise the program would install something over itself.
    assert update.parse_version("nightly") is None
    assert update.parse_version("v0.3") is None
    assert update.parse_version("") is None


def test_a_newer_version_is_recognized() -> None:
    assert update.is_newer("v0.4.0", "0.3.0") is True
    assert update.is_newer("v0.3.1", "0.3.0") is True
    assert update.is_newer("v0.3.0", "0.3.0") is False
    assert update.is_newer("v0.2.9", "0.3.0") is False


def test_a_tag_that_cannot_be_read_is_never_newer() -> None:
    assert update.is_newer("nightly", "0.3.0") is False
    assert update.is_newer("", "0.3.0") is False


def test_the_release_address_is_the_one_of_the_repository() -> None:
    assert update.RELEASE_OWNER == "Mofan2020"
    assert update.RELEASE_REPO == "DeepSleepForWindows"
    assert "Mofan2020/DeepSleepForWindows" in update.release_url()
    assert "v0.3.0" in update.release_url("v0.3.0")


# ----------------------------------------------------------------- archive


def _make_archive(path: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as bundle:
        for name in names:
            bundle.writestr(name, b"not a real executable")


def test_the_executable_is_taken_out_of_the_archive(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    _make_archive(archive, ["DeepSleepForWindows.exe"])

    found = update.extract_executable(archive, tmp_path / "out")
    assert found.name == "DeepSleepForWindows.exe"
    assert found.exists()


def test_an_archive_without_the_executable_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    _make_archive(archive, ["readme.txt"])

    with pytest.raises(ValueError):
        update.extract_executable(archive, tmp_path / "out")


def test_an_archive_that_escapes_its_directory_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    _make_archive(archive, ["../DeepSleepForWindows.exe"])

    with pytest.raises(ValueError):
        update.extract_executable(archive, tmp_path / "out")


# ------------------------------------------------------------------ script


def test_the_updater_script_waits_for_the_program_to_close() -> None:
    script = update.updater_script(
        target="C:/Program Files/DS/DeepSleepForWindows.exe",
        source="C:/Temp/DeepSleepForWindows.exe",
        work_directory="C:/Temp/work",
        pid=4321,
        result_path="C:/ProgramData/DS/update-result.json",
        version="v0.4.0",
        tasks=[("DeepSleepForWindows-Daemon", '"C:/DS/DeepSleepForWindows.exe" daemon')],
    )
    assert "4321" in script
    assert "waitloop" in script
    # The old file is moved aside, not deleted, so a failure can be
    # undone.
    assert "move /y" in script
    assert "copy /y" in script


def test_the_updater_script_points_the_tasks_at_the_new_file() -> None:
    script = update.updater_script(
        target="C:/DS/DeepSleepForWindows.exe",
        source="C:/Temp/DeepSleepForWindows.exe",
        work_directory="C:/Temp/work",
        pid=1,
        result_path="C:/result.json",
        version="v0.4.0",
        tasks=[
            ("DeepSleepForWindows-Daemon", '"C:/DS/DeepSleepForWindows.exe" daemon'),
            ("DeepSleepForWindows-Agent", '"C:/DS/DeepSleepForWindows.exe" agent'),
        ],
    )
    assert "DeepSleepForWindows-Daemon" in script
    assert "DeepSleepForWindows-Agent" in script
    assert "/Change" in script
    # Each task keeps its own sub command.
    assert "daemon" in script
    assert "agent" in script


def test_the_updater_script_makes_the_directory_of_the_result() -> None:
    """The script runs after the program closed.

    The directory of the result is made by the program when it starts,
    and it may not be there when the script runs.  The script makes
    it, or the result is lost and the user never learns what happened.
    """
    script = update.updater_script(
        target="C:/DS/DeepSleepForWindows.exe",
        source="C:/Temp/DeepSleepForWindows.exe",
        work_directory="C:/Temp/work",
        pid=1,
        result_path="C:/ProgramData/DS/update-result.json",
        version="v0.4.0",
        tasks=[("DeepSleepForWindows-Daemon", '"C:/DS/DeepSleepForWindows.exe" daemon')],
        result_dir="C:/ProgramData/DS",
    )
    assert "mkdir" in script
    assert "C:/ProgramData/DS" in script


def test_the_updater_script_writes_the_result() -> None:
    """The result has to be written before the working directory goes.

    The working directory is removed at the end, and the result file
    may be outside it - but the order still matters, because the
    script itself runs from there.
    """
    script = update.updater_script(
        target="C:/DS/DeepSleepForWindows.exe",
        source="C:/Temp/DeepSleepForWindows.exe",
        work_directory="C:/Temp/work",
        pid=1,
        result_path="C:/ProgramData/DS/update-result.json",
        version="v0.4.0",
        tasks=[("DeepSleepForWindows-Daemon", '"C:/DS/DeepSleepForWindows.exe" daemon')],
    )
    assert "write_result" in script
    # The result is written before the directory is removed.
    assert script.index("write_result") < script.index('rd /s /q')


def test_the_updater_script_makes_the_result() -> None:
    script = update.updater_script(
        target="C:/DS/DeepSleepForWindows.exe",
        source="C:/Temp/DeepSleepForWindows.exe",
        work_directory="C:/Temp/work",
        pid=1,
        result_path="C:/ProgramData/DS/update-result.json",
        version="v0.4.0",
        tasks=[("DeepSleepForWindows-Daemon", '"C:/DS/DeepSleepForWindows.exe" daemon')],
    )
    assert "update-result.json" in script

    script = update.updater_script(
        target="C:/DS/DeepSleepForWindows.exe",
        source="C:/Temp/DeepSleepForWindows.exe",
        work_directory="C:/Temp/work",
        pid=1,
        result_path="C:/result.json",
        version="v0.4.0",
        tasks=[("DeepSleepForWindows-Daemon", '"C:/DS/DeepSleepForWindows.exe" daemon')],
    )
    assert "put_failed" in script
    assert "move_failed" in script


def test_the_updater_script_is_written_out(tmp_path: Path) -> None:
    script = update.write_updater_script(
        target=tmp_path / "DeepSleepForWindows.exe",
        source=tmp_path / "new.exe",
        work_directory=tmp_path / "work",
        pid=99,
        version="v0.4.0",
        tasks=[("DeepSleepForWindows-Daemon", '"C:/DS/DeepSleepForWindows.exe" daemon')],
    )
    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "v0.4.0" in text
    assert "99" in text


# ------------------------------------------------------------------ result


def test_the_result_of_an_update_is_written_and_read(tmp_path: Path, monkeypatch) -> None:

    monkeypatch.setenv("WINSLEEP_HOME", str(tmp_path))

    update.write_result("success", "v0.4.0", "已更新到 v0.4.0")
    result = update.read_result()
    assert result["status"] == "success"
    assert result["version"] == "v0.4.0"
    assert result["message"]

    update.clear_result()
    assert update.read_result() == {}


def test_a_result_that_is_not_there_is_an_empty_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WINSLEEP_HOME", str(tmp_path))
    assert update.read_result() == {}
