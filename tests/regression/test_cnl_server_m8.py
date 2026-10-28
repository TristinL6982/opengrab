import base64
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest
from Cryptodome.Cipher import AES

from opengrab.cnl_server import JDCHECK_JS, handle_cnl, make_server

JK = "function f(){ return '31323334353637383930313233343536';}"


def _crypted(plain: bytes) -> str:
    key = bytes.fromhex("31323334353637383930313233343536")
    padded = plain + b"\x00" * (-len(plain) % 16)
    return base64.b64encode(AES.new(key, AES.MODE_CBC, iv=key).encrypt(padded)).decode("ascii")


def test_get_routes():
    got = []
    assert handle_cnl("GET", "/jdcheck.js", {}, got.append) == (200, JDCHECK_JS)
    assert handle_cnl("GET", "/jdcheck.js?x=1", {}, got.append) == (200, JDCHECK_JS)
    assert handle_cnl("GET", "/", {}, got.append) == (200, "JDownloader")
    assert handle_cnl("GET", "/flash/", {}, got.append) == (200, "JDownloader")
    assert handle_cnl("GET", "/flash", {}, got.append) == (200, "JDownloader")
    assert got == []


def test_post_flash_add():
    got = []
    form = {"urls": "https://a.com/1\r\nhttps://a.com/2", "package": "P"}
    assert handle_cnl("POST", "/flash/add", form, got.append) == (200, "success\r\n")
    assert got == [{"links": ["https://a.com/1", "https://a.com/2"], "passwords": [], "source": "", "package": "P"}]


def test_post_flash_add_empty():
    got = []
    assert handle_cnl("POST", "/flash/add", {"urls": ""}, got.append) == (400, "failed\r\n")
    assert handle_cnl("POST", "/flash/add", {}, got.append) == (400, "failed\r\n")
    assert got == []


def test_post_addcrypted2_valid():
    got = []
    form = {"jk": JK, "crypted": _crypted(b"https://x.com/f1")}
    assert handle_cnl("POST", "/flash/addcrypted2", form, got.append) == (200, "success\r\n")
    assert len(got) == 1
    assert got[0]["links"] == ["https://x.com/f1"]


def test_post_addcrypted2_bad_and_not_found():
    got = []
    form = {"jk": "function f(){ return 'zz';}", "crypted": "AAAA"}
    assert handle_cnl("POST", "/flash/addcrypted2", form, got.append) == (400, "failed\r\n")
    assert handle_cnl("GET", "/nope", {}, got.append) == (404, "")
    assert handle_cnl("POST", "/nope", {}, got.append) == (404, "")
    assert handle_cnl("PUT", "/flash/add", {"urls": "https://a.com/1"}, got.append) == (404, "")
    assert handle_cnl("GET", "/flash/add", {}, got.append) == (404, "")
    assert got == []


@pytest.fixture
def server():
    got = []
    srv = make_server(got.append, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv, got
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _url(srv, path):
    return "http://127.0.0.1:%d%s" % (srv.server_address[1], path)


def test_make_server_defaults_to_loopback():
    srv = make_server(lambda r: None, port=0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


def test_real_server_flash_add(server):
    srv, got = server
    data = urllib.parse.urlencode({"urls": "https://c.com/z"}).encode("utf-8")
    with urllib.request.urlopen(_url(srv, "/flash/add"), data=data, timeout=5) as resp:
        assert resp.status == 200
        assert resp.read() == b"success\r\n"
        assert resp.headers["Access-Control-Allow-Origin"] == "*"
        assert resp.headers["Content-Type"] == "text/html; charset=utf-8"
    assert got[0]["links"] == ["https://c.com/z"]


def test_real_server_jdcheck_and_404(server):
    srv, got = server
    with urllib.request.urlopen(_url(srv, "/jdcheck.js"), timeout=5) as resp:
        assert resp.status == 200
        assert resp.read().decode("utf-8") == JDCHECK_JS
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(_url(srv, "/nope"), timeout=5)
    assert exc.value.code == 404
    exc.value.close()
    assert got == []


def test_real_server_addcrypted2_bad_is_400(server):
    srv, got = server
    data = urllib.parse.urlencode({"jk": "function f(){ return 'zz';}", "crypted": "AAAA"}).encode("utf-8")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(_url(srv, "/flash/addcrypted2"), data=data, timeout=5)
    assert exc.value.code == 400
    assert exc.value.read() == b"failed\r\n"
    exc.value.close()
    assert got == []
