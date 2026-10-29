"""M9g part 2: media.dashdl downloads static DASH streams with injected fetchers."""
import pytest

from opengrab.media.dash import DashError
from opengrab.media.dashdl import download_dash, download_rep

BASE = "https://cdn.example/v/"
URL = BASE + "manifest.mpd"

MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT8S">
 <Period>
  <AdaptationSet mimeType="video/mp4">
   <Representation id="v360" bandwidth="800000" width="640" height="360">
    <SegmentList><Initialization sourceURL="v360/init.mp4"/><SegmentURL media="v360/1.m4s"/><SegmentURL media="v360/2.m4s"/></SegmentList>
   </Representation>
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


def fetchers(manifest, calls):
    def fetch_text(u):
        calls.append(u)
        return manifest

    def fetch_bytes(u):
        calls.append(u)
        return u[len(BASE):].encode("utf-8")

    return fetch_bytes, fetch_text


def test_download_dash_best_tracks(tmp_path):
    calls, prog = [], []
    fb, ft = fetchers(MPD, calls)
    v, a = tmp_path / "v.mp4", tmp_path / "a.m4a"
    info = download_dash(URL, str(v), str(a), fb, ft,
                         on_progress=lambda i, n, b: prog.append((i, n, b)))
    assert v.read_bytes() == b"v720/init.mp4v720/1.m4sv720/2.m4s"
    assert a.read_bytes() == b"a1/init.mp4a1/1.m4s"
    assert info["video"] == str(v)
    assert info["audio"] == str(a)
    assert info["segments"] == 5
    assert info["bytes"] == len(v.read_bytes()) + len(a.read_bytes())
    assert info["duration"] == 8.0
    assert calls[0] == URL
    assert len(prog) == 5
    assert prog[-1] == (5, 5, info["bytes"])


def test_download_dash_max_height(tmp_path):
    fb, ft = fetchers(MPD, [])
    v = tmp_path / "v.mp4"
    download_dash(URL, str(v), str(tmp_path / "a.m4a"), fb, ft, max_height=480)
    assert v.read_bytes() == b"v360/init.mp4v360/1.m4sv360/2.m4s"


def test_download_dash_audio_only(tmp_path):
    fb, ft = fetchers(AUDIO_ONLY, [])
    v, a = tmp_path / "v.mp4", tmp_path / "a.m4a"
    info = download_dash(URL, str(v), str(a), fb, ft)
    assert info["video"] is None
    assert info["audio"] == str(a)
    assert info["segments"] == 1
    assert a.read_bytes() == b"a1/1.m4s"
    assert not v.exists()


def test_download_dash_cancel(tmp_path):
    fb, ft = fetchers(MPD, [])
    with pytest.raises(DashError, match="cancelled"):
        download_dash(URL, str(tmp_path / "v.mp4"), str(tmp_path / "a.m4a"),
                      fb, ft, cancel=lambda: True)


def test_download_dash_rejects_live(tmp_path):
    fb, ft = fetchers(MPD.replace('type="static"', 'type="dynamic"'), [])
    with pytest.raises(DashError, match="live"):
        download_dash(URL, str(tmp_path / "v.mp4"), str(tmp_path / "a.m4a"), fb, ft)


def test_download_rep_without_init(tmp_path):
    parts = []
    rep = {"init": None, "segments": [BASE + "x/1.m4s", BASE + "x/2.m4s"]}
    out = tmp_path / "x.bin"
    n = download_rep(rep, str(out), lambda u: u[len(BASE):].encode("utf-8"), parts.append)
    assert out.read_bytes() == b"x/1.m4sx/2.m4s"
    assert n == 14
    assert parts == [7, 7]