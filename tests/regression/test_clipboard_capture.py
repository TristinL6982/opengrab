"""Copied links reach the LinkGrabber and the UI is told, so it can show a
toast and a count (before, they were added silently and looked lost)."""
import time

from tests.helpers.apiserver import req, running_server


def test_copied_links_are_announced_checked_and_deduplicated():
    with running_server({"clipboard_monitor_enabled": True}) as srv:
        events = []
        orig = srv.broadcast
        srv.broadcast = lambda kind, payload: (events.append((kind, payload)), orig(kind, payload))
        text = "see https://example.invalid/a.zip and magnet:?xt=urn:btih:abc, also https://example.invalid/a.zip"
        assert srv.capture_clipboard_text(text) == 2
        assert ("linkgrabber", {"added": 2, "source": "clipboard"}) in events
        items = req(srv, "GET", "/api/linkgrabber")[1]
        assert {i["url"] for i in items} == {"https://example.invalid/a.zip", "magnet:?xt=urn:btih:abc"}
        assert all(i["source"] == "clipboard" for i in items)
        assert srv.capture_clipboard_text(text) == 0  # copying the same text again adds nothing

        srv.manager.settings.clipboard_monitor_enabled = False
        assert srv.capture_clipboard_text("https://example.invalid/b.zip") == 0


def test_watching_is_on_by_default_and_the_link_grabber_can_switch_it():
    """Owner decision 2026-11-05: on by default, like JDownloader, with a
    switch on the link grabber page."""
    from pathlib import Path
    from opengrab.settings import Settings
    assert Settings().clipboard_monitor_enabled is True
    static = Path(__file__).resolve().parents[2] / "src" / "opengrab" / "ui" / "static"
    assert 'id="lg-clipboard"' in (static / "index.html").read_text(encoding="utf-8")
    assert '"#lg-clipboard"' in (static / "app.js").read_text(encoding="utf-8")
    with running_server({"clipboard_monitor_enabled": True}) as srv:
        assert req(srv, "POST", "/api/settings", {"clipboard_monitor_enabled": False})[0] == 200
        assert req(srv, "GET", "/api/clipboard/status")[1]["status"] == "off"
        assert srv.capture_clipboard_text("https://example.invalid/c.zip") == 0
        req(srv, "POST", "/api/settings", {"clipboard_monitor_enabled": True})
        # the switch shows "on" at once, before the watcher's next look
        assert req(srv, "GET", "/api/clipboard/status")[1]["status"] == "watching"
        assert srv.capture_clipboard_text("https://example.invalid/c.zip") == 1


def test_ftp_links_are_picked_up_too():
    with running_server({"clipboard_monitor_enabled": True}) as srv:
        assert srv.capture_clipboard_text("ftp://ftp.example.invalid/pub/file.iso") == 1
