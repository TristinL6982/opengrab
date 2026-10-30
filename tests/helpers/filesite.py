"""In-memory file server for engine tests: ranges, ETags, throttling, sidecars."""
from __future__ import annotations

import hashlib
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FileSite:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.etags: dict[str, str] = {}
        self.delay_per_64k = 0.0  # seconds slept per 64 KiB sent
        self.slow_below = 0       # bytes [0, slow_below) are served slower ...
        self.slow_delay = 0.0     # ... with this delay per 64 KiB
        self.requests: list[tuple[str, str, dict]] = []
        site = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def _serve(self, head: bool):
                path = self.path.split("?", 1)[0]
                site.requests.append((self.command, path, dict(self.headers)))
                data = site.files.get(path)
                if data is None:
                    body = b"not found"
                    self.send_response(404)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if not head:
                        self.wfile.write(body)
                    return
                start, end, code = 0, len(data) - 1, 200
                rng = self.headers.get("Range")
                if rng:
                    m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
                    if m:
                        start = int(m.group(1) or 0)
                        end = int(m.group(2)) if m.group(2) else len(data) - 1
                        end = min(end, len(data) - 1)
                        code = 206
                self.send_response(code)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("ETag", site.etags.get(path) or '"%s"' % hashlib.md5(data).hexdigest())
                self.send_header("Content-Length", str(end - start + 1))
                if code == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.end_headers()
                if head:
                    return
                pos = start
                try:
                    while pos <= end:
                        chunk = data[pos:min(end + 1, pos + 65536)]
                        self.wfile.write(chunk)
                        pos += len(chunk)
                        if site.slow_delay and pos <= site.slow_below:
                            time.sleep(site.slow_delay)
                        elif site.delay_per_64k:
                            time.sleep(site.delay_per_64k)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_GET(self):
                self._serve(False)

            def do_HEAD(self):
                self._serve(True)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def add(self, path: str, data: bytes) -> str:
        self.files[path] = data
        return self.base + path

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
