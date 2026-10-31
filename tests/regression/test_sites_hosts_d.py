"""Site plugins: 1Cloud File, Mdia Load, Buzzheavier, zEtrrent, GoFile storage
links and signed storage-server links copied from the browser.

Offline: a fake client answers from small hand-written fixtures shaped like
the real pages and headers (seen on 2026-10-31 where the site allowed it).
"""
import json

import pytest

from opengrab.plugins.base import CaptchaRequired, PluginContext, PluginError
from opengrab.plugins.builtin import (_hostflow, browserlinks, buzzheavier, gofilestore, mdiaload,
                                      onecloudfile, zetrrent)
from opengrab.plugins.registry import PluginRegistry


# ---------------------------------------------------------------------------
# fake client / context
# ---------------------------------------------------------------------------
class Resp:
    def __init__(self, status=200, text="", headers=None, url="", body=None):
        if body is not None:
            text = json.dumps(body)
        self.status_code = status
        self.text = text
        self.content = text.encode()
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.url = url
        self.cookies = {}

    def json(self):
        return json.loads(self.text)


class Client:
    """routes: list of (method, url-substring, Resp | callable | Exception | list)."""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for i, (m, needle, answer) in enumerate(self.routes):
            if m == method and needle in url:
                if isinstance(answer, list):          # answered in turn
                    answer = answer.pop(0) if len(answer) > 1 else answer[0]
                if isinstance(answer, Exception):
                    raise answer
                r = answer(url, kw) if callable(answer) else answer
                if not r.url:
                    r.url = url
                return r
        return Resp(404, "not routed", url=url)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


class Acc:
    def __init__(self, username="", password="", data=None):
        self.username, self.password, self.data = username, password, data or {}


class Accounts:
    def __init__(self, acc):
        self.find = lambda site: acc
        self.for_domain = lambda host: acc


def ctx_for(routes, code="", account=None):
    c = PluginContext(Client(routes), settings=None,
                      accounts=Accounts(account) if account else None)
    c.manual_code = code       # stands in for a solved captcha
    return c


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(_hostflow, "sleep", slept.append)
    gofilestore._guest.clear()
    return slept


def methods(ctx):
    return [(m, u) for m, u, _ in ctx.client.calls]


# ---------------------------------------------------------------------------
# registry: each URL form goes to the intended plugin first
# ---------------------------------------------------------------------------
ROUTES = {
    "https://1cloudfile.com/2Vv": "1cloudfile",
    "https://www.1cloudfile.com/aB3dE9/report.pdf": "1cloudfile",
    "https://down.mdiaload.com/abc123def456": "mdiaload",
    "https://down.mdiaload.com/abc123def456/name.zip.html": "mdiaload",
    "https://buzzheavier.com/abc123def456": "buzzheavier",
    "https://buzzheavier.com/d/abc123def456": "buzzheavier",
    "https://ts.buzzheavier.com/abc123def456?v=2": "buzzheavier",
    "https://bzzhr.co/abc123def456": "buzzheavier",
    "https://trashbytes.net/d/abc123def456?v=1793413253-abc": "buzzheavier",
    "https://flashbang.sh/d/abc123def456?v=1793413253-abc": "buzzheavier",
    "https://www.zetrrent.com/4XNpwwdT6JD6QF7/file": "zetrrent",
    "https://zetrrent.com/4XNpwwdT6JD6QF7/file": "zetrrent",
    "https://file-eu-par-1.gofile.io/download/web/0b1c2d3e-1111-2222-3333-444455556666/a.zip": "gofilestore",
    "https://store-eu-par-3.gofile.io/download/web/0b1c2d3e-1111-2222-3333-444455556666/a b.zip": "gofilestore",
    "https://store3.gofile.io/download/0b1c2d3e-1111-2222-3333-444455556666/a.zip": "gofilestore",
    "https://gofile.io/d/AbCd12": "gofile",
    "https://s105.turbobit.net/download.php?name=tool.zip&id=12&key=ab": "signedlinks",
    "https://a-12.1fichier.com/c123456789": "signedlinks",
    "https://download2276.mediafire.com/tok3n/wdx5lu4c37sm1cv/tool-1.2.exe": "signedlinks",
    "https://fs12.bowfile.com/token/download/abcdef/tool.zip": "signedlinks",
    "https://srv1.clicknupload.net:8080/d/tok3n/tool.zip": "signedlinks",
}


def test_registry_routes_each_url_form():
    reg = PluginRegistry()
    reg.scan(force=True)
    for pid in ("1cloudfile", "mdiaload", "buzzheavier", "zetrrent", "gofilestore", "signedlinks"):
        assert pid in reg.plugins, (pid, reg.errors)
    for url, pid in ROUTES.items():
        assert reg.for_url(url)[0].id == pid, (url, [p.id for p in reg.for_url(url)])


def test_pages_not_claimed_by_link_plugins():
    sl = browserlinks.SignedServerLinkPlugin()
    for u in ("https://www.mediafire.com/file/wdx5lu4c37sm1cv/tool.exe/file",
              "https://1fichier.com/?abc123", "https://turbobit.net/abc.html",
              "https://www.clicknupload.click/abc123def456"):
        assert not sl.supports(u), u
    assert not gofilestore.GoFileStoragePlugin().supports("https://gofile.io/d/AbCd12")
    for u in ("https://1cloudfile.com/faq", "https://1cloudfile.com/api", "https://1cloudfile.com/"):
        assert onecloudfile.parse(u) is None, u
    assert mdiaload.file_code("https://down.mdiaload.com/download5290259") is None
    assert buzzheavier.parse("https://buzzheavier.com/developers") is None
    assert buzzheavier.parse("https://w.buzzheavier.com/abc123def456") is None
    assert zetrrent.parse("https://www.zetrrent.com/page/terms-of-use") is None


# ---------------------------------------------------------------------------
# signed server links
# ---------------------------------------------------------------------------
def test_signed_link_is_a_file():
    url = "https://fs12.bowfile.com/token/download/abcdef/tool.zip"
    ctx = ctx_for([("HEAD", "fs12.bowfile.com", Resp(200, headers={
        "content-type": "application/zip", "content-length": "4096",
        "content-disposition": 'attachment; filename="tool-1.0.zip"'}))])
    r = browserlinks.SignedServerLinkPlugin().resolve(url, ctx)
    assert (r.url, r.filename, r.size) == (url, "tool-1.0.zip", 4096)
    assert methods(ctx) == [("HEAD", url)]        # never a body download


def test_signed_link_head_not_allowed_still_hands_over():
    url = "https://srv1.clicknupload.net:8080/d/tok3n/tool.zip"
    ctx = ctx_for([("HEAD", "clicknupload", Resp(405))])
    r = browserlinks.SignedServerLinkPlugin().resolve(url, ctx)
    assert r.url == url and r.filename == "tool.zip"


def test_mediafire_expired_points_at_page():
    url = "https://download2276.mediafire.com/tok3n/wdx5lu4c37sm1cv/tool-1.2.exe"
    ctx = ctx_for([("HEAD", "download2276", Resp(302, headers={
        "location": "https://www.mediafire.com/download_repair.php?flag=11&dkey=tok3n&qkey=wdx5lu4c37sm1cv"}))])
    with pytest.raises(PluginError) as e:
        browserlinks.SignedServerLinkPlugin().resolve(url, ctx)
    assert "this download link has expired; add the file's page link instead" in str(e.value)
    assert "https://www.mediafire.com/file/wdx5lu4c37sm1cv" in str(e.value)


@pytest.mark.parametrize("answer", [Resp(410, "<html>gone</html>", {"content-type": "text/html"}),
                                    Resp(200, "<html>", {"content-type": "text/html; charset=utf-8"}),
                                    OSError("Could not resolve host")])
def test_signed_link_expired_messages(answer):
    url = "https://a-12.1fichier.com/c123456789"
    ctx = ctx_for([("HEAD", "1fichier", answer)])
    with pytest.raises(PluginError, match="1fichier: this download link has expired; add the file's page link instead"):
        browserlinks.SignedServerLinkPlugin().resolve(url, ctx)


def test_turbobit_name_from_query():
    url = "https://s105.turbobit.net/download.php?name=tool.zip&id=12&key=ab"
    p = browserlinks.SignedServerLinkPlugin()
    assert p.grab(url, None).filename == "tool.zip"
    ctx = ctx_for([("HEAD", "s105", Resp(200, headers={"content-type": "application/octet-stream"}))])
    assert p.resolve(url, ctx).filename == "tool.zip"


def test_signed_link_challenge_is_not_swallowed():
    ctx = ctx_for([("HEAD", "bowfile", CaptchaRequired(kind="cloudflare"))])
    with pytest.raises(CaptchaRequired):
        browserlinks.SignedServerLinkPlugin().resolve("https://fs1.bowfile.com/token/download/a/b.zip", ctx)


# ---------------------------------------------------------------------------
# GoFile storage links
# ---------------------------------------------------------------------------
GF = "https://file-eu-par-1.gofile.io/download/web/0b1c2d3e-1111-2222-3333-444455556666/sample.zip"
GF_OK = Resp(200, headers={"content-type": "application/zip", "content-length": "10"})
GF_GUEST = Resp(200, body={"status": "ok", "data": {"id": "x", "token": "guestTOKEN", "tier": "guest"}})


def test_gofile_storage_without_cookie():
    ctx = ctx_for([("HEAD", "gofile.io/download", GF_OK)])
    r = gofilestore.GoFileStoragePlugin().resolve(GF, ctx)
    assert r.url == GF and "cookie" not in r.headers and r.size == 10


def test_gofile_storage_refused_then_guest_token():
    seen = []

    def head(url, kw):
        seen.append(kw["headers"].get("cookie"))
        return GF_OK if kw["headers"].get("cookie") else Resp(403, "", {"content-type": "text/plain"})
    ctx = ctx_for([("HEAD", "gofile.io/download", head), ("POST", "api.gofile.io/accounts", GF_GUEST)])
    r = gofilestore.GoFileStoragePlugin().resolve(GF, ctx)
    assert r.headers["cookie"] == "accountToken=guestTOKEN"
    assert seen == [None, "accountToken=guestTOKEN"]


def test_gofile_storage_refused_even_with_token_asks_for_folder():
    ctx = ctx_for([("HEAD", "gofile.io/download", Resp(401)), ("POST", "api.gofile.io/accounts", GF_GUEST)])
    with pytest.raises(PluginError, match=r"add the folder link \(https://gofile.io/d/<code>\)"):
        gofilestore.GoFileStoragePlugin().resolve(GF, ctx)


def test_gofile_storage_gone_and_stored_token():
    ctx = ctx_for([("HEAD", "gofile.io/download", Resp(404, "docContent not found in DB"))])
    with pytest.raises(PluginError, match="does not exist anymore"):
        gofilestore.GoFileStoragePlugin().resolve(GF, ctx)
    ctx = ctx_for([("HEAD", "gofile.io/download", GF_OK)], account=Acc(data={"api_token": "mine"}))
    r = gofilestore.GoFileStoragePlugin().resolve(GF, ctx)
    assert r.headers["cookie"] == "accountToken=mine"
    assert not any(m == "POST" for m, _ in methods(ctx))


# ---------------------------------------------------------------------------
# Buzzheavier
# ---------------------------------------------------------------------------
BZ_ID = "abc123def456"
BZ_PAGE = """<!DOCTYPE html><html><head><title>sample-1.0.zip</title>
<meta name="title" content="sample-1.0.zip"></head><body>
<h1>BUZZHEAVIER</h1><table><tr><td>Size</td><td>1.5 MB</td></tr></table>
<a hx-get="/abc123def456/download" class="link-button">Download</a></body></html>"""
BZ_STORE = "https://trashbytes.net/d/abc123def456?v=1793413253-sig"


def test_buzzheavier_hx_redirect():
    ctx = ctx_for([("GET", "/abc123def456/download", Resp(204, headers={"hx-redirect": BZ_STORE})),
                   ("GET", "buzzheavier.com/abc123def456", Resp(200, BZ_PAGE))])
    r = buzzheavier.BuzzheavierPlugin().resolve(f"https://ts.buzzheavier.com/{BZ_ID}?v=3", ctx)
    assert (r.url, r.filename, r.size) == (BZ_STORE, "sample-1.0.zip", int(1.5 * 1024 ** 2))
    dl = [kw for m, u, kw in ctx.client.calls if u.endswith("/download")][0]
    assert dl["headers"]["hx-request"] == "true" and dl["follow"] is False


def test_buzzheavier_own_download_route_is_followed():
    ctx = ctx_for([("GET", "/abc123def456/download", Resp(200, headers={"hx-redirect": "/d/abc123def456"})),
                   ("HEAD", "buzzheavier.com/d/abc123def456", Resp(302, headers={"location": BZ_STORE})),
                   ("GET", "buzzheavier.com/abc123def456", Resp(200, BZ_PAGE))])
    assert buzzheavier.BuzzheavierPlugin().resolve(f"https://buzzheavier.com/{BZ_ID}", ctx).url == BZ_STORE


def test_buzzheavier_offline_and_no_link():
    ctx = ctx_for([("GET", "buzzheavier.com/", Resp(404, "<title>Not Found</title>"))])
    with pytest.raises(PluginError, match="offline"):
        buzzheavier.BuzzheavierPlugin().resolve(f"https://buzzheavier.com/{BZ_ID}", ctx)
    ctx = ctx_for([("GET", "/download", Resp(200, "")), ("GET", "buzzheavier.com/", Resp(200, BZ_PAGE))])
    with pytest.raises(PluginError, match="no download link"):
        buzzheavier.BuzzheavierPlugin().resolve(f"https://buzzheavier.com/{BZ_ID}", ctx)


def test_buzzheavier_storage_link_expired():
    ctx = ctx_for([("HEAD", "trashbytes.net", Resp(403, "expired"))])
    with pytest.raises(PluginError) as e:
        buzzheavier.BuzzheavierPlugin().resolve(BZ_STORE, ctx)
    assert "add the file's page link instead" in str(e.value)
    assert "https://buzzheavier.com/abc123def456" in str(e.value)
    ctx = ctx_for([("HEAD", "trashbytes.net", Resp(200, headers={"content-type": "application/zip"}))])
    assert buzzheavier.BuzzheavierPlugin().resolve(BZ_STORE, ctx).url == BZ_STORE


# ---------------------------------------------------------------------------
# 1Cloud File (YetiShare)
# ---------------------------------------------------------------------------
OC_PAGE = """<html><head><title>report-2024.pdf - 1Cloud File</title></head><body>
<h2>report-2024.pdf (2.0 MB)</h2>
<script>var seconds = 10; function done(){ $('.download-timer').html("<a class='btn' href='https://1cloudfile.com/2Vv?pt=Tok3nAbC%3D'>download</a>"); }</script>
<div class="download-timer">Please wait <span id="download-timer">10</span> seconds</div></body></html>"""
OC_CAPTCHA = """<html><body><form method="post" action="https://1cloudfile.com/2Vv?pt=Tok3nAbC%3D">
<div class="g-recaptcha" data-sitekey="6Lsitekey"></div>
<input type="hidden" name="submitted" value="1"><input type="hidden" name="d" value="1">
<input type="submit" value="Download"></form></body></html>"""
OC_STORE = "https://fs3.1cloudfile.com/files/abc/report-2024.pdf"


def test_onecloudfile_countdown_then_token_link(no_sleep):
    ctx = ctx_for([("HEAD", "?pt=", Resp(302, headers={"location": OC_STORE})),
                   ("GET", "?pt=", Resp(302, headers={"location": OC_STORE})),
                   ("GET", "1cloudfile.com/2Vv", Resp(200, OC_PAGE, {"content-type": "text/html"}))])
    r = onecloudfile.OneCloudFilePlugin().resolve("https://1cloudfile.com/2Vv", ctx)
    assert (r.url, r.filename, r.size) == (OC_STORE, "report-2024.pdf", 2 * 1024 ** 2)
    assert no_sleep == [10]


def test_onecloudfile_captcha_form(no_sleep):
    ctx = ctx_for([("HEAD", "?pt=", Resp(200, "", {"content-type": "text/html"})),
                   ("GET", "?pt=", Resp(200, OC_CAPTCHA, {"content-type": "text/html"})),
                   ("POST", "?pt=", Resp(302, headers={"location": OC_STORE})),
                   ("GET", "1cloudfile.com/2Vv", Resp(200, OC_PAGE, {"content-type": "text/html"}))],
                  code="SOLVED")
    r = onecloudfile.OneCloudFilePlugin().resolve("https://1cloudfile.com/2Vv", ctx)
    assert r.url == OC_STORE
    post = [kw for m, u, kw in ctx.client.calls if m == "POST"][0]
    assert post["data"]["g-recaptcha-response"] == "SOLVED" and post["data"]["submitted"] == "1"


def test_onecloudfile_offline_wait_and_long_countdown():
    ctx = ctx_for([("GET", "1cloudfile.com/zzz", Resp(404, "404 Oops - Page Not Found!"))])
    with pytest.raises(PluginError, match="offline"):
        onecloudfile.OneCloudFilePlugin().resolve("https://1cloudfile.com/zzz", ctx)
    ctx = ctx_for([("GET", "1cloudfile.com/2Vv", Resp(302, headers={
        "location": "https://1cloudfile.com/error?e=You+must+wait+15+minutes+between+downloads"}))])
    with pytest.raises(PluginError, match="wait 15 minutes"):
        onecloudfile.OneCloudFilePlugin().resolve("https://1cloudfile.com/2Vv", ctx)
    ctx = ctx_for([("GET", "1cloudfile.com/2Vv", Resp(200, OC_PAGE.replace("10", "600"),
                                                      {"content-type": "text/html"}))])
    with pytest.raises(PluginError, match="wait 10 minutes"):
        onecloudfile.OneCloudFilePlugin().resolve("https://1cloudfile.com/2Vv", ctx)


def test_onecloudfile_token_url_that_is_the_file():
    ctx = ctx_for([("HEAD", "?pt=", Resp(200, "", {"content-type": "application/pdf", "content-length": "77"})),
                   ("GET", "1cloudfile.com/2Vv", Resp(200, OC_PAGE, {"content-type": "text/html"}))])
    r = onecloudfile.OneCloudFilePlugin().resolve("https://1cloudfile.com/2Vv", ctx)
    assert "pt=" in r.url and r.size == 77
    assert not any(m == "GET" and "pt=" in u for m, u in methods(ctx))


# ---------------------------------------------------------------------------
# Mdia Load (XFileSharing)
# ---------------------------------------------------------------------------
MD = "https://down.mdiaload.com"
MD_PAGE0 = """<!-- download0.html --><div align="center"><h2>Download File sample.zip</h2>
<div id="download"><a class="btn btn-info" href="https://down.mdiaload.com/?op=payments" title="Premium Download">Premium Download</a>
<a class="btn btn-info" href="https://down.mdiaload.com/download61.html" title="Free Download">Free Download</a></div></div>"""
MD_PAGE0_EMPTY = MD_PAGE0.replace("Download File sample.zip", "Download File ")
MD_PAGE61_EMPTY = """<center><h4> Download File </h4><h4> Click To Download </h4></center>"""
MD_FORM1 = """<form method="POST" action=""><input type="hidden" name="op" value="download1">
<input type="hidden" name="usr_login" value=""><input type="hidden" name="id" value="abc123def456">
<input type="hidden" name="fname" value="sample.zip"><input type="hidden" name="referer" value="">
<input type="submit" name="method_free" value="Free Download"></form><p>Size: (1.0 MB)</p>"""
MD_FORM2 = """<form name="F1" method="POST" action=""><input type="hidden" name="op" value="download2">
<input type="hidden" name="id" value="abc123def456"><input type="hidden" name="rand" value="r4nd">
<input type="hidden" name="method_free" value="Free Download"><div class="g-recaptcha" data-sitekey="6Lmd"></div>
<span id="countdown_str">Wait <span id="cxc">15</span> seconds</span>
<button id="downloadbtn">Create Download Link</button></form>"""
MD_FILE = "https://s3.mdiaload.com:8443/d/longtoken/sample.zip"


def test_mdiaload_full_free_flow(no_sleep):
    posts = []

    def post(url, kw):
        posts.append(dict(kw["data"]))
        if kw["data"]["op"] == "download1":
            return Resp(200, MD_FORM2, {"content-type": "text/html"})
        return Resp(302, headers={"location": MD_FILE})
    ctx = ctx_for([("GET", "/abc123def456", Resp(302, headers={"location": MD + "/download5290259"})),
                   ("GET", "/download5290259", Resp(200, MD_PAGE0, {"content-type": "text/html"})),
                   ("GET", "/download61.html", Resp(200, MD_FORM1, {"content-type": "text/html"})),
                   ("POST", MD, post)], code="TOKEN")
    r = mdiaload.MdiaLoadPlugin().resolve(f"{MD}/abc123def456", ctx)
    assert (r.url, r.filename, r.size) == (MD_FILE, "sample.zip", 1024 ** 2)
    assert [p["op"] for p in posts] == ["download1", "download2"]
    assert posts[1]["g-recaptcha-response"] == "TOKEN" and posts[1]["rand"] == "r4nd"
    assert no_sleep == [15]


def test_mdiaload_missing_file_chain():
    # what the live site answers for a code that does not exist
    ctx = ctx_for([("GET", "/zzzzzzzzzzzz", Resp(302, headers={"location": MD + "/download5290259"})),
                   ("GET", "/download5290259", Resp(200, MD_PAGE0_EMPTY, {"content-type": "text/html"})),
                   ("GET", "/download61.html", Resp(200, MD_PAGE61_EMPTY, {"content-type": "text/html"}))])
    with pytest.raises(PluginError, match="offline"):
        mdiaload.MdiaLoadPlugin().resolve(f"{MD}/zzzzzzzzzzzz", ctx)


def test_mdiaload_wait_and_premium_only():
    ctx = ctx_for([("GET", "/abc123def456", Resp(200, "<p>You have to wait 1 hour, 5 minutes until the next download</p>",
                                                 {"content-type": "text/html"}))])
    with pytest.raises(PluginError, match="wait 65 minutes"):
        mdiaload.MdiaLoadPlugin().resolve(f"{MD}/abc123def456", ctx)


# ---------------------------------------------------------------------------
# zEtrrent
# ---------------------------------------------------------------------------
ZT = "https://www.zetrrent.com/4XNpwwdT6JD6QF7/file"
ZT_PAGE = """<html><head><title>zEtrrent.com — Download — ubuntu-24.04-desktop-amd64.iso.torrent</title></head>
<body><a class="btn" href="/4XNpwwdT6JD6QF7/download">Download torrent</a></body></html>"""
ZT_MAGNET = """<html><head><title>zEtrrent.com — Download — debian.torrent</title></head>
<body><a href="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&amp;dn=debian">Magnet</a></body></html>"""


def test_zetrrent_torrent_link():
    ctx = ctx_for([("GET", "zetrrent.com/4XNpwwdT6JD6QF7/file", Resp(200, ZT_PAGE))])
    r = zetrrent.ZetrrentPlugin().resolve(ZT, ctx)
    assert r.url == "https://www.zetrrent.com/4XNpwwdT6JD6QF7/download"
    assert r.filename == "ubuntu-24.04-desktop-amd64.iso.torrent"


def test_zetrrent_magnet_becomes_child():
    ctx = ctx_for([("GET", "zetrrent.com/", Resp(200, ZT_MAGNET))])
    r = zetrrent.ZetrrentPlugin().resolve(ZT, ctx)
    assert r.url == "" and r.children[0][0].startswith("magnet:?xt=urn:btih:0123")
    assert "&dn=debian" in r.children[0][0]


def test_zetrrent_cloudflare_challenge_parks_link():
    ctx = ctx_for([("GET", "zetrrent.com/", CaptchaRequired(kind="cloudflare", site_url=ZT))])
    with pytest.raises(CaptchaRequired):
        zetrrent.ZetrrentPlugin().resolve(ZT, ctx)
    ctx = ctx_for([("GET", "zetrrent.com/", Resp(404, "not found"))])
    with pytest.raises(PluginError, match="offline"):
        zetrrent.ZetrrentPlugin().resolve(ZT, ctx)
