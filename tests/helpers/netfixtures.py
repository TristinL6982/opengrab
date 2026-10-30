"""Test fixtures for W7: a forward proxy (optionally with a login) and a file
server that requires Basic or Digest authentication."""
from __future__ import annotations

import base64
import hashlib
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _serve(handler_cls):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class ForwardProxy:
    """HTTP forward proxy. ``routes`` maps a made-up host (e.g. files.test) to
    a real local base URL; every proxied request is logged."""

    def __init__(self, routes: dict, login: tuple | None = None):
        self.routes = dict(routes)
        self.login = login
        self.log: list[str] = []
        px = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                pass

            def _deny(self):
                self.send_response(407)
                self.send_header("Proxy-Authenticate", 'Basic realm="proxy"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _forward(self, method):
                if px.login:
                    want = "Basic " + base64.b64encode(f"{px.login[0]}:{px.login[1]}".encode()).decode()
                    if self.headers.get("Proxy-Authorization") != want:
                        return self._deny()
                px.log.append(self.path)
                m = re.match(r"https?://([^/:]+)(?::\d+)?(/.*)?$", self.path)
                base = px.routes.get(m.group(1)) if m else None
                if base is None:
                    self.send_response(502)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                req = urllib.request.Request(base + (m.group(2) or "/"), method=method)
                for k in ("Range", "Authorization", "Cookie"):
                    if self.headers.get(k):
                        req.add_header(k, self.headers[k])
                try:
                    resp = urllib.request.urlopen(req, timeout=10)
                    status, headers, body = resp.status, resp.headers, resp.read()
                except urllib.error.HTTPError as e:
                    status, headers, body = e.code, e.headers, e.read()
                self.send_response(status)
                for k in ("Content-Type", "Content-Range", "Accept-Ranges", "ETag", "WWW-Authenticate"):
                    if headers.get(k):
                        self.send_header(k, headers[k])
                length = headers.get("Content-Length") if method == "HEAD" else None
                self.send_header("Content-Length", str(length or len(body)))
                self.end_headers()
                if method != "HEAD":
                    self.wfile.write(body)

            def do_GET(self):
                self._forward("GET")

            def do_HEAD(self):
                self._forward("HEAD")

        self.httpd = _serve(H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class AuthSite:
    """Serves one file behind Basic or Digest authentication."""

    REALM = "opengrab-test"
    NONCE = "dcd98b7102dd2f0e8b11d0f600bfb0c093"

    def __init__(self, scheme: str, user: str, password: str, data: bytes):
        self.scheme, self.user, self.password, self.data = scheme, user, password, data
        self.attempts = 0
        site = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                pass

            def _challenge(self):
                self.send_response(401)
                if site.scheme == "basic":
                    self.send_header("WWW-Authenticate", f'Basic realm="{site.REALM}"')
                else:
                    self.send_header("WWW-Authenticate",
                                     f'Digest realm="{site.REALM}", qop="auth", nonce="{site.NONCE}", algorithm=MD5')
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _ok(self) -> bool:
                h = self.headers.get("Authorization") or ""
                if site.scheme == "basic":
                    return h == "Basic " + base64.b64encode(f"{site.user}:{site.password}".encode()).decode()
                if not h.startswith("Digest "):
                    return False
                f = dict(re.findall(r'(\w+)="?([^",]+)"?', h[7:]))
                md5 = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
                ha1 = md5(f"{site.user}:{site.REALM}:{site.password}")
                ha2 = md5(f"{self.command}:{f.get('uri', '')}")
                want = md5(f"{ha1}:{f.get('nonce')}:{f.get('nc')}:{f.get('cnonce')}:{f.get('qop')}:{ha2}")
                return f.get("username") == site.user and f.get("response") == want

            def _serve(self, head):
                site.attempts += 1
                if not self._ok():
                    return self._challenge()
                body = site.data
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head:
                    self.wfile.write(body)

            def do_GET(self):
                self._serve(False)

            def do_HEAD(self):
                self._serve(True)

        self.httpd = _serve(H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
