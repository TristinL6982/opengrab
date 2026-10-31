"""Regression tests for the review of the new site plugins and engine code:
secrets, local-network redirects, waits the engine can read, unbounded loops
and crashes on unexpected server answers."""
import base64
import json
import logging
import os
import re
import zlib

import pytest
from Cryptodome.Cipher import AES
from Cryptodome.Hash import SHA256
from Cryptodome.Protocol.KDF import PBKDF2

from opengrab import browserpage
from opengrab.engine import ftp as ftp_engine
from opengrab.logs import RedactingFormatter, redact
from opengrab.plugins.base import PluginError
from opengrab.plugins.builtin import (_hostflow, _megacore, datanodes, dlproxy, fileditch,
                                      filekeeper, fuckingfast, mediafire, onefichier, pastes,
                                      privatebin, rootz, trackers, turbobit, vikingfile)
from tests.regression.test_sites_hosts_a import (DN_CODE, DN_PAGE, DN_URL, FF_ID, FF_PAGE,
                                                 RZ_OK, Resp, _rz_routes, ctx_for)

# the engine's own reading of a wait (DownloadManager._maybe_reconnect)
ENGINE_WAIT = re.compile(r"(\d+)\s*(second|sec|minute|min|hour)", re.I)


def engine_wait_seconds(msg: str) -> int:
    m = ENGINE_WAIT.search(msg)
    assert m, msg
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)[0].lower()]


# ---------------------------------------------------------------------------
# waits the engine can read
# ---------------------------------------------------------------------------
def test_mega_quota_wait_is_read_as_the_full_wait():
    # 2 h 5 min used to read as "5 min" (the first number + unit the engine finds)
    msg = str(_megacore.MegaApiError(_megacore.EOVERQUOTA, 7500))
    assert engine_wait_seconds(msg) == 125 * 60
    assert engine_wait_seconds(_megacore.quota_message(10800)) == 180 * 60
    assert engine_wait_seconds(_megacore.quota_message(30)) == 60


def test_rootz_repeated_rate_limit_reports_minutes(monkeypatch):
    slept = []
    monkeypatch.setattr(rootz, "sleep", slept.append)
    again = Resp(302, "", headers={"Location": "/d/BHXge?error=rate_limited&retry_after=80"})
    with pytest.raises(PluginError) as e:
        rootz.RootzPlugin().resolve("https://www.rootz.so/d/BHXge", ctx_for(_rz_routes(RZ_OK, head=again)))
    assert engine_wait_seconds(str(e.value)) == 120     # 80 s rounds up, not down
    assert slept == [80, 80]                            # no sleep without a re-check


def test_rootz_non_object_api_answer_is_a_plain_error():
    with pytest.raises(PluginError):
        rootz.RootzPlugin().resolve("https://rootz.so/d/BHXge", ctx_for(_rz_routes([1, 2])))


def test_fuckingfast_rate_limit_says_how_long():
    with pytest.raises(PluginError) as e:
        fuckingfast.FuckingFastPlugin().resolve(
            f"https://fuckingfast.co/{FF_ID}", ctx_for([("GET", f"/{FF_ID}", Resp(429, "slow down"))]))
    assert engine_wait_seconds(str(e.value)) == 300


def test_fuckingfast_relative_redirect_is_made_absolute():
    ctx = ctx_for([("GET", f"/{FF_ID}", Resp(text=FF_PAGE)),
                   ("POST", f"/f/{FF_ID}/go", Resp(200, "", headers={"HX-Redirect": "/dl/Zx9"}))])
    r = fuckingfast.FuckingFastPlugin().resolve(f"https://fuckingfast.co/{FF_ID}", ctx)
    assert r.url == "https://fuckingfast.co/dl/Zx9"


def test_fileditch_rate_limit_says_how_long():
    with pytest.raises(PluginError) as e:
        fileditch.status(ctx_for([("GET", "", Resp(429, "{}"))]), "/a/b/c.mp4")
    assert engine_wait_seconds(str(e.value)) == 60


def test_datanodes_download_limit_says_how_long(monkeypatch):
    monkeypatch.setattr(datanodes, "sleep", lambda s: None)
    limit = "<p>You have to wait 1 hour, 30 minutes till next download</p>"
    routes = [("GET", f"/{DN_CODE}", Resp(text=DN_PAGE)), ("POST", "/download", Resp(200, limit))]
    with pytest.raises(PluginError) as e:
        datanodes.DataNodesPlugin().resolve(DN_URL, ctx_for(routes, code="T"))
    assert engine_wait_seconds(str(e.value)) == 90 * 60


def test_datanodes_odd_api_answers_fall_back_to_the_page(monkeypatch):
    from tests.regression.test_sites_hosts_a import Acc
    monkeypatch.setattr(datanodes, "sleep", lambda s: None)
    for body in ([1], {"status": 200, "result": "x"}):
        ctx = ctx_for([("GET", "/api/file/direct_link", Resp(body=body)),
                       ("GET", f"/{DN_CODE}", Resp(text=DN_PAGE)),
                       ("POST", "/download", Resp(body={"url": "https://s1.datanodes.to/d/a/x.zip"}))],
                      code="T", account=Acc(data={"api_key": "K"}))
        assert datanodes.DataNodesPlugin().resolve(DN_URL, ctx).url.endswith("/x.zip")
    ctx = ctx_for([("GET", "/api/file/direct_link",
                    Resp(body={"status": 200, "result": {"url": "https://s1.datanodes.to/d/b", "size": "12 MB"}}))],
                  account=Acc(data={"api_key": "K"}))
    assert datanodes.DataNodesPlugin().resolve(DN_URL, ctx).size is None


# ---------------------------------------------------------------------------
# local-network redirects and credentials on redirects
# ---------------------------------------------------------------------------
def test_local_address_detection():
    assert _hostflow.is_local_host("127.0.0.1") and _hostflow.is_local_host("localhost")
    assert _hostflow.is_local_host("192.168.1.10") and _hostflow.is_local_host("[::1]")
    assert not _hostflow.is_local_host("8.8.8.8") and not _hostflow.is_local_host("example.com")
    assert _hostflow.leaves_public_net("https://host.example/f", "http://10.0.0.5/x")
    assert not _hostflow.leaves_public_net("http://192.168.1.2/f", "http://192.168.1.3/x")


class HeadClient:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def request(self, method, url, headers=None, **kw):
        self.calls.append((url, dict(headers or {})))
        return self.answers[len(self.calls) - 1]


class Ctx:
    settings = None

    def __init__(self, client):
        self.client = client


def test_probe_direct_refuses_redirect_to_local_address():
    c = HeadClient([Resp(302, "", headers={"Location": "http://127.0.0.1:9223/json"})])
    with pytest.raises(PluginError, match="local network"):
        _hostflow.probe_direct("https://file-3.gofile.io/download/x/a.zip", Ctx(c))
    assert len(c.calls) == 1


def test_probe_direct_keeps_account_cookie_on_its_own_host_only():
    c = HeadClient([Resp(302, "", headers={"Location": "/download/y/a.zip"}),
                    Resp(302, "", headers={"Location": "https://cdn.other.example/a.zip"}),
                    Resp(200, "", headers={"content-type": "application/zip", "content-length": "5"})])
    r = _hostflow.probe_direct("https://file-3.gofile.io/download/x/a.zip", Ctx(c),
                               {"cookie": "accountToken=SECRET", "referer": "https://gofile.io/"})
    assert c.calls[1][1]["cookie"] == "accountToken=SECRET"
    assert "cookie" not in c.calls[2][1] and c.calls[2][1]["referer"] == "https://gofile.io/"
    assert "cookie" not in r.headers and r.size == 5


def test_dlproxy_keeps_userinfo_out_of_site_referer_and_errors():
    class C:
        def __init__(self, resp=None, exc=None):
            self.resp, self.exc, self.headers = resp, exc, None

        def request(self, method, url, headers=None, **kw):
            self.headers = headers
            if self.exc:
                raise self.exc
            return self.resp

    url = "https://bob:hunter2@tunnel5.dlproxy.uk/files/a.zip"
    ok = C(Resp(200, "", headers={"content-type": "application/zip"}, url=url))
    g = dlproxy.DirectHostPlugin().grab(url, Ctx(ok))
    assert g.site == "tunnel5.dlproxy.uk" and "hunter2" not in ok.headers["referer"]
    for c in (C(Resp(404, "")), C(Resp(200, "<html>", headers={"content-type": "text/html"})),
              C(exc=OSError("curl: (7) bob:hunter2"))):
        with pytest.raises(PluginError) as e:
            dlproxy.DirectHostPlugin().grab(url, Ctx(c))
        assert "hunter2" not in str(e.value) and "curl" not in str(e.value)


def test_dlproxy_refuses_a_redirect_to_the_local_network():
    class C:
        def request(self, method, url, **kw):
            return Resp(200, "", headers={"content-type": "application/zip"},
                        url="http://192.168.1.1/admin/backup.bin")

    with pytest.raises(PluginError, match="local network"):
        dlproxy.DirectHostPlugin().grab("https://tunnel5.dlproxy.uk/files/a.zip", Ctx(C()))


def test_paste_links_to_local_addresses_are_dropped_on_public_pages():
    text = "http://127.0.0.1:9223/json http://192.168.1.1/cgi-bin/x https://mega.nz/file/a#b"
    assert pastes.extract_links(text, "https://pastebin.com/raw/x") == ["https://mega.nz/file/a#b"]
    # a page on the user's own network may list its neighbours
    assert len(pastes.extract_links(text, "http://192.168.1.5/list.html")) == 3


def test_onefichier_redirect_to_its_own_site_is_not_a_download():
    login = "https://1fichier.com/login.pl"
    ctx = ctx_for([("GET", "?abcdefgh", Resp(302, "", headers={"Location": login})),
                   ("GET", "login.pl", Resp(200, "<html><form>login</form></html>"))])
    try:
        r = onefichier.OneFichierPlugin()._resolve_page(ctx, "https://1fichier.com/?abcdefgh", True)
    except PluginError:
        r = None
    assert r is None or r.url != login
    assert any("login.pl" in c[1] for c in ctx.client.calls)   # the page was read, not downloaded


def test_tapochek_login_always_uses_https():
    assert trackers.TapochekPlugin._base("http://tapochek.net/viewtopic.php?t=1") == "https://tapochek.net/"
    assert trackers.TapochekPlugin._base("http://u:p@tapochek.net:8443/x") == "https://tapochek.net:8443/"


# ---------------------------------------------------------------------------
# bounded work
# ---------------------------------------------------------------------------
def test_mediafire_listing_that_never_ends_is_capped():
    calls = []

    def endless(url, kw):
        calls.append(url)
        return Resp(body={"response": {"result": "Success", "folder_content": {
            "files": [], "folders": [], "more_chunks": "yes"}}})

    items = list(mediafire.MediaFirePlugin()._chunks("abc", "files", ctx_for([("GET", "", endless)])))
    assert items == [] and len(calls) <= mediafire.MAX_ITEMS // 100 + 1


def make_paste(text, iterations=100000, compress=True):
    key, salt, iv = os.urandom(32), os.urandom(8), os.urandom(16)
    adata = [[base64.b64encode(iv).decode(), base64.b64encode(salt).decode(), iterations, 256, 128,
              "aes", "gcm", "zlib" if compress else "none"], "plaintext", 0, 0]
    derived = PBKDF2(key, salt, dkLen=32, count=min(iterations, 100000), hmac_hash_module=SHA256)
    plain = json.dumps({"paste": text}).encode() if isinstance(text, str) else text
    if compress:
        comp = zlib.compressobj(wbits=-15)
        plain = comp.compress(plain) + comp.flush()
    c = AES.new(derived, AES.MODE_GCM, nonce=iv, mac_len=16)
    c.update(json.dumps(adata, separators=(",", ":")).encode())
    ct, tag = c.encrypt_and_digest(plain)
    return {"status": 0, "adata": adata, "ct": base64.b64encode(ct + tag).decode()}, key


def test_privatebin_refuses_huge_key_derivation_cost():
    paste, key = make_paste("x", iterations=10 ** 10)
    with pytest.raises(PluginError, match="unreasonable"):
        privatebin.decrypt_v2(paste, _b58(key))


def _b58(raw: bytes) -> str:
    n, out = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = privatebin.B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def test_privatebin_refuses_a_decompression_bomb(monkeypatch):
    monkeypatch.setattr(privatebin, "MAX_PLAIN", 1000)
    paste, key = make_paste(b"\0" * 100_000)
    with pytest.raises(PluginError, match="too large"):
        privatebin.decrypt_v2(paste, _b58(key))


class PbCtx:
    def __init__(self, answer):
        self.answer = answer
        self.client = self

    def get(self, url, **kw):
        return Resp(200, json.dumps(self.answer))

    def account_for(self, **kw):
        return None


def test_privatebin_children_are_capped_and_bad_pastes_are_plain_errors():
    links = "\n".join(f"https://example.com/f{i}.zip" for i in range(pastes.MAX_CHILDREN + 50))
    paste, key = make_paste(links)
    url = f"https://paste.example.org/?eeb22a8b461f9023#{_b58(key)}"
    r = privatebin.PrivateBinPlugin().resolve(url, PbCtx(paste))
    assert len(r.children) == pastes.MAX_CHILDREN
    broken = dict(paste, adata=[[1, 2, 3]] + paste["adata"][1:])
    for answer in ([1, 2], broken, dict(paste, ct="%%%")):
        with pytest.raises(PluginError) as e:
            privatebin.PrivateBinPlugin().resolve(url, PbCtx(answer))
        assert "Error" not in str(e.value) and "unpack" not in str(e.value)


# ---------------------------------------------------------------------------
# odd server answers
# ---------------------------------------------------------------------------
def test_turbobit_premium_answer_without_url_is_a_clear_error():
    routes = [("GET", "/site-data", Resp(body={})),
              ("POST", "/download/info", Resp(body={"premium": True, "file": {"name": "a.zip"},
                                                    "downloadUrls": [{"name": "a"}]}))]
    with pytest.raises(PluginError, match="no download link"):
        turbobit.TurbobitPlugin().resolve("https://turbobit.net/abcde12345.html", ctx_for(routes))


def test_turbobit_non_numeric_delay_does_not_crash(monkeypatch):
    from opengrab.plugins.builtin import _fdplatform
    monkeypatch.setattr(_fdplatform, "_sleep", lambda s: None)
    routes = [("GET", "/site-data", Resp(body={})),
              ("POST", "/download/info", Resp(body={"file": {"name": "a.zip", "size": 3}})),
              ("POST", "/download/free/init", Resp(body={})),
              ("GET", "/captcha", Resp(body={"driver": "turnstile", "publicKey": "0xK"})),
              ("POST", "/download/free/captcha", Resp(body={"delay": "soon"})),
              ("POST", "/download/free/prepare", Resp(body={"success": True})),
              ("POST", "/download/free/start", Resp(body={"downloadUrl": "https://s1.turbobit.net/a"}))]
    r = turbobit.TurbobitPlugin().resolve("https://turbobit.net/abcde12345.html", ctx_for(routes, code="T"))
    assert r.url == "https://s1.turbobit.net/a"


def test_vikingfile_non_object_answer_is_a_plain_error():
    from tests.regression.test_sites_hosts_a import _vk_routes, VK_HASH
    with pytest.raises(PluginError, match="VikingFile"):
        vikingfile.VikingFilePlugin().resolve(f"https://vikingfile.com/f/{VK_HASH}",
                                              ctx_for(_vk_routes(Resp(body=["x"])), code="T"))


def test_filekeeper_api_list_answer_falls_back():
    from tests.regression.test_sites_hosts_a import Acc
    acc = Acc(data={"api_key": "K"})
    for answer in ([1], {"status": "ok"}):
        ctx = ctx_for([("GET", "/api/file/info", Resp(body={"status": 200, "result": [{"status": 200}]})),
                       ("GET", "/api/file/direct_link", Resp(body=answer))])
        assert filekeeper.FileKeeperPlugin()._api_direct_link(ctx, acc, "abcdefghijkl") is None


# ---------------------------------------------------------------------------
# FTP names and passwords
# ---------------------------------------------------------------------------
def test_ftp_child_url_keeps_a_slash_in_a_name_inside_one_segment():
    u = ftp_engine.child_url("ftp://host/pub/", "../../etc/passwd")
    assert u == "ftp://host/pub/..%2F..%2Fetc%2Fpasswd"


def test_log_file_hides_passwords_in_urls():
    assert redact("grab failed for ftp://bob:s3cret@host/a") == "grab failed for ftp://bob:***@host/a"
    assert redact("https://example.com/a:b@c") == "https://example.com/a:b@c"
    rec = logging.LogRecord("opengrab", logging.WARNING, __file__, 1,
                            "could not handle %s", ("ftps://u:p%40ss@h:990/x",), None)
    out = RedactingFormatter("%(message)s").format(rec)
    assert "p%40ss" not in out and "ftps://u:***@h:990/x" in out


# ---------------------------------------------------------------------------
# browser page lock
# ---------------------------------------------------------------------------
def test_browserpage_closing_twice_does_not_free_another_pages_lock():
    page = browserpage.BrowserPage(driver_factory=lambda: None)
    page.close()                                     # never entered: owns nothing
    assert browserpage.BrowserPage._busy.acquire(timeout=1)
    try:
        page.close()
        assert browserpage.BrowserPage._busy.locked()   # still held by its owner
    finally:
        browserpage.BrowserPage._busy.release()


def test_browserpage_waits_for_the_lock_only_so_long(monkeypatch):
    monkeypatch.setattr(browserpage, "LOCK_WAIT", 0.05)
    assert browserpage.BrowserPage._busy.acquire(timeout=1)
    try:
        with pytest.raises(browserpage.BrowserPageError, match="busy"):
            with browserpage.BrowserPage(driver_factory=lambda: None):
                pass
        assert browserpage.BrowserPage._busy.locked()
    finally:
        browserpage.BrowserPage._busy.release()


def test_log_level_can_be_raised_for_test_builds(monkeypatch, tmp_path):
    import logging
    from opengrab import logs

    monkeypatch.setenv("OPENGRAB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENGRAB_LOG_LEVEL", "debug")
    root = logging.getLogger()
    before = root.level
    try:
        logs.setup_logging()
        assert root.level == logging.DEBUG
        monkeypatch.setenv("OPENGRAB_LOG_LEVEL", "nonsense")
        logs.setup_logging()
        assert root.level == logging.INFO
    finally:
        for h in [h for h in root.handlers if str(tmp_path) in str(getattr(h, "baseFilename", ""))]:
            root.removeHandler(h)
            h.close()
        root.setLevel(before)
