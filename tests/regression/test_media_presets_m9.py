import pytest
from opengrab.media import presets as pr

P360 = {"format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 360, "tbr": 500, "url": "http://x/18"}
P720 = {"format_id": "22", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 720, "tbr": 1500, "url": "http://x/22"}
V1080 = {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 1080, "tbr": 4000, "url": "http://x/137"}
V1080B = {"format_id": "136", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 1080, "tbr": 2500, "url": "http://x/136"}
V2160 = {"format_id": "313", "ext": "webm", "vcodec": "vp9", "acodec": "none", "height": 2160, "tbr": 15000, "url": "http://x/313"}
A128 = {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "abr": 128, "url": "http://x/140"}
A160 = {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 160, "url": "http://x/251"}
NOURL = {"format_id": "999", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 4320, "tbr": 99999}

INFO = {"formats": [NOURL, P360, P720, V1080B, V1080, V2160, A128, A160]}


def test_preset_names():
    assert pr.preset_names() == ["best", "2160", "1440", "1080", "720", "480", "audio-m4a", "audio-mp3"]


def test_unknown_preset():
    with pytest.raises(ValueError, match="unknown preset"):
        pr.select_formats(INFO, "8k", True)


def test_best_merge():
    r = pr.select_formats(INFO, "best", True)
    assert r["mode"] == "merge"
    assert r["video"]["format_id"] == "313"
    assert r["audio"]["format_id"] == "251"
    assert r["ext"] == "webm"


def test_1080_merge_prefers_m4a_and_tbr():
    r = pr.select_formats(INFO, "1080", True)
    assert r["mode"] == "merge"
    assert r["video"]["format_id"] == "137"
    assert r["audio"]["format_id"] == "140"
    assert r["ext"] == "mp4"


def test_720_progressive_when_no_taller_video():
    r = pr.select_formats(INFO, "720", True)
    assert r["mode"] == "progressive"
    assert r["video"]["format_id"] == "22"
    assert r["audio"] is None
    assert r["ext"] == "mp4"


def test_best_without_ffmpeg_is_progressive():
    r = pr.select_formats(INFO, "best", False)
    assert r["mode"] == "progressive"
    assert r["video"]["format_id"] == "22"


def test_480_progressive():
    r = pr.select_formats(INFO, "480", True)
    assert r["mode"] == "progressive"
    assert r["video"]["format_id"] == "18"


def test_formats_without_url_ignored():
    r = pr.select_formats({"formats": [NOURL, P360]}, "best", True)
    assert r["mode"] == "progressive"
    assert r["video"]["format_id"] == "18"


def test_no_format_under_cap():
    with pytest.raises(pr.NoFormat, match="480p"):
        pr.select_formats({"formats": [V1080, A128]}, "480", True)


def test_no_formats_at_all():
    with pytest.raises(pr.NoFormat, match="no downloadable format"):
        pr.select_formats({}, "best", True)


def test_merge_without_progressive():
    r = pr.select_formats({"formats": [V1080, A160]}, "best", True)
    assert r["mode"] == "merge"
    assert r["video"]["format_id"] == "137"
    assert r["audio"]["format_id"] == "251"
    assert r["ext"] == "mkv"


def test_audio_m4a_prefers_ext():
    r = pr.select_formats(INFO, "audio-m4a", False)
    assert r["mode"] == "audio"
    assert r["video"] is None
    assert r["audio"]["format_id"] == "140"
    assert r["ext"] == "m4a"


def test_audio_mp3_best_abr():
    r = pr.select_formats(INFO, "audio-mp3", True)
    assert r["mode"] == "audio"
    assert r["audio"]["format_id"] == "251"
    assert r["ext"] == "mp3"


def test_audio_mp3_needs_ffmpeg():
    with pytest.raises(pr.NoFormat, match="ffmpeg"):
        pr.select_formats(INFO, "audio-mp3", False)


def test_audio_without_audio_only():
    with pytest.raises(pr.NoFormat, match="audio-only"):
        pr.select_formats({"formats": [P360]}, "audio-m4a", True)


def test_presets_dict_kinds():
    assert pr.PRESETS["720"] == {"kind": "video", "max_height": 720}
    assert pr.PRESETS["audio-mp3"] == {"kind": "audio", "ext": "mp3"}