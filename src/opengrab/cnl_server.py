"""Click'n'Load 2 local HTTP listener (the endpoint browser pages post to on 127.0.0.1:9666)."""
import http.server
import urllib.parse

from .cnl import CnlError, decode_addcrypted2, parse_links

JDCHECK_JS = "jdownloader=true; var version='17461';"


def _normalize(path: str) -> str:
    p = path.split("?", 1)[0]
    if p != "/" and p.endswith("/"):
        p = p[:-1]
    return p


def handle_cnl(method: str, path: str, form: dict, on_links) -> tuple[int, str]:
    """Route one Click'n'Load request; returns (status, body)."""
    p = _normalize(path)
    if method == "GET":
        if p == "/jdcheck.js":
            return (200, JDCHECK_JS)
        if p in ("/", "/flash"):
            return (200, "JDownloader")
        return (404, "")
    if method == "POST":
        if p == "/flash/add":
            links = parse_links(form.get("urls", ""))
            if not links:
                return (400, "failed\r\n")
            on_links({
                "links": links,
                "passwords": [],
                "source": form.get("source", ""),
                "package": form.get("package", ""),
            })
            return (200, "success\r\n")
        if p == "/flash/addcrypted2":
            try:
                result = decode_addcrypted2(form)
            except CnlError:
                return (400, "failed\r\n")
            on_links(result)
            return (200, "success\r\n")
    return (404, "")


def make_server(on_links, host: str = "127.0.0.1", port: int = 9666) -> http.server.ThreadingHTTPServer:
    """Create (but do not start) a ThreadingHTTPServer serving the CNL endpoint."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self, status: int, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._reply(*handle_cnl("GET", self.path, {}, on_links))

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                parsed = urllib.parse.parse_qs(raw.decode("utf-8"), keep_blank_values=True)
            except UnicodeDecodeError:
                self._reply(400, "failed\r\n")
                return
            form = {k: v[0] for k, v in parsed.items()}
            self._reply(*handle_cnl("POST", self.path, form, on_links))

        def log_message(self, *args):
            pass

    return http.server.ThreadingHTTPServer((host, port), _Handler)
