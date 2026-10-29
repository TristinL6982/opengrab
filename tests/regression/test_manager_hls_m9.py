"""M9f part 2: DownloadManager downloads HLS streams with media.hlsdl."""
import inspect

import pytest

from opengrab.engine.downloader import DownloadError
from opengrab.engine.manager import DownloadManager
from opengrab.models import Link, LinkStatus
from opengrab.settings import Settings

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
#EXTINF:4.0,
s0.ts
#EXTINF:4.0,
s1.ts
#EXT-X-ENDLIST
"""


def make_mgr(data, calls):
    mgr = DownloadManager.__new__(DownloadManager)
    mgr.settings = Settings()
    mgr._broadcast = lambda kind, link=None: calls.append(("broadcast", kind))

    def fetch(url, headers):
        calls.append((url, dict(headers)))
        return data[url]

    mgr._fetch_url = fetch
    return mgr


def make_link(url):
    link = Link(url=url)
    link.status = LinkStatus.DOWNLOADING
    return link


def test_run_download_routes_hls():
    src = inspect.getsource(DownloadManager._run_download)
    assert "self._download_hls(link, url, dest / filename)" in src
    assert "self._finish_media(link, Path(path))" in src


def test_is_hls_detection():
    plain = Link(url="http://x/page")
    assert DownloadManager._is_hls(plain, BASE + "index.m3u8") is True
    assert DownloadManager._is_hls(plain, BASE + "INDEX.M3U8?token=1") is True
    assert DownloadManager._is_hls(plain, BASE + "clip.mp4") is False
    hinted = Link(url="http://x/page")
    hinted.extra["media_protocol"] = "m3u8_native"
    assert DownloadManager._is_hls(hinted, BASE + "clip") is True


def test_download_hls_writes_segments(tmp_path):
    calls = []
    url = BASE + "index.m3u8"
    data = {url: MEDIA.encode("utf-8"), BASE + "s0.ts": b"AA", BASE + "s1.ts": b"BBB"}
    mgr = make_mgr(data, calls)
    link = make_link(url)
    link.direct_headers = {"Referer": "http://x/"}
    out = tmp_path / "clip.ts"
    assert mgr._download_hls(link, url, out) == out
    assert out.read_bytes() == b"AABBB"
    assert link.extra["hls"] == {"segments": 2, "bytes": 5}
    assert link.downloaded == 5
    assert link.progress == 1.0
    assert calls[0] == (url, {"Referer": "http://x/"})
    assert calls.count(("broadcast", "progress")) == 2


def test_download_hls_uses_max_height(tmp_path):
    calls = []
    master = BASE + "master.m3u8"
    mid = BASE + "mid/index.m3u8"
    data = {master: MASTER.encode("utf-8"), mid: MEDIA.encode("utf-8"),
            BASE + "mid/s0.ts": b"x", BASE + "mid/s1.ts": b"y"}
    mgr = make_mgr(data, calls)
    mgr.settings.media_max_height = 720
    out = tmp_path / "clip.ts"
    mgr._download_hls(make_link(master), master, out)
    assert out.read_bytes() == b"xy"
    assert calls[1] == (mid, {})


def test_download_hls_cancel_is_download_error(tmp_path):
    url = BASE + "index.m3u8"
    mgr = make_mgr({url: MEDIA.encode("utf-8")}, [])
    link = make_link(url)
    link.status = LinkStatus.PAUSED
    with pytest.raises(DownloadError, match="cancel"):
        mgr._download_hls(link, url, tmp_path / "clip.ts")


def test_download_hls_live_is_download_error(tmp_path):
    url = BASE + "live.m3u8"
    mgr = make_mgr({url: b"#EXTM3U\n#EXTINF:4.0,\ns0.ts\n"}, [])
    with pytest.raises(DownloadError, match="HLS: live playlists are not supported"):
        mgr._download_hls(make_link(url), url, tmp_path / "clip.ts")