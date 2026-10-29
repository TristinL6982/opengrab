"""M9g part 3: DownloadManager downloads DASH streams and merges the tracks."""
import inspect
from pathlib import Path

import pytest

from opengrab.engine.downloader import DownloadError
from opengrab.engine.manager import DownloadManager
from opengrab.media import ffmpeg as media_ffmpeg
from opengrab.models import Link, LinkStatus
from opengrab.settings import Settings

BASE = "https://cdn.example/v/"
URL = BASE + "manifest.mpd"

MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT8S">
 <Period>
  <AdaptationSet mimeType="video/mp4">
   <Representation id="v720" bandwidth="2800000" width="1280" height="720">
    <SegmentList><Initialization sourceURL="v720/init.mp4"/><SegmentURL media="v720/1.m4s"/><SegmentURL media="v720/2.m4s"/></SegmentList>
   </Representation>
  </AdaptationSet>
  <AdaptationSet mimeType="audio/mp4">
   <Representation id="a1" bandwidth="128000">
    <SegmentList><Initialization sourceURL="a1/init.mp4"/><SegmentURL media="a1/1.m4s"/></SegmentList>
   </Representation>
  </AdaptationSet>
 </Period>
</MPD>
"""

AUDIO_ONLY = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT4S">
 <Period>
  <AdaptationSet mimeType="audio/mp4">
   <Representation id="a1" bandwidth="128000">
    <SegmentList><SegmentURL media="a1/1.m4s"/></SegmentList>
   </Representation>
  </AdaptationSet>
 </Period>
</MPD>
"""

VIDEO = b"v720/init.mp4v720/1.m4sv720/2.m4s"
AUDIO = b"a1/init.mp4a1/1.m4s"


def make_mgr(manifest, calls):
    mgr = DownloadManager.__new__(DownloadManager)
    mgr.settings = Settings()
    mgr._broadcast = lambda kind, link=None: calls.append(("broadcast", kind))

    def fetch(url, headers):
        calls.append((url, dict(headers)))
        if url == URL:
            return manifest.encode("utf-8")
        return url[len(BASE):].encode("utf-8")

    mgr._fetch_url = fetch
    return mgr


def make_link():
    link = Link(url=URL)
    link.status = LinkStatus.DOWNLOADING
    return link


def ok_merge(merges):
    def merge(video, audio, out, ffmpeg=""):
        merges.append((Path(video).name, Path(audio).name, Path(out).name))
        with open(out, "wb") as fh:
            fh.write(Path(video).read_bytes() + b"|" + Path(audio).read_bytes())
    return merge


def bad_merge(video, audio, out, ffmpeg=""):
    raise media_ffmpeg.MergeError("boom")


def test_run_download_routes_dash():
    src = inspect.getsource(DownloadManager._run_download)
    assert "elif self._is_dash(link, url):" in src
    assert "self._download_dash(link, url, dest / filename)" in src


def test_is_dash_detection():
    plain = Link(url="http://x/page")
    assert DownloadManager._is_dash(plain, BASE + "manifest.mpd") is True
    assert DownloadManager._is_dash(plain, BASE + "MANIFEST.MPD?token=1") is True
    assert DownloadManager._is_dash(plain, BASE + "clip.mp4") is False
    hinted = Link(url="http://x/page")
    hinted.extra["media_protocol"] = "http_dash_segments"
    assert DownloadManager._is_dash(hinted, BASE + "clip") is True


def test_download_dash_merges_tracks(tmp_path):
    calls, merges = [], []
    mgr = make_mgr(MPD, calls)
    link = make_link()
    link.direct_headers = {"Referer": "http://x/"}
    out = mgr._download_dash(link, URL, tmp_path / "manifest.mpd", merge_fn=ok_merge(merges))
    assert out == tmp_path / "manifest.mp4"
    assert out.read_bytes() == VIDEO + b"|" + AUDIO
    assert merges == [("manifest.video.mp4", "manifest.audio.m4a", "manifest.mp4")]
    assert not (tmp_path / "manifest.video.mp4").exists()
    assert not (tmp_path / "manifest.audio.m4a").exists()
    assert link.extra["media_merge"] == {"status": "ok"}
    assert link.extra["dash"] == {"segments": 5, "bytes": len(VIDEO) + len(AUDIO)}
    assert link.progress == 1.0
    assert calls[0] == (URL, {"Referer": "http://x/"})
    assert calls.count(("broadcast", "progress")) == 5


def test_download_dash_merge_failure_keeps_video(tmp_path):
    mgr = make_mgr(MPD, [])
    link = make_link()
    out = mgr._download_dash(link, URL, tmp_path / "clip.mpd", merge_fn=bad_merge)
    assert out == tmp_path / "clip.mp4"
    assert out.read_bytes() == VIDEO
    assert (tmp_path / "clip.audio.m4a").read_bytes() == AUDIO
    assert link.extra["media_merge"] == {"status": "failed", "message": "boom"}


def test_download_dash_audio_only(tmp_path):
    merges = []
    mgr = make_mgr(AUDIO_ONLY, [])
    out = mgr._download_dash(make_link(), URL, tmp_path / "song.mpd", merge_fn=ok_merge(merges))
    assert out == tmp_path / "song.m4a"
    assert out.read_bytes() == b"a1/1.m4s"
    assert merges == []


def test_download_dash_cancel_is_download_error(tmp_path):
    mgr = make_mgr(MPD, [])
    link = make_link()
    link.status = LinkStatus.PAUSED
    with pytest.raises(DownloadError, match="cancel"):
        mgr._download_dash(link, URL, tmp_path / "clip.mpd", merge_fn=ok_merge([]))
    assert not (tmp_path / "clip.video.mp4").exists()


def test_download_dash_live_is_download_error(tmp_path):
    mgr = make_mgr(MPD.replace('type="static"', 'type="dynamic"'), [])
    with pytest.raises(DownloadError, match="DASH: live manifests are not supported"):
        mgr._download_dash(make_link(), URL, tmp_path / "clip.mpd", merge_fn=ok_merge([]))