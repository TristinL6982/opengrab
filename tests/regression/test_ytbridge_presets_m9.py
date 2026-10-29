"""yt-dlp bridge uses quality presets (M9c)."""
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from opengrab.plugins.base import PluginError
from opengrab.plugins.builtin.ytbridge import YtDlpBridgePlugin
from opengrab.settings import Settings


class FakeYdl:
    def __init__(self, info):
        self.info = info

    def extract_info(self, _url, download=False):
        assert download is False
        return self.info


# Module dicts
V1080 = {"format_id": "137", "url": "http://x/137", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 1080, "tbr": 4000, "filesize": 900}
A128 = {"format_id": "140", "url": "http://x/140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "abr": 128, "filesize": 100}
P360 = {"format_id": "18", "url": "http://x/18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 360, "tbr": 500, "filesize": 300}


def run(info, preset, has_ffmpeg):
    ctx = SimpleNamespace(settings=Settings(timeout_s=5, proxy="", media_preset=preset), client=SimpleNamespace(_ua=lambda: "UA"))
    with patch("opengrab.plugins.builtin.ytbridge._get_ydl", return_value=FakeYdl(info)), patch("opengrab.media.ffmpeg.available", return_value=has_ffmpeg):
        return YtDlpBridgePlugin().grab("https://www.youtube.com/watch?v=abc", ctx)


def test_merge_with_ffmpeg():
    g = run({"title": "Clip", "formats": [P360, V1080, A128]}, "best", True)
    assert g.direct_url == "http://x/137"
    assert g.filename == "Clip.mp4"
    assert g.size == 900
    assert g.extra["media_mode"] == "merge"
    assert g.extra["media_ext"] == "mp4"
    assert g.extra["media_parts"] == [
        {"kind": "video", "url": "http://x/137", "ext": "mp4"},
        {"kind": "audio", "url": "http://x/140", "ext": "m4a"}
    ]


def test_no_ffmpeg_uses_progressive():
    g = run({"title": "Clip", "formats": [P360, V1080, A128]}, "best", False)
    assert g.direct_url == "http://x/18"
    assert g.extra["media_mode"] == "progressive"
    assert "media_parts" not in g.extra
    assert g.filename == "Clip.mp4"


def test_empty_preset_keeps_old_behaviour():
    with pytest.raises(PluginError, match="separate video and audio"):
        run({"title": "Clip", "formats": [V1080, A128]}, "", True)


def test_audio_preset():
    g = run({"title": "Clip", "formats": [P360, V1080, A128]}, "audio-m4a", False)
    assert g.direct_url == "http://x/140"
    assert g.filename == "Clip.m4a"
    assert g.extra["media_mode"] == "audio"


def test_no_format_under_cap():
    with pytest.raises(PluginError, match="no format at or below 480p"):
        run({"title": "Clip", "formats": [V1080, A128]}, "480", True)


def test_unknown_preset():
    with pytest.raises(PluginError, match="unknown preset"):
        run({"title": "Clip", "formats": [P360]}, "8k", True)


def test_direct_info_url_ignores_preset():
    g = run({"title": "Clip", "url": "http://x/direct", "ext": "mp4", "formats": [V1080, A128]}, "best", True)
    assert g.direct_url == "http://x/direct"
    assert "media_mode" not in g.extra


def test_settings_default():
    assert Settings().media_preset == ""