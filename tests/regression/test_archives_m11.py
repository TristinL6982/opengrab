import os

import pytest

from opengrab.archives import (
    archive_kind,
    check_limits,
    first_volume,
    group_volumes,
    is_set_complete,
    load_passwords,
    password_candidates,
    safe_member_path,
    seven_zip_command,
    volume_info,
)


def test_archive_kind():
    assert archive_kind("a.ZIP") == "zip"
    assert archive_kind("b.7z") == "7z"
    assert archive_kind("c.part02.rar") == "rar"
    assert archive_kind("d.7z.003") == "7z"
    assert archive_kind("e.r00") == "rar"
    assert archive_kind("f.tar.gz") == "tar"
    assert archive_kind("g.mp4") is None


def test_volume_info():
    assert volume_info("Movie.part01.rar") == ("movie.rar", 1)
    assert volume_info("x.7z.003") == ("x.7z", 3)
    assert volume_info("old.rar") == ("old.rar", 1)
    assert volume_info("old.r00") == ("old.rar", 2)
    assert volume_info("single.zip") is None


def test_group_volumes_sorted():
    names = ["m.part2.rar", "x.7z.002", "m.part1.rar", "x.7z.001", "note.txt"]
    assert group_volumes(names) == {
        "m.rar": ["m.part1.rar", "m.part2.rar"],
        "x.7z": ["x.7z.001", "x.7z.002"],
    }


def test_set_completeness_and_first_volume():
    assert is_set_complete(["a.part1.rar", "a.part2.rar", "a.part3.rar"]) is True
    assert is_set_complete(["a.part1.rar", "a.part3.rar"]) is False
    assert is_set_complete(["old.rar", "old.r00", "old.r01"]) is True
    assert is_set_complete([]) is False
    assert first_volume(["a.part2.rar", "a.part1.rar"]) == "a.part1.rar"
    assert first_volume(["a.part2.rar"]) is None


def test_safe_member_path(tmp_path):
    dest = str(tmp_path)
    assert safe_member_path(dest, "dir/file.txt") == os.path.join(os.path.abspath(dest), "dir", "file.txt")
    with pytest.raises(ValueError):
        safe_member_path(dest, "../evil.txt")
    with pytest.raises(ValueError):
        safe_member_path(dest, "a\\..\\..\\evil.txt")
    with pytest.raises(ValueError):
        safe_member_path(dest, "/etc/passwd")
    with pytest.raises(ValueError):
        safe_member_path(dest, "C:/Windows/x.dll")


def test_check_limits():
    assert check_limits([("a", 10), ("b", 20)], 10, 1) == []
    problems = check_limits([("a", 1), ("b", 1), ("c", 1)], 2, 1)
    assert problems == ["too many files: 3 > 2"]
    big = check_limits([("a", 2 * 1024 ** 3)], 10, 1)
    assert len(big) == 1
    assert "exceeds" in big[0]


def test_passwords():
    text = "# my list\nsecret\n\n  hunter2  \nsecret\n"
    assert load_passwords(text) == ["secret", "hunter2"]
    assert password_candidates("pkg", ["secret", "pkg"]) == ["pkg", "secret"]
    assert password_candidates("", ["secret"]) == ["secret"]


def test_seven_zip_command():
    assert seven_zip_command("7z.exe", "a.7z", "out", "pw") == ["7z.exe", "x", "-y", "-oout", "-ppw", "a.7z"]
    assert seven_zip_command("7z.exe", "a.7z", "out")[4] == "-p"