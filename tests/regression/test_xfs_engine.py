"""The shared XFileSharing flow (``_xfs``), the hosts built on it, and the
HTTP client fixes they needed (2026-11-05).

The DataNodes pages here follow the request order of a real JDownloader 2
session against the site (25 Oct 2026): share link with the
``/users`` referer -> 302 to /download + file_code cookie -> op=download1
-> second step drawn by JavaScript (rand/dl-token/countdown attributes, the
Turnstile widget escaped in captcha-html) -> op=download2 -> JSON url.
"""
import html
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from opengrab.http_client import HttpClient
from opengrab.plugins.base import CaptchaRequired, PluginContext, PluginError
from opengrab.plugins.builtin import _xfs, datanodes, fuckingfast, generic
from opengrab.settings import Settings
from tests.regression.test_sites_hosts_a import Client, Resp, ctx_for

# ---------------------------------------------------------------------------
# DataNodes, the real page shapes
# ---------------------------------------------------------------------------
CODE = "hr1lkwtstsa3"
SHARE = f"https://datanodes.to/{CODE}/tool.part02.rar"
WIDGET = '<div class="cf-turnstile" data-sitekey="0x4AAAAAAD8U9nktqncPIkBM"></div>'
STEP1 = f"""<html><body><form id="downloadForm" method="POST" action="">
<input type="hidden" name="op" value="download1"><input type="hidden" name="usr_login" value="">
<input type="hidden" name="id" value="{CODE}"><input type="hidden" name="fname" value="tool.part02.rar">
<input type="hidden" name="referer" value="https://datanodes.to/{CODE}">
<input type="hidden" name="method_premium" value="">
<button type="submit" name="method_free" value="Free Download">Free Download</button>
</form></body></html>"""
STEP2 = f"""<html><body><div id="app"><download-page rand="r4nd0m" dl-token="tok123"
countdown="3" captcha-html="{html.escape(WIDGET, quote=True)}"></download-page></div>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script></body></html>"""
FILE = "https://s7.datanodes.to/d/abcdef/tool.part02.rar"


def dn_routes():
    def share(url, kw):
        if (kw.get("headers") or {}).get("referer") != "https://datanodes.to/users":
            return Resp(200, "<p>Not allowed from domain you're coming from</p>")
        return Resp(200, STEP1, url="https://datanodes.to/download")

    def post(url, kw):
        form = kw["data"]
        if form["op"] == "download1":
            return Resp(200, STEP2, url="https://datanodes.to/download")
        return Resp(200, '{"url": "https%3A%2F%2Fs7.datanodes.to%2Fd%2Fabcdef%2Ftool.part02.rar"}')
    return [("GET", f"/{CODE}", share), ("POST", "/download", post)]


def test_datanodes_follows_the_real_request_order(monkeypatch):
    slept = []
    monkeypatch.setattr(datanodes, "sleep", slept.append)
    with pytest.raises(CaptchaRequired) as e:
        datanodes.DataNodesPlugin().resolve(SHARE, ctx_for(dn_routes()))
    assert e.value.kind == "turnstile"
    ctx = ctx_for(dn_routes(), code="TOKEN")
    r = datanodes.DataNodesPlugin().resolve(SHARE, ctx)
    assert r.url == FILE and r.filename == "tool.part02.rar"
    assert slept == [3]
    get = ctx.client.calls[0]
    assert get[2]["headers"]["referer"] == "https://datanodes.to/users"
    posts = [c[2]["data"] for c in ctx.client.calls if c[0] == "POST"]
    assert [p["op"] for p in posts] == ["download1", "download2"]
    assert posts[0]["method_free"] == "Free Download" and "method_premium" not in posts[0]
    two = posts[1]
    assert (two["id"], two["rand"], two["dl_token"], two["g_captch__a"]) == (CODE, "r4nd0m", "tok123", "1")
    assert two["cf-turnstile-response"] == "TOKEN" and two["method_free"] == "Free Download >>"
    # the plugin no longer replaces the site's cookies with its own Cookie header
    assert all("cookie" not in (c[2].get("headers") or {}) for c in ctx.client.calls)


def test_datanodes_refusal_of_the_referer_is_explained():
    ctx = ctx_for([("GET", f"/{CODE}", Resp(200, "<p>Not allowed from domain you're coming from</p>"))])
    with pytest.raises(PluginError, match="did not accept where it came from"):
        datanodes.DataNodesPlugin().resolve(SHARE, ctx)


# ---------------------------------------------------------------------------
# an unknown XFS site through the generic plugin
# ---------------------------------------------------------------------------
SITE = "https://files.example-host.test"
X_STEP1 = f"""<html><head><title>Download report.pdf</title></head><body>
<h2 class="file-name">report.pdf</h2>
<form method="POST" action=""><input type="hidden" name="op" value="download1">
<input type="hidden" name="usr_login" value=""><input type="hidden" name="id" value="ab12cd34ef56">
<input type="hidden" name="fname" value="report.pdf"><input type="hidden" name="referer" value="">
<input type="submit" name="method_free" value="Free Download"></form></body></html>"""
DIGITS = ('<div style="width:80px;height:26px;font:bold 13px Arial;background:#ccc;text-align:left;">'
          '<span style="position:absolute;padding-left:44px;padding-top:6px;">&#55;</span>'
          '<span style="position:absolute;padding-left:4px;padding-top:4px;">&#51;</span>'
          '<span style="position:absolute;padding-left:24px;padding-top:5px;">&#57;</span></div>')
X_STEP2 = f"""<html><body><form name="F1" method="POST" action="">
<input type="hidden" name="op" value="download2"><input type="hidden" name="id" value="ab12cd34ef56">
<input type="hidden" name="rand" value="q9w8"><input type="hidden" name="referer" value="">
<input type="hidden" name="method_free" value="Free Download"><input type="hidden" name="method_premium" value="">
<input type="hidden" name="adblock_detected" value="1">
<span id="countdown_str">Wait <span id="cxc">2</span> seconds</span>
{DIGITS}<input type="text" name="code" class="captcha_code"></form></body></html>"""
X_FILE = "https://s3.example-host.test:8080/d/xyz987/report.pdf"
PACKED = ("<script>eval(function(p,a,c,k,e,d){while(c--)if(k[c])p=p.replace(new RegExp('\\\\b'+c.toString(a)+'\\\\b','g'),k[c]);return p}"
          "('0 1=\"2://3.4-5.6:7/d/8/9.a\";',11,11,'var|link|https|s3|example|host|test|8080|xyz987|report|pdf'.split('|')))</script>")


def x_routes(final_page):
    def post(url, kw):
        op = kw["data"]["op"]
        if op == "download1":
            return Resp(200, X_STEP2)
        assert kw["data"]["code"] == "397" and kw["data"]["adblock_detected"] == "0"
        return Resp(200, final_page)
    return [("GET", "/ab12cd34ef56", Resp(200, X_STEP1)), ("POST", "/ab12cd34ef56", post)]


def generic_ctx(routes):
    c = PluginContext(Client(routes), settings=SimpleNamespace(grab_timeout_s=10))
    return c


@pytest.mark.parametrize("final", [f'<a href="{X_FILE}">Direct download link</a>', PACKED])
def test_unknown_xfs_site_works_through_the_generic_plugin(monkeypatch, final):
    slept = []
    monkeypatch.setattr(_xfs, "sleep", slept.append)
    p = generic.GenericPagePlugin()
    g = p.grab(f"{SITE}/ab12cd34ef56", generic_ctx(x_routes(final)))
    assert g.extra.get("xfs") and g.filename == "report.pdf" and not g.children
    r = p.resolve(f"{SITE}/ab12cd34ef56", generic_ctx(x_routes(final)))
    assert r.url == X_FILE and r.filename == "report.pdf"
    assert slept == [2]


def test_xfs_page_messages():
    prof = _xfs.Profile(name="Example")
    cases = [("<h2>File Not Found</h2><p>The file was removed by administrator</p>", "offline"),
             ("<p>You have to wait 2 hours, 5 minutes till next download</p>", "wait 125 minutes"),
             ("<p>This file is available for Premium Users only.</p>", "premium"),
             ('<form><input name="op" value="download2"><input name="id" value="a"></form>'
              '<span id="countdown_str">Wait <span>600</span> seconds</span>', "wait 10 minutes")]
    for page, msg in cases:
        ctx = ctx_for([("GET", "/abc", Resp(200, page))])
        with pytest.raises(PluginError, match=msg):
            _xfs.resolve(f"{SITE}/abc", ctx, prof)


def test_looks_like_only_matches_download_forms():
    assert _xfs.looks_like(X_STEP1)
    assert not _xfs.looks_like('<form><input name="op" value="report_file"><input name="id" value="1"></form>')
    assert not _xfs.looks_like("<html><a href='/x.zip'>x</a></html>")


def test_xfs_link_to_the_local_network_is_refused():
    page = X_STEP1.replace("</body>", '<a href="http://192.168.1.10/d/abc/x.zip">x</a></body>')
    with pytest.raises(PluginError, match="local network"):
        _xfs.resolve(f"{SITE}/ab12cd34ef56", ctx_for([("GET", "/ab12cd34ef56", Resp(200, page))]),
                     _xfs.Profile(name="Example"))


# ---------------------------------------------------------------------------
# FuckingFast: Turnstile loaded by script, links on other hosts and paths
# ---------------------------------------------------------------------------
FF_ID = "w7sfvwatp70x"
FF_PAGE = f"""<html><head><title>part01.rar</title>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" defer></script></head>
<body x-data="{{ turnstileSuccess: false }}"><p>Size: 2 GB | Downloads: 10</p>
<button hx-post="/f/{FF_ID}/go">DOWNLOAD</button></body></html>"""


def test_fuckingfast_solves_turnstile_first_with_the_sites_key():
    seen = []

    def go(url, kw):
        seen.append(kw.get("data"))
        if (kw.get("data") or {}).get("cf-turnstile-response") == "TOKEN":
            return Resp(200, "OK", headers={"HX-Redirect": "https://cdn9.fuckingfast.net/x/Zx9"})
        return Resp(403, "turnstile failed")
    routes = [("GET", f"/{FF_ID}", Resp(text=FF_PAGE)), ("POST", "/go", go)]
    with pytest.raises(CaptchaRequired) as e:
        fuckingfast.FuckingFastPlugin().resolve(f"https://fuckingfast.co/{FF_ID}", ctx_for(routes))
    assert e.value.sitekey == fuckingfast.SITEKEY
    r = fuckingfast.FuckingFastPlugin().resolve(f"https://fuckingfast.co/{FF_ID}",
                                                ctx_for(routes, code="TOKEN"))
    assert r.url == "https://cdn9.fuckingfast.net/x/Zx9"
    assert seen == [{"cf-turnstile-response": "TOKEN"}]   # one post, token included


def test_fuckingfast_inline_link_on_another_server():
    page = '<title>a.rar</title><script>window.open("https://cdn2.ffcdn.net/get/abc")</script>'
    r = fuckingfast.FuckingFastPlugin().resolve(f"https://fuckingfast.co/{FF_ID}",
                                                ctx_for([("GET", f"/{FF_ID}", Resp(text=page))]))
    assert r.url == "https://cdn2.ffcdn.net/get/abc"


# ---------------------------------------------------------------------------
# HTTP client: cookies set on a redirect hop are kept
# ---------------------------------------------------------------------------
class _Site(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        _Site.seen.append((self.path, self.headers.get("Cookie", "")))
        if self.path == "/share":
            self.send_response(302)
            self.send_header("Location", "/download")
            self.send_header("Set-Cookie", "file_code=abc; Path=/")
            self.end_headers()
            return
        self.send_response(200)
        if self.path == "/download":
            self.send_header("Set-Cookie", "affiliate=x; Path=/")
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")


@pytest.fixture
def site():
    srv = HTTPServer(("127.0.0.1", 0), _Site)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Site.seen = []
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_cookie_set_on_a_redirect_is_kept(site):
    c = HttpClient(Settings())
    c.get(site + "/share")
    c.get(site + "/next")
    sent = dict(_Site.seen)["/next"]
    assert "file_code=abc" in sent and "affiliate=x" in sent


def test_a_callers_cookie_header_is_merged_not_replaced(site):
    c = HttpClient(Settings())
    c.get(site + "/download")                       # saves affiliate=x
    c.get(site + "/next", headers={"cookie": "mine=1"})
    sent = dict(_Site.seen)["/next"]
    assert "mine=1" in sent and "affiliate=x" in sent
