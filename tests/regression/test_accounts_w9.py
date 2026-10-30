"""W9: accounts in the engine - debrid, usage quotas, cookies, secret masking."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from opengrab import debrid
from tests.helpers.apiserver import req, running_server
from tests.helpers.filesite import FileSite

BLOB = os.urandom(150 * 1024)


def _wait(pred, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


@pytest.fixture
def site():
    s = FileSite()
    yield s
    s.close()


@pytest.fixture
def fake_realdebrid(site, monkeypatch):
    """A local Real-Debrid API that unrestricts any link to a FileSite file."""
    calls = []
    target = site.add("/unrestricted/movie.mkv", BLOB)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            form = self.rfile.read(n).decode()
            calls.append((self.headers.get("Authorization"), form))
            if self.headers.get("Authorization") != "Bearer RDTOKEN":
                body, code = json.dumps({"error": "bad_token"}).encode(), 401
            else:
                body, code = json.dumps({"download": target, "filename": "movie.mkv",
                                         "filesize": len(BLOB)}).encode(), 200
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(debrid.RealDebrid, "api", f"http://127.0.0.1:{httpd.server_address[1]}/rest/1.0")
    yield calls
    httpd.shutdown()
    httpd.server_close()


def test_hoster_link_resolves_through_realdebrid_and_counts_usage(fake_realdebrid):
    with running_server() as srv:
        st, acc = req(srv, "POST", "/api/accounts", body={
            "site": "real-debrid.com", "username": "me", "password": "", "premium": True,
            "api_token": "RDTOKEN", "usage": {"daily_mb": 1000}})
        assert st == 200 and acc["has_token"] is True and "api_token" not in json.dumps(acc)
        link = srv.manager.add_url("http://hoster.test/file/abc123")
        l = _wait(lambda: srv.manager.get(link.id).status.value == "finished" and srv.manager.get(link.id))
        assert open(l.path, "rb").read() == BLOB
        assert l.extra["service"] == "realdebrid" and l.extra["account"] == acc["id"]
        assert fake_realdebrid[0][0] == "Bearer RDTOKEN" and "hoster.test" in fake_realdebrid[0][1]
        usage = {u["id"]: u for u in req(srv, "GET", "/api/accounts/usage")[1]["accounts"]}
        assert usage[acc["id"]]["used_today"] >= len(BLOB) and usage[acc["id"]]["service"] == "realdebrid"


def test_exhausted_quota_skips_the_debrid_account(fake_realdebrid, site):
    site.add("/plain.bin", BLOB)
    with running_server() as srv:
        st, acc = req(srv, "POST", "/api/accounts", body={
            "site": "real-debrid.com", "username": "me", "api_token": "RDTOKEN",
            "usage": {"daily_mb": 0.05}})  # about 50 kB
        first = srv.manager.add_url("http://hoster.test/file/one")
        _wait(lambda: srv.manager.get(first.id).status.value == "finished")
        n_calls = len(fake_realdebrid)
        srv.manager.account_policy._debrid_miss.clear()
        second = srv.manager.add_url("http://hoster.test/file/two")
        _wait(lambda: srv.manager.get(second.id).status.value in ("error", "finished"))
        assert len(fake_realdebrid) == n_calls  # over quota: not asked again
        assert srv.manager.get(second.id).extra.get("service") is None


def test_bad_token_falls_back_to_the_normal_plugin(fake_realdebrid, site):
    url = site.add("/direct.zip", BLOB)
    with running_server() as srv:
        req(srv, "POST", "/api/accounts", body={"site": "real-debrid.com", "username": "me", "api_token": "WRONG"})
        link = srv.manager.add_url(url)
        l = _wait(lambda: srv.manager.get(link.id).status.value == "finished" and srv.manager.get(link.id))
        assert "service" not in l.extra  # the direct plugin never asks debrid


def test_account_cookies_are_sent_with_downloads(site):
    url = site.add("/members/file.bin", BLOB)
    with running_server() as srv:
        st, acc = req(srv, "POST", "/api/accounts", body={
            "site": "127.0.0.1", "username": "member", "cookie_header": "session=abc123; theme=dark"})
        assert st == 200 and acc["has_cookies"] is True
        link = srv.manager.add_url(url)
        _wait(lambda: srv.manager.get(link.id).status.value == "finished")
        cookies = [h.get("Cookie", "") for (_m, path, h) in site.requests if path == "/members/file.bin"]
        assert any("session=abc123" in c for c in cookies)


def test_secrets_are_masked_and_protected_on_disk():
    with running_server() as srv:
        st, acc = req(srv, "POST", "/api/accounts", body={
            "site": "alldebrid.com", "username": "u", "password": "pw-secret", "debrid": "alldebrid",
            "api_token": "TOK-SECRET", "cookies_txt": ".alldebrid.com\tTRUE\t/\tFALSE\t0\tsid\tCOOKIE-SECRET\n"})
        assert st == 200
        raw = json.dumps(req(srv, "GET", "/api/accounts")[1]) + json.dumps(acc)
        for secret in ("pw-secret", "TOK-SECRET", "COOKIE-SECRET"):
            assert secret not in raw
        on_disk = (Path(os.environ["OPENGRAB_DATA_DIR"]) / "accounts.json").read_text()
        for secret in ("pw-secret", "TOK-SECRET", "COOKIE-SECRET"):
            assert secret not in on_disk
        # reload from disk decrypts again
        srv.manager.accounts.load()
        assert srv.manager.accounts.accounts[0].data["api_token"] == "TOK-SECRET"
        assert req(srv, "POST", "/api/accounts", body={"site": "x", "username": "y", "debrid": "nope"})[0] == 400
        st, upd = req(srv, "POST", f"/api/accounts/{acc['id']}", body={"premium": True, "usage": {"max_parallel": 1}})
        assert st == 200 and upd["premium"] is True and upd["data"]["usage"]["max_parallel"] == 1


def test_plugins_get_premium_accounts_first():
    with running_server() as srv:
        req(srv, "POST", "/api/accounts", body={"site": "host.test", "username": "free"})
        req(srv, "POST", "/api/accounts", body={"site": "host.test", "username": "paid", "premium": True})
        ctx = srv.manager._ctx()
        assert ctx.account_for(url="https://dl.host.test/x").username == "paid"
