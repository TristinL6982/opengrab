"""DownloadManager merges the audio part after a merge-mode download (M9d part 2)."""
import inspect
from pathlib import Path

from opengrab.engine.manager import DownloadManager
from opengrab.media import ffmpeg as media_ffmpeg
from opengrab.models import Link
from opengrab.settings import Settings

EXTRA = {"media_mode": "merge", "media_ext": "mp4", "media_parts": [
    {"kind": "video", "url": "http://x/v", "ext": "mp4"},
    {"kind": "audio", "url": "http://x/a", "ext": "m4a"}]}


def make_mgr(calls):
    mgr = DownloadManager.__new__(DownloadManager)
    mgr.settings = Settings()

    def fetch(url, dest, headers):
        calls.append((url, Path(dest).name, headers))
        Path(dest).write_bytes(b"audio")

    mgr._fetch_media_part = fetch
    return mgr


def fake_merge(video, audio, out, ffmpeg=""):
    Path(out).write_bytes(Path(video).read_bytes() + Path(audio).read_bytes())
    return out


def test_run_download_calls_finish_media():
    assert "self._finish_media(link, Path(path))" in inspect.getsource(DownloadManager._run_download)


def test_not_merge_unchanged(tmp_path):
    v = tmp_path / "Clip.mp4"
    v.write_bytes(b"video")
    calls = []
    link = Link(url="http://x/page")
    assert make_mgr(calls)._finish_media(link, v) == v
    assert calls == []
    assert "media_merge" not in link.extra


def test_merge_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(media_ffmpeg, "merge", fake_merge)
    v = tmp_path / "Clip.mp4"
    v.write_bytes(b"video")
    calls = []
    link = Link(url="http://x/page")
    link.extra.update(EXTRA)
    link.direct_headers = {"Referer": "http://x/"}
    assert make_mgr(calls)._finish_media(link, v) == v
    assert v.read_bytes() == b"videoaudio"
    assert calls == [("http://x/a", "Clip.audio.m4a", {"Referer": "http://x/"})]
    assert link.extra["media_merge"] == {"status": "ok"}


def test_ffmpeg_path_setting_used(tmp_path, monkeypatch):
    seen = []

    def merge(video, audio, out, ffmpeg=""):
        seen.append(ffmpeg)
        return fake_merge(video, audio, out)

    monkeypatch.setattr(media_ffmpeg, "merge", merge)
    v = tmp_path / "Clip.mp4"
    v.write_bytes(b"video")
    mgr = make_mgr([])
    mgr.settings.media_ffmpeg_path = "C:/ff/ffmpeg.exe"
    link = Link(url="http://x/page")
    link.extra.update(EXTRA)
    mgr._finish_media(link, v)
    assert seen == ["C:/ff/ffmpeg.exe"]


def test_ffmpeg_missing_keeps_video(tmp_path, monkeypatch):
    def boom(video, audio, out, ffmpeg=""):
        raise media_ffmpeg.MediaUnavailable("ffmpeg not found")

    monkeypatch.setattr(media_ffmpeg, "merge", boom)
    v = tmp_path / "Clip.mp4"
    v.write_bytes(b"video")
    link = Link(url="http://x/page")
    link.extra.update(EXTRA)
    assert make_mgr([])._finish_media(link, v) == v
    assert v.read_bytes() == b"video"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Clip.mp4"]
    assert link.extra["media_merge"] == {"status": "failed", "message": "ffmpeg not found"}