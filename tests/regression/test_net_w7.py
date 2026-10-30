"""W7: proxy list with rules and failover, proxy logins, server Basic/Digest logins."""
import hashlib
import os
import time

import pytest

from tests.helpers.apiserver import req, running_server
from tests.helpers.filesite import FileSite
from tests.helpers.netfixtures import AuthSite, ForwardProxy

BLOB = os.urandom(200 * 1024)


def _wait(pred, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


def _finished(srv, lid):
    l = srv.manager.get(lid)
    return l.status.value == "finished" and l


@pytest.fixture
def site():
    s = FileSite()
    yield s
    s.close()


def test_failover_skips_a_dead_proxy_and_rules_send_hosts_direct(site):
    site.add("/f.bin", BLOB)
    good = ForwardProxy({"files.test": site.base})
    try:
        settings = {"proxies": ["http://127.0.0.1:9", good.url], "proxy_rules": {"127.0.0.1": "direct"}}
        with running_server(settings) as srv:
            link = srv.manager.add_url("http://files.test/f.bin")
            l = _wait(lambda: _finished(srv, link.id))
            assert hashlib.sha256(open(l.path, "rb").read()).digest() == hashlib.sha256(BLOB).digest()
            assert "http://files.test/f.bin" in good.log
            st, info = req(srv, "GET", "/api/proxies")
            assert "127.0.0.1:9" in info["failed"]
            # a host with a "direct" rule never touches the proxy
            before = len(good.log)
            direct = srv.manager.add_url(site.base + "/f.bin?direct")
            _wait(lambda: _finished(srv, direct.id))
            assert len(good.log) == before
    finally:
        good.close()


def test_proxy_login_from_the_store(site):
    site.add("/p.bin", BLOB)
    px = ForwardProxy({"files.test": site.base}, login=("puser", "p@ss word"))
    try:
        with running_server({"proxies": [px.url]}) as srv:
            st, _ = req(srv, "POST", "/api/site-logins", body={
                "pattern": "127.0.0.1", "username": "puser", "password": "p@ss word", "for_proxy": True})
            assert st == 201
            link = srv.manager.add_url("http://files.test/p.bin")
            _wait(lambda: _finished(srv, link.id))
            assert px.log
    finally:
        px.close()


@pytest.mark.parametrize("scheme", ["basic", "digest"])
def test_server_login(scheme):
    data = os.urandom(64 * 1024)
    site = AuthSite(scheme, "alice", "s3cret!", data)
    try:
        with running_server() as srv:
            # without a login the server refuses
            bad = srv.manager.add_url(site.base + "/locked.bin")
            l = _wait(lambda: srv.manager.get(bad.id).status.value in ("error", "finished") and srv.manager.get(bad.id))
            assert l.status.value == "error"
            st, out = req(srv, "POST", "/api/site-logins", body={
                "pattern": "127.0.0.1", "username": "alice", "password": "s3cret!", "scheme": scheme})
            assert st == 201
            good = srv.manager.add_url(site.base + "/locked.bin?again")
            l = _wait(lambda: _finished(srv, good.id))
            assert open(l.path, "rb").read() == data
    finally:
        site.close()


def test_logins_never_leave_the_server_in_clear(tmp_path):
    with running_server() as srv:
        st, _ = req(srv, "POST", "/api/site-logins", body={"pattern": "*.corp.test", "username": "bob",
                                                            "password": "hunter2", "scheme": "ntlm"})
        assert st == 201
        st, listing = req(srv, "GET", "/api/site-logins")
        assert listing["logins"] == [{"pattern": "*.corp.test", "username": "bob", "scheme": "ntlm", "for_proxy": False}]
        assert "hunter2" not in str(listing)
        st, settings = req(srv, "GET", "/api/settings")
        assert "site_credentials" not in settings
        stored = srv.manager.settings.site_credentials[0]["secret"]
        assert stored != "hunter2" and stored.startswith(("dp:", "ob:"))
        # the settings endpoint cannot overwrite logins
        st, res = req(srv, "POST", "/api/settings", body={"site_credentials": []})
        assert "site_credentials" in res["ignored"] and srv.manager.settings.site_credentials
        assert req(srv, "DELETE", "/api/site-logins?pattern=*.corp.test")[0] == 200
        assert req(srv, "DELETE", "/api/site-logins?pattern=*.corp.test")[0] == 404
        assert req(srv, "POST", "/api/site-logins", body={"pattern": "x.test", "username": ""})[0] == 400


def test_bad_proxy_entries_are_reported():
    with running_server() as srv:
        st, res = req(srv, "POST", "/api/settings", body={"proxies": ["ftp://nope:21", "socks5h://127.0.0.1:1080"]})
        assert st == 200 and any("ftp://nope:21" in w for w in res["warnings"])
        st, info = req(srv, "GET", "/api/proxies")
        assert info["proxies"] == ["socks5h://127.0.0.1:1080"]


def test_proxy_test_route(site):
    site.add("/ping", b"pong")
    px = ForwardProxy({"files.test": site.base})
    try:
        with running_server() as srv:
            st, ok = req(srv, "POST", "/api/proxies/test", body={"proxy": px.url, "url": "http://files.test/ping"})
            assert st == 200 and ok["ok"] is True and ok["status"] == 200
            st, dead = req(srv, "POST", "/api/proxies/test", body={"proxy": "http://127.0.0.1:9", "url": "http://files.test/ping"})
            assert dead["ok"] is False and dead["error"]
    finally:
        px.close()
