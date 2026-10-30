"""A server that answers HEAD with Content-Length: 0 must not produce a
"finished" download with no file (found while wiring W7)."""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from opengrab.engine.downloader import DownloadError, LinkDownload
from opengrab.models import Link
from opengrab.settings import Settings

BODY = os.urandom(50_000)


class _H(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")  # lies on HEAD
        self.end_headers()

    def do_GET(self):
        body = BODY if self.path == "/real.bin" else b""
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_zero_length_head_still_downloads_the_body(server, tmp_path):
    url = server + "/real.bin"
    path = LinkDownload(Link(url=url, filename="real.bin"), url, tmp_path, settings=Settings()).run()
    assert open(path, "rb").read() == BODY


def test_truly_empty_file_is_created(server, tmp_path):
    url = server + "/empty.bin"
    path = LinkDownload(Link(url=url, filename="empty.bin"), url, tmp_path, settings=Settings()).run()
    assert os.path.getsize(path) == 0


def test_finalize_without_a_part_file_is_an_error(tmp_path):
    dl = LinkDownload(Link(url="http://x.invalid/a", filename="a"), "http://x.invalid/a", tmp_path, settings=Settings())
    with pytest.raises(DownloadError, match="nothing was downloaded"):
        dl._finalize()
