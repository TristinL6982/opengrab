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


def test_ftp_links_are_picked_up_too():
    with running_server({"clipboard_monitor_enabled": True}) as srv:
        assert srv.capture_clipboard_text("ftp://ftp.example.invalid/pub/file.iso") == 1
