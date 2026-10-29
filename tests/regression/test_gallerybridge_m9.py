"""gallery-dl bridge (M9h part 1)."""
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from opengrab.plugins.base import PluginError
from opengrab.plugins.builtin import gallerybridge
from opengrab.plugins.builtin.gallerybridge import (GalleryDlBridgePlugin, filename_for,
                                                    host_matches, parse_urls, run_gallery_dl)
from opengrab.settings import Settings

CTX = SimpleNamespace(settings=Settings(timeout_s=5, proxy=""))


def test_host_matches():
    assert host_matches("https://www.deviantart.com/x/art/1")
    assert host_matches("https://i.imgur.com/a.png")
    assert not host_matches("https://example.com/imgur.com")
    assert not host_matches("https://notimgur.com/a")


def test_parse_urls_skips_noise_and_dupes():
    out = "https://a/1.jpg\n| https://b/1.jpg\n\nhttps://a/2.png\nhttps://a/1.jpg\nwarning\n"
    assert parse_urls(out) == ["https://a/1.jpg", "https://a/2.png"]


def test_filename_for():
    assert filename_for("https://a/x/My%20Pic.jpg?s=1", 3) == "My Pic.jpg"
    assert filename_for("https://a/", 4) == "file_4"


def test_run_missing_tool():
    with patch("shutil.which", return_value=None):
        with pytest.raises(PluginError, match="not installed"):
            run_gallery_dl("https://imgur.com/a/x")


def test_run_builds_command():
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="https://a/1.jpg\n", stderr="")

    with patch("shutil.which", return_value="C:/t/gallery-dl.exe"), patch("subprocess.run", side_effect=fake_run):
        out = run_gallery_dl("https://imgur.com/a/x", timeout=5, proxy="http://p:1")
    assert out == "https://a/1.jpg\n"
    assert seen["cmd"] == ["C:/t/gallery-dl.exe", "-g", "--proxy", "http://p:1", "https://imgur.com/a/x"]


def test_run_failure_raises():
    fake = subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
    with patch("shutil.which", return_value="g"), patch("subprocess.run", return_value=fake):
        with pytest.raises(PluginError, match="boom"):
            run_gallery_dl("https://imgur.com/a/x")


def test_grab_many_gives_children():
    with patch.object(gallerybridge, "run_gallery_dl", return_value="https://a/1.jpg\nhttps://a/2.jpg\n"):
        g = GalleryDlBridgePlugin().grab("https://imgur.com/a/x", CTX)
    assert g.children == [("https://a/1.jpg", "1.jpg"), ("https://a/2.jpg", "2.jpg")]
    assert g.direct_url is None
    assert g.extra["gallery_count"] == 2


def test_grab_single_is_direct_and_resolve():
    with patch.object(gallerybridge, "run_gallery_dl", return_value="https://a/1.jpg\n"):
        g = GalleryDlBridgePlugin().grab("https://imgur.com/a/x", CTX)
        r = GalleryDlBridgePlugin().resolve("https://imgur.com/a/x", CTX)
    assert g.direct_url == "https://a/1.jpg"
    assert r.url == "https://a/1.jpg"
    assert r.filename == "1.jpg"


def test_grab_empty_raises():
    with patch.object(gallerybridge, "run_gallery_dl", return_value="nothing\n"):
        with pytest.raises(PluginError, match="no files"):
            GalleryDlBridgePlugin().grab("https://imgur.com/a/x", CTX)