"""W2: challenge detection, captcha inbox, browser clearance and harvest page."""
import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from opengrab import browser_solver
from tests.helpers.apiserver import req, running_server

FILE = b"OpenGrab W2 payload " * 400
CF_PAGE = (b"<html><head><title>Just a moment...</title></head><body>"
           b"<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1'></script></body></html>")
RC_PAGE = (b"<html><body><form><div class=\"g-recaptcha\" data-sitekey=\"6LcW2testKEY_abc\"></div>"
           b"</form></body></html>")
BLOCK_PAGE = b"<html><body>Access denied<p>error code: 1020</p></body></html>"


class _Fixture(BaseHTTPRequestHandler):
    seen_headers: list = []

    def log_message(self, *a):
        pass

    def _cleared(self):
        return "cf_clearance=ok" in (self.headers.get("Cookie") or "")

    def version_string(self):
        return getattr(self, "_server_name", "fixture")

    def _send(self, code, body, ctype, extra=None):
        extra = dict(extra or {})
        self._server_name = extra.pop("Server", "fixture")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        type(self).seen_headers.append(dict(self.headers))
        if self.path.startswith("/cf/"):
            if not self._cleared():
                return self._send(503, CF_PAGE, "text/html; charset=UTF-8",
                                  {"Server": "cloudflare", "CF-RAY": "8a1b2c3d4e5f6a7b-FRA"})
            rng = self.headers.get("Range")
            if rng == "bytes=0-0":
                return self._send(206, FILE[:1], "application/zip",
                                  {"Content-Range": f"bytes 0-0/{len(FILE)}", "Accept-Ranges": "bytes"})
            return self._send(200, FILE, "application/zip", {"Accept-Ranges": "bytes"})
        if self.path.startswith("/rc/"):
            return self._send(403, RC_PAGE, "text/html", {})
        if self.path.startswith("/block/"):
            return self._send(403, BLOCK_PAGE, "text/html", {"Server": "cloudflare", "CF-RAY": "77-AMS"})
        return self._send(404, b"nope", "text/plain")


@pytest.fixture
def site():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Fixture)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    _Fixture.seen_headers = []
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _wait(pred, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


def _link(srv, lid):
    return srv.manager.get(lid)


def test_cloudflare_page_parks_shared_inbox_entry_then_browser_clears_it(site, monkeypatch):
    with running_server() as srv:
        a = srv.manager.add_url(site + "/cf/one.zip")
        b = srv.manager.add_url(site + "/cf/two.zip")
        # the status flips a moment before the inbox entry id is stored (raced on CI)
        parked = lambda lid: (l := _link(srv, lid)).status.value == "captcha" and l.extra.get("captcha_request_id") and l
        la = _wait(lambda: parked(a.id))
        lb = _wait(lambda: parked(b.id))
        assert la.captcha_kind == "cf_js" and la.extra["captcha_ray_id"] == "8a1b2c3d4e5f6a7b-FRA"
        rid = la.extra["captcha_request_id"]
        assert lb.extra["captcha_request_id"] == rid  # one entry per host and kind
        st, inbox = req(srv, "GET", "/api/captcha/inbox")
        assert st == 200 and len(inbox["pending"]) == 1
        entry = inbox["pending"][0]
        assert entry["kind"] == "cf_js" and entry["solve_in_browser"] and not entry["harvest"]
        assert {x["id"] for x in entry["links"]} == {a.id, b.id}
        notes = req(srv, "GET", "/api/notifications")[1]
        assert [n["title"] for n in notes] == ["Captcha waiting: 127.0.0.1"]

        driver = browser_solver.FakeDriver([[], [{"name": "cf_clearance", "value": "ok"}]], user_agent="EdgeUA/1")
        monkeypatch.setattr(browser_solver, "make_driver", lambda: driver)
        st, body = req(srv, "POST", f"/api/captcha/{rid}/browser", body={})
        assert st == 202, body
        _wait(lambda: _link(srv, a.id).status.value == "finished" and _link(srv, b.id).status.value == "finished")
        # the entry belongs to whichever link parked first
        assert driver.opened in ([site + "/cf/one.zip"], [site + "/cf/two.zip"]) and driver.closed
        for lid in (a.id, b.id):
            data = open(_link(srv, lid).path, "rb").read()
            assert hashlib.sha256(data).digest() == hashlib.sha256(FILE).digest()
        assert any("EdgeUA/1" in h.get("User-Agent", "") for h in _Fixture.seen_headers)
        st, inbox = req(srv, "GET", "/api/captcha/inbox")
        assert inbox["pending"] == [] and inbox["clearances"][0]["host"] == "127.0.0.1"
        assert any(n["title"] == "Challenge passed: 127.0.0.1" for n in req(srv, "GET", "/api/notifications")[1])
        assert req(srv, "DELETE", "/api/captcha/clearances/127.0.0.1")[0] == 200
        assert req(srv, "GET", "/api/captcha/inbox")[1]["clearances"] == []


def test_skip_errors_every_waiting_link(site):
    with running_server() as srv:
        a = srv.manager.add_url(site + "/cf/x.zip")
        la = _wait(lambda: _link(srv, a.id).status.value == "captcha" and _link(srv, a.id))
        rid = la.extra["captcha_request_id"]
        assert req(srv, "POST", f"/api/captcha/{rid}/skip", body={})[1] == {"ok": True, "links": 1}
        assert _link(srv, a.id).status.value == "error" and _link(srv, a.id).error == "captcha skipped"
        assert req(srv, "POST", f"/api/captcha/{rid}/skip", body={})[0] == 404


def test_widget_challenge_gets_a_key_protected_harvest_page(site):
    with running_server() as srv:
        a = srv.manager.add_url(site + "/rc/page")
        la = _wait(lambda: _link(srv, a.id).status.value == "captcha" and _link(srv, a.id))
        assert la.captcha_kind == "recaptcha"
        rid = la.extra["captcha_request_id"]
        st, link = req(srv, "POST", f"/api/captcha/{rid}/harvest-link", body={})
        assert st == 200 and "?k=" in link["url"]
        st, page = req(srv, "GET", link["url"], auth=False)
        assert st == 200 and 'data-sitekey="6LcW2testKEY_abc"' in page
        assert f"/api/captcha/harvest/{rid}/solve?k=" in page
        st, _ = req(srv, "GET", f"/api/captcha/harvest/{rid}?k=wrong", auth=False)
        assert st == 404
        key = link["url"].split("?k=")[1]
        st, err = req(srv, "POST", f"/api/captcha/harvest/{rid}/solve?k=bad", body={"answer": "tok"}, auth=False)
        assert st == 403
        st, ok = req(srv, "POST", f"/api/captcha/harvest/{rid}/solve?k={key}",
                     body={"id": rid, "answer": "TOKEN123"}, auth=False)
        assert st == 200 and ok["resumed"] == 1
        assert req(srv, "GET", "/api/captcha/inbox")[1]["pending"] == []
        # the key dies with the request
        assert req(srv, "GET", link["url"], auth=False)[0] == 404


def test_cloudflare_block_is_an_error_not_a_captcha(site):
    with running_server() as srv:
        a = srv.manager.add_url(site + "/block/f.zip")
        la = _wait(lambda: _link(srv, a.id).status.value in ("error", "captcha") and _link(srv, a.id))
        assert la.status.value == "error"
        assert "blocked by Cloudflare" in la.error and "77-AMS" in la.error


def test_device_cannot_skip_without_auth_and_routes_need_token(site):
    with running_server() as srv:
        assert req(srv, "GET", "/api/captcha/inbox", auth=False)[0] == 401
        assert req(srv, "POST", "/api/captcha/" + "0" * 32 + "/browser", body={})[0] == 404


def test_http_client_keeps_caller_headers(site):
    from opengrab.http_client import HttpClient
    from opengrab.settings import Settings
    c = HttpClient(Settings())
    c.get(site + "/nothing", headers={"X-OpenGrab-Test": "kept"}, detect_challenges=False)
    sent = {k.lower(): v for k, v in _Fixture.seen_headers[-1].items()}
    assert sent.get("x-opengrab-test") == "kept"
