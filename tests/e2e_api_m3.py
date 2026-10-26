"""Local API tests for the M3 health, notification, file, and media routes."""
from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
import time

temp = Path(tempfile.mkdtemp(prefix="opengrab-m3-"))
os.environ["OPENGRAB_DATA_DIR"] = str(temp / "data")

from opengrab.engine.manager import DownloadManager  # noqa: E402
from opengrab import server as server_module  # noqa: E402
from opengrab.server import ApiServer  # noqa: E402
from opengrab.settings import Settings  # noqa: E402
from opengrab.models import Link  # noqa: E402


def main() -> int:
    downloads = temp / "downloads"
    downloads.mkdir()
    settings = Settings(download_dir=str(downloads), max_parallel=1,
                        base_path="/open")
    manager = DownloadManager(settings)
    manager.start()

    # Exercise the clipboard HTTP contract without reading or changing the
    # user's actual Windows clipboard during automated verification.
    real_make_handler = server_module._make_handler
    clipboard_value = ""

    def make_test_handler(api_server):
        handler = real_make_handler(api_server)

        def fake_clipboard(self, value=None):
            nonlocal clipboard_value
            if value is not None:
                clipboard_value = value
                return None
            return clipboard_value

        handler._clipboard = fake_clipboard
        return handler

    server_module._make_handler = make_test_handler
    server = ApiServer(settings, manager, port=0)
    port = server.start()
    base = f"http://127.0.0.1:{port}"
    passes = 0
    original_add_url = manager.add_url

    def request(path: str, method: str = "GET", body=None,
                data: bytes | None = None, headers=None):
        request_headers = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + server.base_path + path,
                                     data=data, method=method,
                                     headers=request_headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.headers, response.read()

    def check(name: str, condition: bool):
        nonlocal passes
        if not condition:
            raise AssertionError(name)
        passes += 1
        print(f"[PASS] {name}")

    try:
        _, _, page = request("/")
        check("UI base-path bootstrap", b'OPENGRAB_BASE_PATH="/open"' in page)
        _, _, manifest = request("/manifest.webmanifest")
        check("PWA start URL honors base path",
              json.loads(manifest)["start_url"] == "/open/")

        for path in ("/api/ping", "/api/health"):
            status, _, raw = request(path)
            check(path, status == 200 and json.loads(raw)["ok"] is True)

        _, _, providers = request("/api/captcha-test")
        check("captcha provider diagnostics", bool(json.loads(providers)["providers"]))
        settings.proxy = "http://alice:secret@example.invalid:8080"
        _, _, diagnostics = request("/api/diagnostics")
        diagnostic_data = json.loads(diagnostics)
        check("diagnostics omit tokens, proxy credentials, and download URLs",
              server.token not in diagnostics.decode()
              and "secret" not in diagnostics.decode()
              and "example.invalid" not in diagnostics.decode()
              and "plugin_ids" in diagnostic_data)
        settings.proxy = ""
        request("/api/clipboard", "POST", {"text": "fixture clipboard"})
        _, _, clipboard = request("/api/clipboard")
        check("clipboard API contract (mock backend)",
              json.loads(clipboard)["text"] == "fixture clipboard")
        added_urls = []

        def fake_add_url(url, **kwargs):
            added_urls.append(url)
            return Link(url=url)

        manager.add_url = fake_add_url
        captured = "https://example.com/a https://example.com/b."
        check("clipboard capture disabled by default",
              server.capture_clipboard_text(captured) == 0)
        settings.clipboard_monitor_enabled = True
        check("clipboard capture queues candidates for review",
              server.capture_clipboard_text(captured) == 2 and not added_urls)
        check("clipboard capture deduplicates repeated text",
              server.capture_clipboard_text(captured) == 0)
        _, _, candidates_raw = request("/api/linkgrabber")
        candidates = json.loads(candidates_raw)
        request(f"/api/linkgrabber/{candidates[0]['id']}/dismiss", "POST", {})
        _, _, added = request(f"/api/linkgrabber/{candidates[1]['id']}/add", "POST", {})
        check("candidate is added only after explicit approval",
              json.loads(added)["ok"] and len(added_urls) == 1)
        request("/api/linkgrabber/clear", "POST", {})

        manager.on_change("finished", {"id": "test-link", "status": "finished",
                                        "title": "fixture.zip"})
        _, _, notices = request("/api/notifications")
        check("notification feed", json.loads(notices)[0]["message"] == "fixture.zip: finished")
        request("/api/notifications/clear", "POST", {})
        _, _, notices = request("/api/notifications")
        check("notification clear", json.loads(notices) == [])

        _, _, listing = request("/api/files")
        check("file manager root listing", json.loads(listing)["items"] == [])
        request("/api/files", "POST", {"action": "mkdir", "path": "sub"})
        payload = b"OpenGrab file API test\n"
        request("/api/files/upload?path=sub%2Fsample.txt", "PUT", data=payload)
        _, _, listing = request("/api/files?path=sub")
        check("mkdir and upload", json.loads(listing)["items"][0]["name"] == "sample.txt")

        _, headers, body = request("/api/files/download?path=sub%2Fsample.txt")
        check("file download", body == payload and "attachment" in headers["Content-Disposition"])
        status, headers, body = request("/api/audio?path=sub%2Fsample.txt",
                                        headers={"Range": "bytes=8-11"})
        check("audio byte range", status == 206 and body == payload[8:12]
              and headers["Content-Range"] == f"bytes 8-11/{len(payload)}")
        _, _, ticket_raw = request("/api/audio-ticket", "POST",
                                   {"path": "sub/sample.txt"})
        ticket = json.loads(ticket_raw)["url"].split("?", 1)[1]
        proxy_headers = {"X-Forwarded-For": "203.0.113.8",
                         "X-Forwarded-Proto": "https",
                         "Range": "bytes=0-3"}
        status, _, body = request("/api/audio?" + ticket,
                                  headers=proxy_headers)
        check("scoped audio ticket streams through HTTPS proxy",
              status == 206 and body == payload[:4])

        request("/api/files", "POST", {"action": "rename", "path": "sub/sample.txt",
                                        "to": "sub/renamed.txt"})
        request("/api/files", "POST", {"action": "delete", "path": "sub/renamed.txt"})
        request("/api/files", "POST", {"action": "delete", "path": "sub"})
        check("rename and delete", not (downloads / "sub").exists())

        try:
            request("/api/files?path=..")
            escaped = False
        except urllib.error.HTTPError as exc:
            escaped = exc.code == 400
        check("file path traversal blocked", escaped)

        forwarded = {"X-Forwarded-For": "203.0.113.9"}
        check("forwarded loopback requires token",
              not server.authorize("127.0.0.1", forwarded))
        check("remote client requires token",
              not server.authorize("203.0.113.9", {}))
        check("remote plain HTTP is blocked",
              not server.remote_transport_allowed("203.0.113.9", {}))
        check("proxy must report HTTPS",
              not server.remote_transport_allowed("127.0.0.1", forwarded))
        proxy_https = {**forwarded, "X-Forwarded-Proto": "https"}
        check("HTTPS proxy transport accepted",
              server.remote_transport_allowed("127.0.0.1", proxy_https))
        check("HTTPS proxy still requires bearer token",
              not server.authorize("127.0.0.1", proxy_https))

        try:
            request("/api/terminal")
            disabled = False
        except urllib.error.HTTPError as exc:
            disabled = exc.code == 403
        check("terminal disabled by default", disabled)

        settings.terminal_enabled = True
        try:
            request("/api/terminal", "POST", {"command": "echo terminal-check"})
            unauthenticated = False
        except urllib.error.HTTPError as exc:
            unauthenticated = exc.code == 401
        check("terminal requires bearer token on localhost", unauthenticated)

        auth = {"Authorization": f"Bearer {server.token}"}
        status, _, started = request("/api/terminal", "POST",
                                     {"command": "echo terminal-check"},
                                     headers=auth)
        job_id = json.loads(started)["id"]
        result = None
        for _ in range(40):
            _, _, raw = request(f"/api/terminal/{job_id}", headers=auth)
            result = json.loads(raw)
            if not result["running"]:
                break
            time.sleep(0.1)
        check("opt-in terminal job completes", status == 202 and result is not None
              and not result["running"] and "terminal-check" in result["output"])
        print(f"{passes} checks passed")
        return 0
    finally:
        manager.add_url = original_add_url
        server.stop()
        manager.shutdown()
        server_module._make_handler = real_make_handler


if __name__ == "__main__":
    raise SystemExit(main())
