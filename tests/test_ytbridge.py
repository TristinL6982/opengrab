"""Media bridge playlist selection and quality policy checks."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from opengrab.plugins.base import PluginError
from opengrab.plugins.builtin.ytbridge import YtDlpBridgePlugin
from opengrab.engine.manager import DownloadManager
from opengrab.models import Link
from opengrab.settings import Settings


class FakeYdl:
    def __init__(self, info):
        self.info = info
        self.opts = None

    def extract_info(self, _url, download=False):
        assert download is False
        return self.info


def main() -> int:
    plugin = YtDlpBridgePlugin()
    playlist = {"_type": "playlist", "title": "Sample", "entries": [
        {"id": f"v{i}", "ie_key": "Youtube", "title": f"Video {i}"}
        for i in range(5)
    ]}
    fake = FakeYdl(playlist)
    ctx = SimpleNamespace(settings=Settings(
        timeout_s=5, media_playlist_max_entries=3, proxy=""))
    with patch("opengrab.plugins.builtin.ytbridge._get_ydl", return_value=fake) as get_ydl:
        result = plugin.grab("https://www.youtube.com/playlist?list=sample", ctx)
    opts = get_ydl.call_args.args[0]
    assert opts["noplaylist"] is False and opts["extract_flat"] == "in_playlist"
    assert opts["playlistend"] == 4
    assert result.children == [
        (f"https://www.youtube.com/watch?v=v{i}", f"Video {i}") for i in range(3)]
    assert result.extra == {"playlist_truncated": True, "playlist_count": 3}
    parent = Link(url="https://www.youtube.com/playlist?list=sample")
    DownloadManager._apply_grab(None, parent, plugin.id, result)
    assert parent.extra["playlist_truncated"] is True
    assert parent.children[0] == ["https://www.youtube.com/watch?v=v0", "Video 0"]
    print("[PASS] playlist children are valid item URLs and the configured cap is visible")

    formats = [
        {"url": "https://cdn.invalid/1080.mp4", "height": 1080,
         "vcodec": "h264", "acodec": "aac", "tbr": 3000},
        {"url": "https://cdn.invalid/720.mp4", "height": 720,
         "vcodec": "h264", "acodec": "aac", "tbr": 1500},
    ]
    selected = plugin._pick_url({"formats": formats}, max_height=720)
    assert selected["url"].endswith("720.mp4")
    print("[PASS] maximum progressive resolution is respected")

    try:
        plugin._pick_url({"formats": [
            {"url": "https://cdn.invalid/video", "height": 1080,
             "vcodec": "h264", "acodec": "none"},
            {"url": "https://cdn.invalid/audio", "vcodec": "none",
             "acodec": "aac"},
        ]})
        raise AssertionError("separate tracks must not silently drop audio")
    except PluginError as exc:
        assert "separate video and audio" in str(exc)
    print("[PASS] separate audio/video streams fail with an actionable reason")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
