import os
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

from opengrab.media import ffmpeg as ff
from opengrab.settings import Settings


def make_file(folder, name, data=b"x"):
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


class FakeRun:
    def __init__(self, returncode=0, write=b"merged", stderr=b"", exc=None):
        self.returncode = returncode
        self.write = write
        self.stderr = stderr
        self.exc = exc
        self.calls = []

    def __call__(self, cmd, capture_output=False, timeout=None):
        self.calls.append((cmd, capture_output, timeout))
        if self.write:
            with open(cmd[-1], "wb") as f:
                f.write(self.write)
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(returncode=self.returncode, stdout=b"", stderr=self.stderr)


def test_settings_media_ffmpeg_path_default():
    assert Settings().media_ffmpeg_path == ""


def test_find_ffmpeg_explicit_path():
    d = tempfile.mkdtemp()
    exe = make_file(d, "ffmpeg.exe")
    assert ff.find_ffmpeg(exe) == exe


def test_find_ffmpeg_explicit_missing_no_fallback():
    seen = []
    assert ff.find_ffmpeg(
        os.path.join(tempfile.mkdtemp(), "nope.exe"),
        which=lambda n: seen.append(n) or "C:/x/ffmpeg.exe",
    ) == ""
    assert seen == []


def test_find_ffmpeg_path_lookup():
    seen = []
    assert ff.find_ffmpeg("", which=lambda n: seen.append(n) or "C:/x/ffmpeg.exe") == "C:/x/ffmpeg.exe"
    assert seen == ["ffmpeg"]


def test_find_ffmpeg_none():
    assert ff.find_ffmpeg("", which=lambda n: None) == ""


def test_available():
    d = tempfile.mkdtemp()
    exe = make_file(d, "ffmpeg.exe")
    assert ff.available(exe) is True
    assert ff.available("", which=lambda n: None) is False


def test_merge_cmd_exact():
    assert ff.merge_cmd("ff", "v.mp4", "a.m4a", "o.mp4") == [
        "ff",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        "v.mp4",
        "-i",
        "a.m4a",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        "o.mp4",
    ]


def test_temp_path_keeps_extension():
    assert ff.temp_path(os.path.join("d", "clip.mkv")) == os.path.join("d", "clip.merging.mkv")


def test_merge_ok():
    d = tempfile.mkdtemp()
    exe, v, a = make_file(d, "ffmpeg.exe"), make_file(d, "v.mp4"), make_file(d, "a.m4a")
    out = os.path.join(d, "out.mp4")
    run = FakeRun()
    assert ff.merge(v, a, out, ffmpeg=exe, run=run, timeout=7) == out
    with open(out, "rb") as f:
        assert f.read() == b"merged"
    assert not os.path.exists(ff.temp_path(out))
    assert run.calls == [(ff.merge_cmd(exe, v, a, ff.temp_path(out)), True, 7)]


def test_merge_replaces_existing_output():
    d = tempfile.mkdtemp()
    out = make_file(d, "out.mp4", b"old")
    exe, v, a = make_file(d, "ffmpeg.exe"), make_file(d, "v.mp4"), make_file(d, "a.m4a")
    run = FakeRun()
    assert ff.merge(v, a, out, ffmpeg=exe, run=run) == out
    with open(out, "rb") as f:
        assert f.read() == b"merged"


def test_merge_no_ffmpeg():
    d = tempfile.mkdtemp()
    v, a, out = make_file(d, "v.mp4"), make_file(d, "a.m4a"), os.path.join(d, "out.mp4")
    run = FakeRun()
    with pytest.raises(ff.MediaUnavailable):
        ff.merge(v, a, out, run=run, which=lambda n: None)
    assert run.calls == []


def test_merge_missing_input():
    d = tempfile.mkdtemp()
    exe = make_file(d, "ffmpeg.exe")
    v = make_file(d, "v.mp4")
    a = os.path.join(d, "nope.m4a")
    out = os.path.join(d, "out.mp4")
    run = FakeRun()
    with pytest.raises(ff.MergeError, match="missing input"):
        ff.merge(v, a, out, ffmpeg=exe, run=run)
    assert run.calls == []


def test_merge_nonzero_exit():
    d = tempfile.mkdtemp()
    exe, v, a = make_file(d, "ffmpeg.exe"), make_file(d, "v.mp4"), make_file(d, "a.m4a")
    out = os.path.join(d, "out.mp4")
    run = FakeRun(returncode=1, stderr=b"boom")
    with pytest.raises(ff.MergeError, match="boom"):
        ff.merge(v, a, out, ffmpeg=exe, run=run)
    assert not os.path.exists(out)
    assert not os.path.exists(ff.temp_path(out))


def test_merge_empty_output():
    d = tempfile.mkdtemp()
    exe, v, a = make_file(d, "ffmpeg.exe"), make_file(d, "v.mp4"), make_file(d, "a.m4a")
    out = os.path.join(d, "out.mp4")
    run = FakeRun(write=b"")
    with pytest.raises(ff.MergeError, match="no output"):
        ff.merge(v, a, out, ffmpeg=exe, run=run)
    assert not os.path.exists(out)


def test_merge_timeout_cleans_temp():
    d = tempfile.mkdtemp()
    exe, v, a = make_file(d, "ffmpeg.exe"), make_file(d, "v.mp4"), make_file(d, "a.m4a")
    out = os.path.join(d, "out.mp4")
    run = FakeRun(exc=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1))
    with pytest.raises(ff.MergeError, match="timeout"):
        ff.merge(v, a, out, ffmpeg=exe, run=run)
    assert not os.path.exists(ff.temp_path(out))
    assert not os.path.exists(out)


def test_merge_oserror_is_unavailable():
    d = tempfile.mkdtemp()
    exe, v, a = make_file(d, "ffmpeg.exe"), make_file(d, "v.mp4"), make_file(d, "a.m4a")
    out = os.path.join(d, "out.mp4")
    run = FakeRun(write=b"", exc=OSError("denied"))
    with pytest.raises(ff.MediaUnavailable, match="denied"):
        ff.merge(v, a, out, ffmpeg=exe, run=run)