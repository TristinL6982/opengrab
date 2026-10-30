"""W8: LinkGrabber v2 - online check, spider, Click'n'Load."""
import base64
import http.client
import time
import urllib.parse

import pytest
from Cryptodome.Cipher import AES

from tests.helpers.apiserver import req, running_server
from tests.helpers.filesite import FileSite


def _wait(pred, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


def _candidates(srv):
    return req(srv, "GET", "/api/linkgrabber")[1]


@pytest.fixture
def site():
    s = FileSite()
    yield s
    s.close()


def test_pasted_links_are_checked_online_and_offline(site):
    good = site.add("/files/movie.mkv", b"x" * 12345)
    with running_server() as srv:
        st, res = req(srv, "POST", "/api/linkgrabber", body={"urls": [good, site.base + "/files/gone.zip"]})
        assert st == 201 and res["added"] == 2
        items = _wait(lambda: (lambda c: c if all(i["state"] != "unchecked" for i in c) else None)(_candidates(srv)))
        by = {i["url"]: i for i in items}
        assert by[good]["state"] == "online" and by[good]["size"] == 12345 and by[good]["source"] == "paste"
        assert by[site.base + "/files/gone.zip"]["state"] == "offline"
        # duplicates are not queued twice; only online ones are added with add-all
        assert req(srv, "POST", "/api/linkgrabber", body={"urls": [good]})[1]["added"] == 0
        st, res = req(srv, "POST", "/api/linkgrabber/add-all", body={"only_online": True})
        assert res["added"] == 1 and [l.url for l in srv.manager.all_links()] == [good]
        assert [i["url"] for i in _candidates(srv)] == [site.base + "/files/gone.zip"]


def test_spider_crawls_two_levels_and_respects_robots(site):
    site.add("/", b'<a href="/a/">A</a> <a href="/private/">P</a> <a href="/top.zip">top</a>')
    site.add("/a/", b'<a href="/a/deep.iso">deep</a> <a href="https://elsewhere.test/x.zip">ext</a>')
    site.add("/private/", b'<a href="/private/secret.zip">s</a>')
    site.add("/robots.txt", b"User-agent: *\nDisallow: /private/\n")
    for p in ("/top.zip", "/a/deep.iso", "/private/secret.zip"):
        site.add(p, b"file")
    # FileSite serves everything as octet-stream; the spider needs text/html pages
    with running_server({"spider_delay_s": 0}) as srv:
        orig = srv.manager.client.get

        def get(url, **kw):
            r = orig(url, **kw)
            if url.endswith("/") and r.status_code == 200:
                r.headers["content-type"] = "text/html"
            return r

        srv.manager.client.get = get
        st, job = req(srv, "POST", "/api/linkgrab/spider", body={"url": site.base + "/", "depth": 2})
        assert st == 202
        done = _wait(lambda: (lambda j: j if j["status"] != "running" else None)(
            req(srv, "GET", f"/api/linkgrab/spider/{job['id']}")[1]))
        assert done["status"] == "done", done
        urls = {i["url"] for i in _candidates(srv)}
        assert {site.base + "/top.zip", site.base + "/a/deep.iso"} <= urls
        assert site.base + "/private/secret.zip" not in urls  # robots.txt
        # same-domain limits which pages are crawled, not where files may live
        assert "https://elsewhere.test/x.zip" in urls
        assert all(i["source"] == "spider" for i in _candidates(srv))
        assert done["blocked"] >= 1
        assert req(srv, "POST", "/api/linkgrab/spider", body={"url": "ftp://x"})[0] == 400


def _cnl_post(port, path, form):
    body = urllib.parse.urlencode(form)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", path, body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = conn.getresponse()
    out = (r.status, r.read().decode(), r.getheader("Access-Control-Allow-Origin"))
    conn.close()
    return out


def test_clicknload_adds_links_for_review_with_package_and_passwords():
    with running_server() as srv:
        lg = srv.features["linkgrabber"]
        status = lg.start_cnl(port=0)
        try:
            port = status["port"]
            assert status["listening"] and req(srv, "GET", "/api/cnl/status")[1]["port"] == port
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/jdcheck.js")
            assert b"jdownloader=true" in conn.getresponse().read()
            conn.close()
            # encrypted form (Click'n'Load 2): key = hex in the jk function
            key = bytes.fromhex("31323334353637383930313233343536")
            plain = "https://host.test/a.rar\r\nhttps://host.test/b.rar".encode()
            plain += b"\0" * (16 - len(plain) % 16)
            crypted = base64.b64encode(AES.new(key, AES.MODE_CBC, iv=key).encrypt(plain)).decode()
            st, body, cors = _cnl_post(port, "/flash/addcrypted2", {
                "jk": "function f(){ return '31323334353637383930313233343536';}",
                "crypted": crypted, "package": "Holiday photos", "passwords": "pw1\npw2"})
            assert st == 200 and body.startswith("success") and cors == "*"
            items = _wait(lambda: (lambda c: c if len(c) == 2 else None)(_candidates(srv)))
            assert {i["package"] for i in items} == {"Holiday photos"}
            assert items[0]["passwords"] == ["pw1", "pw2"] and items[0]["source"] == "clicknload"
            assert srv.manager.all_links() == []  # nothing downloads on its own
            st, res = req(srv, "POST", f"/api/linkgrabber/{items[0]['id']}/add", body={})
            link = srv.manager.get(res["link"]["id"])
            assert link.extra["package"] == "Holiday photos" and link.extra["archive_passwords"] == ["pw1", "pw2"]
            assert any(n["title"] == "Links from Click'n'Load" for n in req(srv, "GET", "/api/notifications")[1])
        finally:
            lg.stop_cnl()


def test_busy_clicknload_port_is_a_warning_not_a_failure():
    import socket
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    try:
        with running_server() as srv:
            status = srv.features["linkgrabber"].start_cnl(port=blocker.getsockname()[1])
            assert status["listening"] is False and "in use" in status["error"]
    finally:
        blocker.close()
