"""M12h: the extension's Download All sends every page link to the LinkGrabber."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXT = os.path.join(ROOT, "extension")


def _read(name):
    with open(os.path.join(EXT, name), "r", encoding="utf-8") as f:
        return f.read()


def test_manifest_allows_page_scripting():
    m = json.loads(_read("manifest.json"))
    assert {"scripting", "activeTab"} <= set(m["permissions"])
    assert "<all_urls>" not in m.get("host_permissions", [])


def test_download_all_menu_registered():
    b = _read("background.js")
    assert b.count('id: "opengrab-download-all"') == 1
    assert "Download all links with OpenGrab" in b
    assert 'const MENU_ALL_ID = "opengrab-download-all";' in b


def test_download_all_collects_links_and_media():
    b = _read("background.js")
    assert "function collectPageLinks()" in b
    assert 'querySelectorAll("a[href]")' in b
    for tag in ("img[src]", "video[src]", "audio[src]", "source[src]"):
        assert tag in b


def test_download_all_filters_and_goes_to_linkgrabber():
    b = _read("background.js")
    assert "function filterPageLinks(urls, pattern)" in b
    assert "DOWNLOAD_ALL_MAX = 200" in b
    assert "chrome.scripting.executeScript" in b
    assert "downloadAllPattern" in b
    # review in the LinkGrabber, never auto-start
    assert "auto_download: false" in b
    assert b.count("chrome.contextMenus.onClicked.addListener") == 2
