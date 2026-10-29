"""Merge-mode helper (M9d)."""
from pathlib import Path

from opengrab.media import ffmpeg as media_ffmpeg
from opengrab.media.mergejob import audio_part, merge_job

EXTRA = {"media_mode": "merge", "media_ext": "mp4", "media_parts": [
    {"kind": "video", "url": "http://x/v", "ext": "mp4"},
    {"kind": "audio", "url": "http://x/a", "ext": "m4a"}]}


def fake_fetch(calls):
    def fetch(url, dest):
        calls.append((url, Path(dest).name))
        Path(dest).write_bytes(b"audio")
    return fetch


def fake_merge(video, audio, out, ffmpeg=""):
    Path(out).write_bytes(Path(video).read_bytes() + Path(audio).read_bytes())
    return out


def test_audio_part():
    assert audio_part(EXTRA) == {"kind": "audio", "url": "http://x/a", "ext": "m4a"}
    assert audio_part({"media_mode": "progressive"}) is None
    assert audio_part({}) is None


def test_not_merge_returns_video(tmp_path):
    v = tmp_path / "Clip.mp4"
    v.write_bytes(b"video")
    calls = []
    assert merge_job(v, {"media_mode": "progressive"}, fake_fetch(calls)) == v
    assert calls == []


def test_merge_same_ext(tmp_path):
    v = tmp_path / "Clip.mp4"
    v.write_bytes(b"video")
    calls = []
    extra = dict(EXTRA)
    out = merge_job(v, extra, fake_fetch(calls), merge_fn=fake_merge)
    assert out == v
    assert v.read_bytes() == b"videoaudio"
    assert calls == [("http://x/a", "Clip.audio.m4a")]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Clip.mp4"]
    assert extra["media_merge"] == {"status": "ok"}


def test_merge_new_ext(tmp_path):
    v = tmp_path / "Clip.webm"
    v.write_bytes(b"video")
    extra = dict(EXTRA, media_ext="mkv")
    out = merge_job(v, extra, fake_fetch([]), merge_fn=fake_merge)
    assert out == tmp_path / "Clip.mkv"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Clip.mkv"]


def test_merge_failure_keeps_video(tmp_path):
    v = tmp_path / "Clip.mp4"
    v.write_bytes(b"video")

    def boom(video, audio, out, ffmpeg=""):
        raise media_ffmpeg.MediaUnavailable("ffmpeg not found")

    extra = dict(EXTRA)
    out = merge_job(v, extra, fake_fetch([]), merge_fn=boom)
    assert out == v
    assert v.read_bytes() == b"video"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Clip.mp4"]
    assert extra["media_merge"] == {"status": "failed", "message": "ffmpeg not found"}


def test_fetch_failure_keeps_video(tmp_path):
    v = tmp_path / "Clip.mp4"
    v.write_bytes(b"video")

    def bad_fetch(url, dest):
        raise OSError("network down")

    extra = dict(EXTRA)
    assert merge_job(v, extra, bad_fetch, merge_fn=fake_merge) == v
    assert extra["media_merge"]["status"] == "failed"