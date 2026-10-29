"""M9f: HLS segment downloader."""
import pytest

from opengrab.media.hlsdl import HlsError, download_hls, resolve_media

BASE = "https://cdn.example/v/"

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
mid/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
high/index.m3u8
"""

MEDIA = """#EXTM3U
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:4.0,
s0.ts
#EXTINF:4.0,
s1.ts
#EXTINF:2.0,
s2.ts
#EXT-X-ENDLIST
"""


def make_fetchers(texts, blobs, calls):
    def fetch_text(u):
        calls.append(u)
        return texts[u]

    def fetch_bytes(u):
        calls.append(u)
        return blobs[u]

    return fetch_text, fetch_bytes


def test_media_playlist_concatenates_in_order(tmp_path):
    calls = []
    url = BASE + "index.m3u8"
    blobs = {BASE + "s0.ts": b"AA", BASE + "s1.ts": b"BBB", BASE + "s2.ts": b"C"}
    ft, fb = make_fetchers({url: MEDIA}, blobs, calls)
    progress = []
    dest = tmp_path / "out.ts"
    res = download_hls(url, str(dest), fb, ft, on_progress=lambda i, t, w: progress.append((i, t, w)))
    assert dest.read_bytes() == b"AABBBC"
    assert res == {"media_url": url, "segments": 3, "bytes": 6, "duration": 10.0}
    assert progress == [(1, 3, 2), (2, 3, 5), (3, 3, 6)]


def test_master_picks_variant_within_max_height(tmp_path):
    calls = []
    master = BASE + "master.m3u8"
    mid = BASE + "mid/index.m3u8"
    blobs = {BASE + "mid/s0.ts": b"x", BASE + "mid/s1.ts": b"y", BASE + "mid/s2.ts": b"z"}
    ft, fb = make_fetchers({master: MASTER, mid: MEDIA}, blobs, calls)
    res = download_hls(master, str(tmp_path / "o.ts"), fb, ft, max_height=720)
    assert res["media_url"] == mid
    assert (tmp_path / "o.ts").read_bytes() == b"xyz"
    assert calls[:2] == [master, mid]


def test_resolve_media_without_master():
    calls = []
    url = BASE + "index.m3u8"
    ft, _ = make_fetchers({url: MEDIA}, {}, calls)
    media_url, info = resolve_media(url, ft)
    assert media_url == url
    assert len(info["segments"]) == 3


def test_init_segment_written_first(tmp_path):
    calls = []
    url = BASE + "fmp4.m3u8"
    text = "#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\"\n#EXTINF:4.0,\nseg1.m4s\n#EXT-X-ENDLIST\n"
    ft, fb = make_fetchers({url: text}, {BASE + "init.mp4": b"II", BASE + "seg1.m4s": b"SS"}, calls)
    res = download_hls(url, str(tmp_path / "o.mp4"), fb, ft)
    assert (tmp_path / "o.mp4").read_bytes() == b"IISS"
    assert res["bytes"] == 4


def test_live_playlist_rejected(tmp_path):
    url = BASE + "live.m3u8"
    text = "#EXTM3U\n#EXTINF:4.0,\ns0.ts\n"
    ft, fb = make_fetchers({url: text}, {}, [])
    with pytest.raises(HlsError, match="live"):
        download_hls(url, str(tmp_path / "o.ts"), fb, ft)


def test_encrypted_playlist_rejected(tmp_path):
    url = BASE + "enc.m3u8"
    text = "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"k.bin\"\n#EXTINF:4.0,\ns0.ts\n#EXT-X-ENDLIST\n"
    ft, fb = make_fetchers({url: text}, {}, [])
    with pytest.raises(HlsError, match="AES-128"):
        download_hls(url, str(tmp_path / "o.ts"), fb, ft)


def test_cancel_stops_before_fetching_segments(tmp_path):
    calls = []
    url = BASE + "index.m3u8"
    ft, fb = make_fetchers({url: MEDIA}, {}, calls)
    with pytest.raises(HlsError, match="cancelled"):
        download_hls(url, str(tmp_path / "o.ts"), fb, ft, cancel=lambda: True)
    assert calls == [url]


def test_empty_master_raises():
    url = BASE + "m.m3u8"
    ft, _ = make_fetchers({url: "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n"}, {}, [])
    with pytest.raises(HlsError, match="no variants"):
        resolve_media(url, ft)