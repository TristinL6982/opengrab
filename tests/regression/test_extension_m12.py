"""M12e: the MV3 browser extension base ships valid, consistent files."""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXT = os.path.join(ROOT, "extension")


def _read(name):
    with open(os.path.join(EXT, name), "r", encoding="utf-8") as f:
        return f.read()


def _manifest():
    return json.loads(_read("manifest.json"))


def test_manifest_is_mv3_with_needed_permissions():
    m = _manifest()
    assert m["manifest_version"] == 3
    assert m["name"] == "OpenGrab"
    assert re.fullmatch(r"\d+\.\d+\.\d+", m["version"])
    assert {"contextMenus", "storage"} <= set(m["permissions"])
    assert "http://127.0.0.1/*" in m["host_permissions"]
    assert "<all_urls>" not in m.get("host_permissions", [])


def test_manifest_points_at_existing_files():
    m = _manifest()
    assert m["background"]["service_worker"] == "background.js"
    assert "scripts" not in m["background"]
    for name in (m["background"]["service_worker"], m["options_page"], "options.js"):
        assert os.path.isfile(os.path.join(EXT, name)), name


def test_background_registers_context_menu_for_link_page_selection():
    js = _read("background.js")
    assert "Download with OpenGrab" in js
    assert re.search(r'contexts:\s*\[\s*"link",\s*"page",\s*"selection"\s*\]', js)
    assert "chrome.contextMenus.onClicked.addListener" in js


def test_background_posts_urls_with_device_token():
    js = _read("background.js")
    assert '"/api/urls"' in js
    assert '"Bearer " + s.deviceToken' in js
    assert "chrome.storage.local.get" in js
    assert "eval(" not in js


def test_options_page_stores_app_url_and_token():
    html = _read("options.html")
    js = _read("options.js")
    assert 'id="appUrl"' in html and 'id="deviceToken"' in html
    assert '<script src="options.js"></script>' in html
    assert "chrome.storage.local.set" in js
    assert "appUrl" in js and "deviceToken" in js
    assert "<script>" not in html  # MV3 CSP forbids inline scripts