"""Local API tests for the M3 health, notification, file, and media routes."""
from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

temp = Path(tempfile.mkdtemp(prefix="opengrab-m3-"))
os.environ["OPENGRAB_DATA_DIR"] = str(temp / "data")

from opengrab.engine.manager import DownloadManager  # noqa: E402
from opengrab.server import ApiServer  # noqa: E402
from opengrab.settings import Settings  # noqa: E402


def main() -> int:
    downloads = temp / "downloads"
    downloads.mkdir()
    settings = Settings(download_dir=str(downloads), max_parallel=1,
                        base_path="/open")
    manager = DownloadManager(settings)
    manager.start()
    server = ApiServer(settings, manager, port=0)
    port = server.start()
    base = f"http://127.0.0.1:{port}"
    passes = 0

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
        print(f"{passes} checks passed")
        return 0
    finally:
        server.stop()
        manager.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
