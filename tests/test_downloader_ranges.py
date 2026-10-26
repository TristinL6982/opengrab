"""Local HTTP coverage for chunk range requests and safe fallback."""
from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile

from opengrab.engine.downloader import LinkDownload
from opengrab.models import Link
from opengrab.settings import Settings


DATA = bytes(range(251)) * 24_000  # > CHUNK_MIN, exercises parallel path


class Handler(BaseHTTPRequestHandler):
    ignore_ranges = False
    ranges_seen = 0

    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(DATA)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        value = self.headers.get("Range")
        if value:
            type(self).ranges_seen += 1
        if value and not type(self).ignore_ranges:
            start, end = map(int, value.removeprefix("bytes=").split("-"))
            end = min(end, len(DATA) - 1)
            body = DATA[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(DATA)))
        self.end_headers()
        self.wfile.write(DATA)


def download(ignore_ranges: bool, dest: Path) -> tuple[Path, int]:
    Handler.ignore_ranges = ignore_ranges
    Handler.ranges_seen = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        link = Link(url=f"http://127.0.0.1:{server.server_port}/file.bin",
                    filename="file.bin")
        result = LinkDownload(link, link.url, dest,
                              settings=Settings(per_link_chunks=3)).run()
        return result, Handler.ranges_seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="opengrab-ranges-") as tmp:
        root = Path(tmp)
        expected = hashlib.sha256(DATA).hexdigest()
        good, ranges = download(False, root / "good")
        assert good.read_bytes() == DATA
        assert hashlib.sha256(good.read_bytes()).hexdigest() == expected
        assert ranges == 3, f"expected 3 Range requests including offset zero; got {ranges}"
        print("[PASS] parallel download sends and validates every chunk range")

        fallback, ranges = download(True, root / "fallback")
        assert fallback.read_bytes() == DATA
        assert hashlib.sha256(fallback.read_bytes()).hexdigest() == expected
        assert ranges >= 1, "parallel probe did not exercise ignored Range behavior"
        print("[PASS] ignored ranges safely restart as a single stream")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
