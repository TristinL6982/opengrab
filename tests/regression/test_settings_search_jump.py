"""Settings search box, jump-to-setting helper and the machine-readable
"fix this in Settings" hint on download errors."""
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from opengrab.engine import torrents
from opengrab.models import Link, LinkStatus

STATIC = Path(__file__).resolve().parents[2] / "src" / "opengrab" / "ui" / "static"
NODE = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"


def _read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_torrent_missing_sets_fix_setting_hint(monkeypatch):
    svc = torrents.TorrentService(SimpleNamespace(settings=SimpleNamespace()))
    monkeypatch.setattr(svc, "ensure_session", lambda: False)
    link = Link(url="magnet:?xt=urn:btih:abc")
    svc.add_link(link, magnet=link.url)
    assert link.status == LinkStatus.ERROR
    assert link.error == torrents.NOT_INSTALLED
    assert link.extra["fix_setting"] == "addon-torrent"


def test_search_markup_and_js_exist():
    html, js, css = _read("index.html"), _read("app.js"), _read("style.css")
    assert 'id="settings-search"' in html and 'aria-label="Search settings"' in html
    assert 'id="settings-search-clear"' in html and 'id="settings-search-empty"' in html
    assert html.index('id="settings-search"') > html.index('id="tab-settings"')
    assert html.index('id="settings-search"') < html.index("<h2>Appearance</h2>")
    for needle in ("OG_SETTINGS_SEARCH", "sf-mark", "sf-hide", "No settings match",
                   "'Escape'", 'key.toLowerCase() === "f"'):
        assert needle in js, needle
    assert ".sf-hide" in css and "mark.sf-mark" in css


def test_jump_to_setting_hook():
    html, js, css = _read("index.html"), _read("app.js"), _read("style.css")
    assert "function openSetting(" in js
    assert 'data-act="fix-setting"' in js and "l.extra?.fix_setting" in js
    assert '"addon-torrent"' in js and "#btn-addon-torrent-install" in js
    assert 'id="btn-addon-torrent-install"' in html
    assert ".setting-flash" in css and "prefers-reduced-motion" in css


def test_new_strings_are_translated():
    import json
    zh = json.loads(_read("i18n/zh-CN.json"))["strings"]
    for s in ("Search settings", "Search settings… (Ctrl+F)", "Clear search",
              'No settings match "{q}".', "Install BitTorrent"):
        assert zh.get(s), s


def test_app_js_parses():
    r = subprocess.run([NODE, "--check", str(STATIC / "app.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
