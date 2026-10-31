"""Offline tests for the 1fichier, Turbobit, Hitfile and FileKeeper plugins.

A fake client answers from small fixtures modelled on each site's pages and
JSON API; ctx.solve_captcha is faked, and the plugins' sleep is replaced so
countdowns are recorded rather than waited.
"""
import json

import pytest

from opengrab.models import Account
from opengrab.plugins.base import (LoginRequired, PaywallRequired, PluginContext,
                                   PluginError)
from opengrab.plugins.builtin import _fdplatform, filekeeper, onefichier
from opengrab.plugins.builtin.filekeeper import FileKeeperPlugin
from opengrab.plugins.builtin.hitfile import HitfilePlugin
from opengrab.plugins.builtin.onefichier import OneFichierPlugin
from opengrab.plugins.builtin.turbobit import TurbobitPlugin


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------
class Resp:
    def __init__(self, status=200, text="", headers=None, url=""):
        self.status_code = status
        self.text = text if isinstance(text, str) else json.dumps(text)
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.url = url
        self.cookies = {}

    def json(self):
        return json.loads(self.text)


class FakeClient:
    """routes: {(METHOD, url): Resp | callable(kw) -> Resp | [Resp, ...]}"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.cookies = {}

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.routes.get((method, url))
        if r is None:
            raise AssertionError(f"unexpected {method} {url}")
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if callable(r):
            r = r(kw)
        r.url = r.url or url
        return r

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def set_cookie(self, url, name, value):
        self.cookies[(url, name)] = value

    def sent(self, method, url):
        return [kw for m, u, kw in self.calls if m == method and u == url]


class Accounts:
    def __init__(self, acc):
        self.acc = acc

    def find(self, site):
        return self.acc if self.acc and self.acc.site == site else None

    for_domain = find


class Ctx(PluginContext):
    def __init__(self, client, account=None, answer="ANSWER"):
        super().__init__(client, settings=None, accounts=Accounts(account))
        self.captchas = []
        self.answer = answer

    def solve_captcha(self, image_b64="", kind="text", site_url="", sitekey="", image_url=""):
        self.captchas.append({"kind": kind, "sitekey": sitekey, "site_url": site_url,
                              "image_b64": image_b64, "image_url": image_url})
        return self.answer


@pytest.fixture
def slept(monkeypatch):
    waits = []
    for mod in (onefichier, _fdplatform, filekeeper):
        monkeypatch.setattr(mod, "_sleep", waits.append)
    return waits


# ---------------------------------------------------------------------------
# 1fichier
# ---------------------------------------------------------------------------
F1 = "https://1fichier.com/?abcde12345"
CHECK = "https://1fichier.com/check_links.pl"
API = "https://api.1fichier.com/v1"

F1_FREE = """<html><body>
<table><tr><td class="normal">File Name :</td><td class="normal">tool-1.0.zip</td></tr></table>
<form action="https://1fichier.com/?abcde12345" method="post" class="ct_warn">
 <input type="hidden" name="adz" value="5.3.21"/>
 <div class="cf-turnstile" data-sitekey="0x4AAAAAAA1fichierKey"></div>
 <input type="submit" id="dlb" value="Access to download" class="btn-orange btn-general" disabled/>
</form>
<script>var ct = 30;</script>
</body></html>"""
F1_LINK = """<html><body>
<a href="https://a-7.1fichier.com/c123456789" style="float:none" class="ok btn-general btn-orange">Click here to download the file</a>
</body></html>"""
F1_WAIT = """<div class="ct_warn"><span>Warning !</span><br/>Without subscription, you must wait
between downloads.<br/>You must wait 14 minutes to download again or subscribe.</div>"""
F1_GONE = "<html><div class='bloc2'>The requested file does not exist<br/>It could be deleted by its owner.</div></html>"


def test_onefichier_supports_aliases_and_old_links():
    p = OneFichierPlugin()
    assert p.supports(F1)
    assert p.supports("https://1fichier.com/?abcde12345&af=99")
    assert p.supports("https://abcde12345.1fichier.com/")
    assert p.supports("https://www.alterupload.com/?abcde12345")
    assert p.supports("https://megadl.fr/?abcde12345")
    assert onefichier.file_id("https://abcde12345.1fichier.com/") == "abcde12345"
    assert not p.supports("https://1fichier.com/dir/XYZabc")
    assert not p.supports("https://1fichier.com/login.pl")
    assert not p.supports("https://example.com/?abcde12345")


def test_onefichier_grab_uses_public_link_checker():
    c = FakeClient({("POST", CHECK): Resp(text=f"{F1};tool-1.0.zip;123456\n")})
    g = OneFichierPlugin().grab(F1, Ctx(c))
    assert (g.filename, g.size) == ("tool-1.0.zip", 123456)
    c = FakeClient({("POST", CHECK): Resp(text=f"{F1};;;NOT FOUND\n")})
    with pytest.raises(PluginError, match="offline"):
        OneFichierPlugin().grab(F1, Ctx(c))


def test_onefichier_free_flow_countdown_and_turnstile(slept):
    def post(kw):
        assert kw["data"]["adz"] == "5.3.21"
        assert kw["data"]["cf-turnstile-response"] == "ANSWER"
        return Resp(text=F1_LINK)
    c = FakeClient({("GET", F1): Resp(text=F1_FREE), ("POST", F1): post})
    ctx = Ctx(c)
    res = OneFichierPlugin().resolve(F1, ctx)
    assert res.url == "https://a-7.1fichier.com/c123456789"
    assert res.headers["referer"] == F1
    assert ctx.captchas == [{"kind": "turnstile", "sitekey": "0x4AAAAAAA1fichierKey",
                             "site_url": F1, "image_b64": "", "image_url": ""}]
    assert slept == [31]


def test_onefichier_wait_and_offline_messages(slept):
    c = FakeClient({("GET", F1): Resp(text=F1_WAIT)})
    with pytest.raises(PluginError, match=r"wait 14 minutes"):
        OneFichierPlugin().resolve(F1, Ctx(c))
    c = FakeClient({("GET", F1): Resp(status=404, text=F1_GONE)})
    with pytest.raises(PluginError, match="offline"):
        OneFichierPlugin().resolve(F1, Ctx(c))
    # a countdown longer than 90 s becomes a wait message, nothing is posted
    c = FakeClient({("GET", F1): Resp(text=F1_FREE.replace("var ct = 30", "var ct = 600"))})
    with pytest.raises(PluginError, match=r"wait 10 minutes"):
        OneFichierPlugin().resolve(F1, Ctx(c))
    assert slept == []


def test_onefichier_wait_after_post(slept):
    c = FakeClient({("GET", F1): Resp(text=F1_FREE), ("POST", F1): Resp(text=F1_WAIT)})
    with pytest.raises(PluginError, match=r"wait 14 minutes"):
        OneFichierPlugin().resolve(F1, Ctx(c))


def test_onefichier_premium_api_key():
    acc = Account(site="1fichier.com", username="me@example.com", premium=True,
                  data={"api_token": "TESTKEY"})

    def info(kw):
        assert kw["headers"]["authorization"] == "Bearer TESTKEY"
        assert kw["json_body"] == {"url": F1}
        return Resp(text={"filename": "tool-1.0.zip", "size": 4096})

    c = FakeClient({("POST", f"{API}/file/info.cgi"): info,
                    ("POST", f"{API}/download/get_token.cgi"):
                        Resp(text={"status": "OK", "url": "https://a-9.1fichier.com/p9999"})})
    res = OneFichierPlugin().resolve(F1, Ctx(c, acc))
    assert (res.url, res.filename, res.size) == ("https://a-9.1fichier.com/p9999", "tool-1.0.zip", 4096)
    assert not [u for _, u, _ in c.calls if u.startswith("https://1fichier.com")]  # no page scraping


def test_onefichier_api_key_errors():
    acc = Account(site="1fichier.com", username="x", premium=True, data={"api_key": "BAD"})
    c = FakeClient({("POST", f"{API}/file/info.cgi"):
                    Resp(status=401, text={"status": "KO", "message": "Not authenticated #247"})})
    with pytest.raises(LoginRequired):
        OneFichierPlugin().resolve(F1, Ctx(c, acc))
    c = FakeClient({("POST", f"{API}/file/info.cgi"): Resp(text={"filename": "a", "size": 1}),
                    ("POST", f"{API}/download/get_token.cgi"):
                        Resp(text={"status": "KO", "message": "Resource not found #631"})})
    with pytest.raises(PluginError, match="offline"):
        OneFichierPlugin().resolve(F1, Ctx(c, acc))


def test_onefichier_premium_login_direct_redirect(slept):
    acc = Account(site="1fichier.com", username="me@example.com", password="pw", premium=True)

    def login(kw):
        assert kw["data"]["mail"] == "me@example.com" and kw["data"]["pass"] == "pw"
        return Resp(text="<a href='/logout.pl'>Logout</a>")

    c = FakeClient({("POST", "https://1fichier.com/login.pl"): login,
                    ("GET", F1): Resp(status=302, headers={"Location": "https://a-3.1fichier.com/p42"})})
    ctx = Ctx(c, acc)
    res = OneFichierPlugin().resolve(F1, ctx)
    assert res.url == "https://a-3.1fichier.com/p42"
    assert ctx.captchas == [] and slept == []


def test_onefichier_login_rejected():
    acc = Account(site="1fichier.com", username="me@example.com", password="bad", premium=True)
    c = FakeClient({("POST", "https://1fichier.com/login.pl"):
                    Resp(text='<form class="auth"><input id="lg-pass" name="pass"></form>')})
    with pytest.raises(LoginRequired):
        OneFichierPlugin().resolve(F1, Ctx(c, acc))


# ---------------------------------------------------------------------------
# Turbobit / Hitfile (shared platform)
# ---------------------------------------------------------------------------
def tb_routes(api, extra=None, captcha=None):
    routes = {
        ("GET", f"{api}/site-data"): Resp(text={"siteName": "turbobit", "user": None}),
        ("POST", f"{api}/download/info"): Resp(text={"file": {"id": "abcdef123456", "name": "tool.zip",
                                                              "size": 2048}, "premium": False}),
        ("GET", f"{api}/captcha"): Resp(text=captcha or {"driver": "turnstile", "index": 0,
                                                         "publicKey": "0x4AAAAAAAtbKey"}),
        ("POST", f"{api}/download/free/init"): Resp(text={"file": {"id": "abcdef123456"}}),
        ("POST", f"{api}/download/free/captcha"): Resp(text={"delay": 60}),
        ("POST", f"{api}/download/free/prepare"): Resp(text={"success": True}),
        ("POST", f"{api}/download/free/start"): Resp(text={"downloadUrl": "https://s12.turbobit.net/dl/xyz/tool.zip"}),
    }
    routes.update(extra or {})
    return routes


TB_API = "https://app.turbobit.net/api"
HF_API = "https://app.hitfile.net/api"


def test_turbobit_hitfile_supports():
    tb, hf = TurbobitPlugin(), HitfilePlugin()
    assert tb.file_id("https://turbobit.net/abcdef123456.html") == "abcdef123456"
    assert tb.file_id("https://turbobit.net/abcdef123456/tool-1.0.zip.html") == "abcdef123456"
    assert tb.file_id("https://www.turbobit.net/download/free/abcdef123456") == "abcdef123456"
    assert tb.file_id("https://trbt.cc/abcdef123456") == "abcdef123456"
    assert tb.file_id("https://turbobit.cc/abcdef123456.html") == "abcdef123456"
    assert not tb.supports("https://turbobit.net/premium")
    assert not tb.supports("https://turbobit.net/download/folder/123")
    assert not tb.supports("https://hitfile.net/AbC12dE")
    assert hf.file_id("https://hitfile.net/AbC12dE") == "AbC12dE"
    assert hf.file_id("https://hitfile.net/AbC12dE/tool.zip") == "AbC12dE"
    assert hf.file_id("https://hil.to/AbC12dE") == "AbC12dE"
    assert not hf.supports("https://hitfile.net/faq")
    assert not hf.supports("https://turbobit.net/abcdef123456.html")


def test_turbobit_grab_and_offline():
    c = FakeClient(tb_routes(TB_API))
    g = TurbobitPlugin().grab("https://turbobit.net/abcdef123456.html", Ctx(c))
    assert (g.filename, g.size) == ("tool.zip", 2048)
    assert c.sent("POST", f"{TB_API}/download/info")[0]["json_body"]["fileId"] == "abcdef123456"
    c = FakeClient(tb_routes(TB_API, {("POST", f"{TB_API}/download/info"):
                                      Resp(404, {"error_name": "file_is_not_available_for_download"})}))
    with pytest.raises(PluginError, match="offline"):
        TurbobitPlugin().grab("https://turbobit.net/abcdef123456.html", Ctx(c))


def test_short_domain_is_passed_to_info():
    c = FakeClient(tb_routes(TB_API))
    TurbobitPlugin().grab("https://trbt.cc/abcdef123456", Ctx(c))
    assert c.sent("POST", f"{TB_API}/download/info")[0]["json_body"]["shortDomain"] == "trbt.cc"


def test_turbobit_free_flow(slept):
    c = FakeClient(tb_routes(TB_API))
    ctx = Ctx(c)
    res = TurbobitPlugin().resolve("https://turbobit.net/abcdef123456.html", ctx)
    assert res.url == "https://s12.turbobit.net/dl/xyz/tool.zip"
    assert (res.filename, res.size) == ("tool.zip", 2048)
    assert ctx.captchas[0]["kind"] == "turnstile"
    assert ctx.captchas[0]["sitekey"] == "0x4AAAAAAAtbKey"
    assert ctx.captchas[0]["site_url"] == "https://turbobit.net/download/free/abcdef123456"
    body = c.sent("POST", f"{TB_API}/download/free/captcha")[0]["json_body"]
    assert body == {"fileId": "abcdef123456", "captchaResponse": "ANSWER", "captchaIndex": 0}
    assert slept == [61]
    order = [u.rsplit("/api", 1)[1] for m, u, _ in c.calls if m == "POST"]
    assert order[-4:] == ["/download/free/init", "/download/free/captcha",
                          "/download/free/prepare", "/download/free/start"]


def test_turbobit_text_captcha_driver(slept):
    cfg = {"driver": "gregwar", "index": 2, "publicKey": "k123\niVBORw0KGgo="}
    c = FakeClient(tb_routes(TB_API, captcha=cfg))
    ctx = Ctx(c, answer="x7k2")
    TurbobitPlugin().resolve("https://turbobit.net/abcdef123456.html", ctx)
    assert ctx.captchas[0]["kind"] == "text" and ctx.captchas[0]["image_b64"] == "iVBORw0KGgo="
    body = c.sent("POST", f"{TB_API}/download/free/captcha")[0]["json_body"]
    assert body["captchaResponse"] == "k123\nx7k2" and body["captchaIndex"] == 2


def test_turbobit_ip_ban_and_long_delay(slept):
    c = FakeClient(tb_routes(TB_API, {("POST", f"{TB_API}/download/free/init"):
                                      Resp(text={"file": {"id": "x"}, "ipBan": {"delay": 1200}})}))
    ctx = Ctx(c)
    with pytest.raises(PluginError, match=r"wait 20 minutes"):
        TurbobitPlugin().resolve("https://turbobit.net/abcdef123456.html", ctx)
    assert ctx.captchas == []
    c = FakeClient(tb_routes(TB_API, {("POST", f"{TB_API}/download/free/captcha"):
                                      Resp(text={"delay": 900})}))
    with pytest.raises(PluginError, match=r"wait 15 minutes"):
        TurbobitPlugin().resolve("https://turbobit.net/abcdef123456.html", Ctx(c))
    assert slept == []


def test_turbobit_captcha_rejected(slept):
    c = FakeClient(tb_routes(TB_API, {("POST", f"{TB_API}/download/free/captcha"):
                                      Resp(422, {"errors": {"captcha": ["Incorrect captcha"]}})}))
    with pytest.raises(PluginError, match="rejected the captcha"):
        TurbobitPlugin().resolve("https://turbobit.net/abcdef123456.html", Ctx(c))


def test_turbobit_premium_only_file():
    c = FakeClient(tb_routes(TB_API, {("POST", f"{TB_API}/download/info"):
                                      Resp(text={"file": {"name": "a"}, "premium": False,
                                                 "premiumOnlyDownload": True})}))
    with pytest.raises(PaywallRequired):
        TurbobitPlugin().resolve("https://turbobit.net/abcdef123456.html", Ctx(c))


def test_hitfile_premium_login_and_dhc_captcha(slept):
    acc = Account(site="hitfile.net", username="me@example.com", password="pw", premium=True)

    def login(kw):
        b = kw["json_body"]
        assert (b["email"], b["password"], b["captcha"], b["captchaResponse"]) == \
            ("me@example.com", "pw", True, "ANSWER")
        return Resp(text={"user": {"id": 1}})

    routes = tb_routes(HF_API, {
        ("GET", f"{HF_API}/user/info"): Resp(401, {"error_name": "unauthorized"}),
        ("POST", f"{HF_API}/auth/login"): login,
        ("POST", f"{HF_API}/download/info"): Resp(text={"file": {"name": "big.iso", "size": 9},
                                                        "premium": True, "needCaptcha": True}),
        ("POST", f"{HF_API}/download/premium/dhc"):
            Resp(text={"downloadUrls": ["https://p1.hitfile.net/dl/big.iso"]}),
    })
    c = FakeClient(routes)
    ctx = Ctx(c, acc)
    res = HitfilePlugin().resolve("https://hitfile.net/AbC12dE", ctx)
    assert res.url == "https://p1.hitfile.net/dl/big.iso"
    assert len(ctx.captchas) == 2 and ctx.captchas[0]["site_url"] == "https://hitfile.net/login"
    assert not c.sent("POST", f"{HF_API}/download/free/init") and slept == []


def test_turbobit_premium_session_cookies_skip_login():
    acc = Account(site="turbobit.net", username="me", premium=True,
                  data={"cookie_header": "user_session=abc123"})
    routes = tb_routes(TB_API, {("POST", f"{TB_API}/download/info"):
                                Resp(text={"file": {"name": "a.zip", "size": 1}, "premium": True,
                                           "downloadUrls": ["https://p.turbobit.net/a.zip"]})})
    c = FakeClient(routes)
    ctx = Ctx(c, acc)
    res = TurbobitPlugin().resolve("https://turbobit.net/abcdef123456.html", ctx)
    assert res.url == "https://p.turbobit.net/a.zip"
    assert c.cookies[("https://app.turbobit.net/", "user_session")] == "abc123"
    assert ctx.captchas == [] and not c.sent("POST", f"{TB_API}/auth/login")


# ---------------------------------------------------------------------------
# FileKeeper
# ---------------------------------------------------------------------------
FK = "https://filekeeper.net/abcdefgh1234"
FK_DL = "https://filekeeper.net/download"
FK_PAGE = """<html><head><title>Download tool-2.1.tar.gz</title></head><body>
<div id="dl" data-code="abcdefgh1234" data-rand="r4nd" data-referer="" data-method="Free download"
     data-countdown="10" data-has-password="false" data-has-captcha="false" data-size="52428800"></div>
<h1>tool-2.1.tar.gz</h1></body></html>"""
FK_CAPTCHA_PAGE = """<html><body><form name="F1" method="POST" action="">
<input type="hidden" name="op" value="download2"><input type="hidden" name="id" value="abcdefgh1234">
<input type="hidden" name="rand" value="r9"><input type="hidden" name="referer" value="">
<input type="hidden" name="method_free" value="Free Download">
<div class="g-recaptcha" data-sitekey="6LcFKsitekey"></div>
<span id="countdown_str">Wait <span id="cxc">5</span> seconds</span></form></body></html>"""
FK_404 = "<html><head><title>404 File Not Found</title></head><body>The file you were looking for could not be found</body></html>"


def test_filekeeper_supports():
    p = FileKeeperPlugin()
    assert p.supports(FK)
    assert p.supports("https://www.filekeeper.net/abcdefgh1234/tool.tar.gz.html")
    assert not p.supports("https://filekeeper.net/login")
    assert not p.supports("https://filekeeper.net/download")
    assert not p.supports("https://example.net/abcdefgh1234")


def test_filekeeper_free_flow_with_redirects(slept):
    def post(kw):
        assert kw["data"]["op"] == "download2" and kw["data"]["id"] == "abcdefgh1234"
        assert kw["data"]["rand"] == "r4nd" and kw["data"]["method_free"] == "Free download"
        return Resp(302, headers={"Location": "https://fs7.filekeeper-cdn.example/d/xyz/tool-2.1.tar.gz"})

    c = FakeClient({("GET", FK): Resp(302, headers={"Location": "/download"}),
                    ("GET", FK_DL): Resp(text=FK_PAGE), ("POST", FK_DL): post})
    res = FileKeeperPlugin().resolve(FK, Ctx(c))
    assert res.url == "https://fs7.filekeeper-cdn.example/d/xyz/tool-2.1.tar.gz"
    assert (res.filename, res.size) == ("tool-2.1.tar.gz", 52428800)
    assert slept == [11]
    assert all(kw["follow"] is False for _, _, kw in c.calls)   # cookies kept per hop


def test_filekeeper_classic_form_with_recaptcha(slept):
    def post(kw):
        assert kw["data"]["g-recaptcha-response"] == "ANSWER" and kw["data"]["rand"] == "r9"
        return Resp(302, headers={"Location": "https://fs1.example-files.net/d/abc/file.bin"})

    c = FakeClient({("GET", FK): Resp(text=FK_CAPTCHA_PAGE), ("POST", FK): post})
    ctx = Ctx(c)
    res = FileKeeperPlugin().resolve(FK, ctx)
    assert res.url == "https://fs1.example-files.net/d/abc/file.bin"
    assert ctx.captchas[0]["kind"] == "recaptcha" and ctx.captchas[0]["sitekey"] == "6LcFKsitekey"
    assert slept == [6]


def test_filekeeper_offline_wait_and_premium_only(slept):
    c = FakeClient({("GET", FK): Resp(302, headers={"Location": "/download"}),
                    ("GET", FK_DL): Resp(404, FK_404)})
    with pytest.raises(PluginError, match="offline"):
        FileKeeperPlugin().grab(FK, Ctx(c))
    wait = "<div class='err'>You have to wait 1 hours, 12 minutes, 30 seconds till next download</div>"
    c = FakeClient({("GET", FK): Resp(text=wait)})
    with pytest.raises(PluginError, match=r"wait 73 minutes"):
        FileKeeperPlugin().resolve(FK, Ctx(c))
    c = FakeClient({("GET", FK): Resp(text="<p>This file is available for Premium Users only.</p>")})
    with pytest.raises(PaywallRequired):
        FileKeeperPlugin().resolve(FK, Ctx(c))
    c = FakeClient({("GET", FK): Resp(text=FK_PAGE.replace('data-countdown="10"', 'data-countdown="300"'))})
    with pytest.raises(PluginError, match=r"wait 5 minutes"):
        FileKeeperPlugin().resolve(FK, Ctx(c))
    assert slept == []


def test_filekeeper_premium_login_then_direct_redirect(slept):
    acc = Account(site="filekeeper.net", username="me", password="pw", premium=True)
    login_page = """<form method="POST" action="https://filekeeper.net/" name="FL">
      <input type="hidden" name="op" value="login"><input type="hidden" name="token" value="tok42">
      <input type="hidden" name="rand" value=""><input type="hidden" name="redirect" value="">
      <input name="login"><input name="password" type="password"></form>"""

    def login(kw):
        d = kw["data"]
        assert (d["op"], d["token"], d["login"], d["password"]) == ("login", "tok42", "me", "pw")
        return Resp(302, headers={"Location": "/?op=my_files"})

    c = FakeClient({("GET", "https://filekeeper.net/login"): Resp(text=login_page),
                    ("POST", "https://filekeeper.net/"): login,
                    ("GET", "https://filekeeper.net/?op=my_files"): Resp(text="<a href='/logout'>Logout</a>"),
                    ("GET", FK): Resp(302, headers={"Location": "https://fs2.example-files.net/d/p/file.bin"})})
    res = FileKeeperPlugin().resolve(FK, Ctx(c, acc))
    assert res.url == "https://fs2.example-files.net/d/p/file.bin" and slept == []


def test_filekeeper_login_rejected():
    acc = Account(site="filekeeper.net", username="me", password="bad", premium=True)
    form = '<form name="FL"><input type="hidden" name="token" value="t"></form>'
    c = FakeClient({("GET", "https://filekeeper.net/login"): Resp(text=form),
                    ("POST", "https://filekeeper.net/"): Resp(text="<b>Incorrect Login or Password</b>" + form)})
    with pytest.raises(LoginRequired):
        FileKeeperPlugin().resolve(FK, Ctx(c, acc))


def test_registry_discovers_the_four_plugins():
    from opengrab.plugins.registry import PluginRegistry
    reg = PluginRegistry()
    reg.scan()
    for url, pid in [(F1, "1fichier"), ("https://turbobit.net/abcdef123456.html", "turbobit"),
                     ("https://hitfile.net/AbC12dE", "hitfile"), (FK, "filekeeper")]:
        assert reg.for_url(url)[0].id == pid
    assert not any(k.endswith(("_fdplatform", "onefichier", "turbobit", "hitfile", "filekeeper"))
                   for k in reg.errors)
