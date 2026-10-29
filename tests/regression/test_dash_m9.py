"""M9g part 1: MPEG-DASH manifest parsing."""
import pytest

from opengrab.media.dash import (DashError, fill_template, parse_duration,
                                 parse_mpd, pick_audio, pick_video)

URL = "https://cdn.example/v/manifest.mpd"

MPD1 = """<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT10S">
  <Period>
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <SegmentTemplate timescale="1000" duration="4000" startNumber="1" initialization="$RepresentationID$/init.mp4" media="$RepresentationID$/seg-$Number%03d$.m4s"/>
      <Representation id="v360" bandwidth="800000" width="640" height="360" codecs="avc1.4d401e"/>
      <Representation id="v720" bandwidth="2800000" width="1280" height="720" codecs="avc1.4d401f"/>
    </AdaptationSet>
    <AdaptationSet contentType="audio" mimeType="audio/mp4">
      <Representation id="a1" bandwidth="128000" codecs="mp4a.40.2">
        <SegmentTemplate timescale="48000" initialization="a/init.mp4" media="a/$Time$.m4s">
          <SegmentTimeline>
            <S t="0" d="192000" r="1"/>
            <S d="96000"/>
          </SegmentTimeline>
        </SegmentTemplate>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""

MPD2 = """<MPD type="static" mediaPresentationDuration="PT8S">
  <BaseURL>https://media.example/base/</BaseURL>
  <Period>
    <AdaptationSet mimeType="video/mp4">
      <Representation id="v" bandwidth="1000" width="320" height="240">
        <BaseURL>video.mp4</BaseURL>
      </Representation>
    </AdaptationSet>
    <AdaptationSet mimeType="audio/mp4">
      <Representation id="a" bandwidth="64000">
        <SegmentList>
          <Initialization sourceURL="a-init.mp4"/>
          <SegmentURL media="a-1.m4s"/>
          <SegmentURL media="a-2.m4s"/>
        </SegmentList>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""


def test_parse_duration():
    assert parse_duration("PT1M30.5S") == 90.5
    assert parse_duration("P1DT1H") == 90000.0
    with pytest.raises(DashError):
        parse_duration("bogus")
    with pytest.raises(DashError):
        parse_duration("PT")


def test_fill_template():
    assert fill_template("$RepresentationID$-$Number%05d$-$$.m4s", "v1", 0, 7) == "v1-00007-$.m4s"
    assert fill_template("$Bandwidth$/$Time$", "x", 500, None, 10) == "500/10"
    with pytest.raises(DashError, match="Number"):
        fill_template("seg-$Number$.m4s", "v1", 0)


def test_template_number_and_timeline():
    info = parse_mpd(MPD1, URL)
    assert info["duration"] == 10.0
    reps = info["representations"]
    assert [r["id"] for r in reps] == ["v360", "v720", "a1"]
    v720 = reps[1]
    assert v720["kind"] == "video"
    assert v720["height"] == 720
    assert v720["width"] == 1280
    assert v720["codecs"] == "avc1.4d401f"
    assert v720["init"] == "https://cdn.example/v/v720/init.mp4"
    assert v720["segments"] == ["https://cdn.example/v/v720/seg-001.m4s",
                                "https://cdn.example/v/v720/seg-002.m4s",
                                "https://cdn.example/v/v720/seg-003.m4s"]
    a1 = reps[2]
    assert a1["kind"] == "audio"
    assert a1["mime"] == "audio/mp4"
    assert a1["init"] == "https://cdn.example/v/a/init.mp4"
    assert a1["segments"] == ["https://cdn.example/v/a/0.m4s",
                              "https://cdn.example/v/a/192000.m4s",
                              "https://cdn.example/v/a/384000.m4s"]


def test_base_url_and_segment_list():
    reps = parse_mpd(MPD2, URL)["representations"]
    v, a = reps
    assert v["kind"] == "video"
    assert v["init"] is None
    assert v["segments"] == ["https://media.example/base/video.mp4"]
    assert a["kind"] == "audio"
    assert a["init"] == "https://media.example/base/a-init.mp4"
    assert a["segments"] == ["https://media.example/base/a-1.m4s",
                             "https://media.example/base/a-2.m4s"]


def test_pick_video_and_audio():
    reps = parse_mpd(MPD1, URL)["representations"]
    assert pick_video(reps)["id"] == "v720"
    assert pick_video(reps, max_height=480)["id"] == "v360"
    assert pick_video(reps, max_height=200)["id"] == "v360"
    assert pick_audio(reps)["id"] == "a1"
    audio_only = [r for r in reps if r["kind"] == "audio"]
    assert pick_video(audio_only) is None
    assert pick_audio([r for r in reps if r["kind"] == "video"]) is None


def test_rejects_bad_manifests():
    with pytest.raises(DashError, match="live manifests are not supported"):
        parse_mpd('<MPD type="dynamic"><Period/></MPD>', URL)
    enc = ('<MPD type="static" mediaPresentationDuration="PT4S"><Period>'
           '<AdaptationSet mimeType="video/mp4">'
           '<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011"/>'
           '<Representation id="v" bandwidth="1"/></AdaptationSet></Period></MPD>')
    with pytest.raises(DashError, match="encrypted streams are not supported"):
        parse_mpd(enc, URL)
    with pytest.raises(DashError, match="no Period"):
        parse_mpd('<MPD type="static"/>', URL)
    with pytest.raises(DashError, match="bad manifest"):
        parse_mpd("not xml", URL)
    nodur = ('<MPD type="static"><Period><AdaptationSet mimeType="video/mp4">'
             '<SegmentTemplate media="s-$Number$.m4s"/>'
             '<Representation id="v" bandwidth="1"/></AdaptationSet></Period></MPD>')
    with pytest.raises(DashError, match="cannot count segments"):
        parse_mpd(nodur, URL)