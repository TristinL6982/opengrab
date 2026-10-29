"""HLS playlist parsing helpers (M9e)."""
from opengrab.media import hls

MASTER = (
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2"\n'
    "low/index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\n"
    "http://cdn/x/720.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\n"
    "1080.m3u8\n"
)

MEDIA = (
    "#EXTM3U\n"
    "#EXT-X-VERSION:7\n"
    "#EXT-X-TARGETDURATION:6\n"
    "#EXT-X-MEDIA-SEQUENCE:10\n"
    '#EXT-X-MAP:URI="init.mp4"\n'
    "#EXTINF:6.0,\n"
    "seg10.m4s\n"
    '#EXT-X-KEY:METHOD=AES-128,URI="https://k/key.bin",IV=0x0000000000000000000000000000000A\n'
    "#EXTINF:4.5,title\n"
    "seg11.m4s\n"
    "#EXT-X-ENDLIST\n"
)


def test_parse_attrs_quoted_commas():
    assert hls.parse_attrs('A=1,B="x,y",C-D=z') == {"A": "1", "B": "x,y", "C-D": "z"}


def test_is_master():
    assert hls.is_master(MASTER) is True
    assert hls.is_master(MEDIA) is False


def test_parse_master():
    vs = hls.parse_master(MASTER, "http://h/v/master.m3u8")
    assert len(vs) == 3
    assert vs[0] == {"url": "http://h/v/low/index.m3u8", "bandwidth": 800000, "width": 640,
                     "height": 360, "codecs": "avc1.4d401e,mp4a.40.2"}
    assert vs[1]["url"] == "http://cdn/x/720.m3u8"
    assert vs[2]["url"] == "http://h/v/1080.m3u8"
    assert vs[2]["height"] == 1080


def test_pick_variant():
    vs = hls.parse_master(MASTER, "http://h/v/master.m3u8")
    assert hls.pick_variant(vs)["height"] == 1080
    assert hls.pick_variant(vs, 720)["height"] == 720
    assert hls.pick_variant(vs, 240)["height"] == 360
    assert hls.pick_variant([]) is None


def test_parse_media():
    r = hls.parse_media(MEDIA, "http://h/v/720/index.m3u8")
    assert r["target_duration"] == 6
    assert r["media_sequence"] == 10
    assert r["endlist"] is True
    assert r["init"] == "http://h/v/720/init.mp4"
    assert r["duration"] == 10.5
    segs = r["segments"]
    assert len(segs) == 2
    assert segs[0] == {"url": "http://h/v/720/seg10.m4s", "duration": 6.0, "seq": 10, "key": None}
    assert segs[1]["seq"] == 11
    assert segs[1]["duration"] == 4.5
    assert segs[1]["key"] == {"method": "AES-128", "uri": "https://k/key.bin",
                              "iv": "0x0000000000000000000000000000000A"}


def test_live_playlist_has_no_endlist():
    r = hls.parse_media("#EXTM3U\n#EXTINF:2,\na.ts\n", "http://h/live.m3u8")
    assert r["endlist"] is False
    assert r["segments"][0]["url"] == "http://h/a.ts"
