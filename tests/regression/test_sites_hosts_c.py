"""Site plugins: Rapidgator, chomikuj.pl, MixDrop.

Offline: a fake client answers from small hand-written fixtures shaped like
the real API answers and pages (Rapidgator API v2 documentation, chomikuj.pl
download dialogs and MixDrop embed pages seen on 2026-10-30).
"""
import json
import re

import pytest

from opengrab.plugins.base import (CaptchaRequired, LoginRequired, PaywallRequired,
                                   PluginContext, PluginError)
from opengrab.plugins.builtin import chomikuj, mixdrop, rapidgator
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
    """routes: list of (method, url-substring, Resp or callable(url, kw))."""

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for m, needle, answer in self.routes:
            if m == method and needle in url:
                r = answer(url, kw) if callable(answer) else answer
                if not r.url:
                    r.url = url
                return r
        return Resp(404, "not routed", url=url)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def set_cookie(self, url, name, value):
        pass


class Acc:
    def __init__(self, username="me@example.invalid", password="pw", premium=True, data=None):
        self.id = "acc1"
        self.username, self.password, self.premium, self.data = username, password, premium, data or {}


class Accounts:
    def __init__(self, acc):
        self.find = lambda site: acc
        self.for_domain = lambda host: acc


def ctx_for(routes, code="", account=None):
    c = PluginContext(Client(routes), settings=None,
                      accounts=Accounts(account) if account else None)
    c.manual_code = code       # stands in for a solved captcha
    return c


def posted(ctx, needle):
    return [kw.get("data") or {} for m, u, kw in ctx.client.calls if m == "POST" and needle in u]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(rapidgator, "_sleep", slept.append)
    return slept


# ---------------------------------------------------------------------------
# registry: each site's links go to its plugin first
# ---------------------------------------------------------------------------
def test_registry_routes_hosts_to_site_plugins():
    reg = PluginRegistry()
    reg.scan(force=True)
    for pid in ("rapidgator", "chomikuj", "mixdrop"):
        assert pid in reg.plugins, (pid, reg.errors)
    cases = {
        "https://rapidgator.net/file/0123456789abcdef0123456789abcdef/test.zip.html": "rapidgator",
        "https://rg.to/file/0123456789abcdef0123456789abcdef/test.zip": "rapidgator",
        "https://www.rapidgator.net/file/0123456789abcdef0123456789abcdef": "rapidgator",
        "https://chomikuj.pl/someone/Programy/putty,3459807392.exe(executable)": "chomikuj",
        "https://chomikuj.pl/someone/Folder+A/backup,123456789.zip(archive)": "chomikuj",
        "https://chomikuj.pl/someone/Folder/backup,123456789.zip": "chomikuj",
        "https://mixdrop.ag/f/abc123def45": "mixdrop",
        "https://mixdrop.co/e/abc123def45": "mixdrop",
        "https://mxdrop.click/f/abc123def45/clip.mp4": "mixdrop",
        "https://mixdrop.sx/e/abc123def45": "mixdrop",
    }
    for url, pid in cases.items():
        assert reg.for_url(url)[0].id == pid, (url, [p.id for p in reg.for_url(url)])


# ---------------------------------------------------------------------------
# Rapidgator
# ---------------------------------------------------------------------------
RG_ID = "267796c7b59efd11126606dbde8dd1e4"
RG_URL = f"https://rapidgator.net/file/{RG_ID}/test-setup.exe.html"
RG_LOGIN = {"response": {"token": "tok-1", "user": {"email": "me@example.invalid", "is_premium": True}},
            "status": 200, "details": None}
RG_FREE_LOGIN = {"response": {"token": "tok-f", "user": {"is_premium": False}}, "status": 200, "details": None}
RG_INFO = {"response": {"file": {"file_id": RG_ID, "name": "test-setup.exe", "size": 597304}},
           "status": 200, "details": None}
RG_DL = {"response": {"download_url": "https://pr3.rapidgator.net//?r=download/index&session_id=abc",
                      "delay": 0}, "status": 200, "details": None}
RG_GONE = {"response": None, "status": 404, "details": "Error: File not found"}
RG_SESSION = {"response": None, "status": 401, "details": "Error. Session doesn't exist"}
RG_PAGE = """<html><head><title>Download file test-setup.exe</title></head><body>
<div class="file-descr"><p>File size: <strong>583.31 KB</strong></p></div>
<script>var fid = 98765; var secs = 30; var startTimerUrl = '/download/AjaxStartTimer';</script>
</body></html>"""
RG_CAPTCHA = """<html><form id="captchaform" action="/download/captcha" method="post">
<input type="hidden" name="DownloadCaptchaForm[captcha]" value="">
<div class="g-recaptcha" data-sitekey="6Lsitekey-rapidgator-0000000000"></div>
<input type="submit" value="Download"></form></html>"""
RG_DONE = """<html><script>var download_link = 'https://pr12.rapidgator.net/download/HASH/test-setup.exe';
</script></html>"""


def test_rapidgator_parse_url():
    assert rapidgator.parse_url(RG_URL) == (RG_ID, "test-setup.exe")
    assert rapidgator.parse_url(f"https://rg.to/file/{RG_ID}") == (RG_ID, "")
    assert rapidgator.parse_url("https://rapidgator.net/file/12345678/x.rar.html") == ("12345678", "x.rar")
    for bad in ("https://rapidgator.net/article/premium", "https://rapidgator.net/folder/123/x.html",
                "https://example.com/file/" + RG_ID):
        assert rapidgator.parse_url(bad) is None


def test_rapidgator_premium_api_and_token_cache():
    p = rapidgator.RapidgatorPlugin()
    ctx = ctx_for([("POST", "/api/v2/user/login", Resp(body=RG_LOGIN)),
                   ("POST", "/api/v2/file/info", Resp(body=RG_INFO)),
                   ("POST", "/api/v2/file/download", Resp(body=RG_DL))], account=Acc())
    res = p.resolve(RG_URL, ctx)
    assert res.url.startswith("https://pr3.rapidgator.net/")
    assert (res.filename, res.size) == ("test-setup.exe", 597304)
    login = posted(ctx, "user/login")
    assert login == [{"login": "me@example.invalid", "password": "pw"}]   # password in the body, not the URL
    assert all("pw" not in u for _, u, _ in ctx.client.calls)
    assert posted(ctx, "file/download")[0] == {"file_id": RG_ID, "token": "tok-1"}
    p.resolve(RG_URL, ctx)
    assert len(posted(ctx, "user/login")) == 1                             # token cached


def test_rapidgator_session_expired_logs_in_again():
    p = rapidgator.RapidgatorPlugin()
    answers = iter([RG_SESSION, RG_DL])
    ctx = ctx_for([("POST", "user/login", Resp(body=RG_LOGIN)),
                   ("POST", "file/info", Resp(body=RG_INFO)),
                   ("POST", "file/download", lambda u, kw: Resp(body=next(answers)))], account=Acc())
    p._tokens["acc1"] = ("old", 9e12, True)
    assert p.resolve(RG_URL, ctx).url.startswith("https://pr3.")
    assert len(posted(ctx, "user/login")) == 1


def test_rapidgator_api_errors():
    p = rapidgator.RapidgatorPlugin()
    bad = {"response": None, "status": 401, "details": "Error: Wrong e-mail or password."}
    with pytest.raises(LoginRequired) as e:
        p.resolve(RG_URL, ctx_for([("POST", "user/login", Resp(body=bad))], account=Acc()))
    assert "Wrong e-mail" in e.value.hint
    tfa = {"response": None, "status": 401, "details": "Error: Invalid auth code"}
    with pytest.raises(LoginRequired) as e:
        p.resolve(RG_URL, ctx_for([("POST", "user/login", Resp(body=tfa))], account=Acc()))
    assert "two-factor" in e.value.hint
    with pytest.raises(PluginError, match="offline"):
        p.resolve(RG_URL, ctx_for([("POST", "user/login", Resp(body=RG_LOGIN)),
                                   ("POST", "file/", Resp(body=RG_GONE))], account=Acc()))


def test_rapidgator_free_flow(no_sleep):
    p = rapidgator.RapidgatorPlugin()
    ctx = ctx_for([("GET", "/file/" + RG_ID, Resp(text=RG_PAGE)),
                   ("GET", "AjaxStartTimer?fid=98765", Resp(body={"state": "started", "sid": "s1d"})),
                   ("GET", "AjaxGetDownloadLink?sid=s1d", Resp(body={"state": "done"})),
                   ("GET", "/download/captcha", Resp(text=RG_CAPTCHA)),
                   ("POST", "/download/captcha", Resp(text=RG_DONE))], code="solved-token")
    res = p.resolve(RG_URL, ctx)
    assert res.url == "https://pr12.rapidgator.net/download/HASH/test-setup.exe"
    assert res.filename == "test-setup.exe" and res.size == int(583.31 * 1024)
    assert no_sleep == [31]
    form = posted(ctx, "/download/captcha")[0]
    assert form["g-recaptcha-response"] == "solved-token"
    assert "DownloadCaptchaForm[captcha]" in form


def test_rapidgator_free_asks_solver():
    p = rapidgator.RapidgatorPlugin()
    ctx = ctx_for([("GET", "/file/", Resp(text=RG_PAGE)),
                   ("GET", "AjaxStartTimer", Resp(body={"state": "started", "sid": "s"})),
                   ("GET", "AjaxGetDownloadLink", Resp(body={"state": "done"})),
                   ("GET", "/download/captcha", Resp(text=RG_CAPTCHA))])
    with pytest.raises(CaptchaRequired) as e:
        p.resolve(RG_URL, ctx)
    assert (e.value.kind, e.value.sitekey) == ("recaptcha", "6Lsitekey-rapidgator-0000000000")


def test_rapidgator_free_account_uses_free_flow():
    p = rapidgator.RapidgatorPlugin()
    ctx = ctx_for([("POST", "user/login", Resp(body=RG_FREE_LOGIN)),
                   ("GET", "/file/", Resp(text=RG_PAGE)),
                   ("GET", "AjaxStartTimer", Resp(body={"state": "started", "sid": "s"})),
                   ("GET", "AjaxGetDownloadLink", Resp(body={"state": "done"})),
                   ("GET", "/download/captcha", Resp(text=RG_CAPTCHA)),
                   ("POST", "/download/captcha", Resp(text=RG_DONE))], code="x", account=Acc(premium=False))
    assert p.resolve(RG_URL, ctx).url.endswith("test-setup.exe")
    assert not posted(ctx, "file/download")


@pytest.mark.parametrize("page,exc,msg", [
    ("<p>Delay between downloads must be not less than 120 min.</p>", PluginError, "wait 120 minutes"),
    ("<p>You have reached your daily downloads limit.</p>", PluginError, "wait 60 minutes"),
    ("<p>You can`t download not more than 1 file at a time in free mode.</p>", PluginError, "wait 5 minutes"),
    ("<p>You can download files up to 500 MB in free mode.</p>", PaywallRequired, None),
    ("<p>This file can be downloaded by premium only</p>", PaywallRequired, None),
])
def test_rapidgator_free_limits(page, exc, msg):
    p = rapidgator.RapidgatorPlugin()
    with pytest.raises(exc, match=msg):
        p.resolve(RG_URL, ctx_for([("GET", "/file/", Resp(text=page))]))


def test_rapidgator_offline_and_long_wait():
    p = rapidgator.RapidgatorPlugin()
    gone = Resp(text="<h1>404 File not found</h1><p>Get the advantages of premium-account now!</p>",
                url="https://rapidgator.net/article/premium")
    with pytest.raises(PluginError, match="offline"):
        p.resolve(RG_URL, ctx_for([("GET", "/file/", gone)]))
    with pytest.raises(PluginError, match="offline"):
        p.grab(RG_URL, ctx_for([("GET", "/file/", gone)]))
    long_wait = RG_PAGE.replace("secs = 30", "secs = 600")
    with pytest.raises(PluginError, match="wait 10 minutes"):
        p.resolve(RG_URL, ctx_for([("GET", "/file/", Resp(text=long_wait))]))
    refused = {"state": "error", "code": "File not found or there`s a system error."}
    with pytest.raises(PluginError, match="refused"):
        p.resolve(RG_URL, ctx_for([("GET", "/file/", Resp(text=RG_PAGE)),
                                   ("GET", "AjaxStartTimer", Resp(body=refused))]))


def test_rapidgator_wrong_captcha():
    p = rapidgator.RapidgatorPlugin()
    ctx = ctx_for([("GET", "/file/", Resp(text=RG_PAGE)),
                   ("GET", "AjaxStartTimer", Resp(body={"state": "started", "sid": "s"})),
                   ("GET", "AjaxGetDownloadLink", Resp(body={"state": "done"})),
                   ("GET", "/download/captcha", Resp(text=RG_CAPTCHA)),
                   ("POST", "/download/captcha", Resp(text="<p>The verification code is incorrect.</p>"))],
                  code="bad")
    with pytest.raises(PluginError, match="rejected the captcha"):
        p.resolve(RG_URL, ctx)


# ---------------------------------------------------------------------------
# chomikuj.pl
# ---------------------------------------------------------------------------
CH_URL = "https://chomikuj.pl/someone/Putty/putty,3459807392.exe(executable)"
CH_PAGE = """<html><head><title>putty.exe - Putty - someone - Chomikuj.pl</title></head><body>
<form><input name="__RequestVerificationToken" type="hidden" value="TOKEN+/=" /></form>
<h1 class="filename exe"><a href="/someone/Putty/putty,3459807392.exe(executable)" >putty.exe</a></h1>
<p class="fileSize">444 KB</p></body></html>"""
CH_CAPTCHA = {"Type": "Window", "IsSuccess": True, "Data": None, "Content": """
<div class="windowFileWithCaptcha"><form action="/action/License/DownloadNotLoggedCaptchaEntered"
 id="notLoggedDownloadCaptchaForm" method="post"><input id="FileId" name="FileId" type="hidden" value="3459807392" />
<input id="SerializedUserSelection" name="SerializedUserSelection" type="hidden" value="AAEAAA==" />
<input id="FileName" name="FileName" type="hidden" value="putty.exe" />
<div id="abc" class="g-recaptcha"></div><script>grecaptcha.render("abc", { 'sitekey': '6LchomikSitekey-000000000000', 'size': 'normal' });</script>
<input type="submit" value="Pobierz" class="greenButtonCSS"/></form>
<div class="or">lub</div><form action="/action/Login/LoginWindow" id="downloadLoginForm" method="post">
<input type="hidden" name="FileId" value="3459807392" /></form></div>"""}
CH_WARNING = {"Type": "Window", "IsSuccess": True, "Content": """
<form action="/action/License/DownloadWarningAccept" id="downloadWarningForm" method="post">
<input id="FileId" name="FileId" type="hidden" value="3459807392" />
<input id="SerializedOrgFile" name="SerializedOrgFile" type="hidden" value="BBB=" />
<button type="submit">Pobierz</button></form>"""}
CH_BIG = {"Type": "Window", "IsSuccess": True, "Content": """
<p>Próbujesz pobrać plik o rozmiarze 37,1 MB. Każdy plik powyżej 1 MB wymaga opłacenia kosztów trasferu.</p>
<div id="BuyAdditionalTransfer"><form action="/action/License/BuyFileBySmsByDCB" id="buyFileBySMSByDCBForm"
 method="post"><input type="hidden" name="fileId" value="1" /></form></div>"""}
CH_REDIRECT = {"Type": "Redirect", "IsSuccess": True,
               "redirectUrl": "https://s5.chomikuj.pl/File.aspx?e=abc&pv=2"}


def test_chomikuj_file_id():
    assert chomikuj.file_id(CH_URL) == "3459807392"
    assert chomikuj.file_id("https://chomikuj.pl/a/B+C/x+y,123.zip(archive)") == "123"
    assert chomikuj.file_id("https://www.chomikuj.pl/a/x,99.rar") == "99"
    assert chomikuj.file_id("https://chomikuj.pl/a/Folder") is None
    assert chomikuj.file_id("https://chomikuj.pl/x,99.rar") is None
    assert chomikuj.file_id("https://example.com/a/x,99.rar") is None


def test_chomikuj_visitor_small_file_with_captcha():
    p = chomikuj.ChomikujPlugin()
    ctx = ctx_for([("GET", CH_URL, Resp(text=CH_PAGE)),
                   ("POST", "DownloadNotLoggedCaptchaEntered", Resp(body=CH_REDIRECT)),
                   ("POST", "/action/License/Download", Resp(body=CH_CAPTCHA))], code="g-token")
    res = p.resolve(CH_URL, ctx)
    assert res.url == CH_REDIRECT["redirectUrl"]
    assert (res.filename, res.size) == ("putty.exe", 444 * 1024)
    first = posted(ctx, "/action/License/Download")[0]
    assert first == {"fileId": "3459807392", "__RequestVerificationToken": "TOKEN+/="}
    sent = posted(ctx, "DownloadNotLoggedCaptchaEntered")[0]
    assert sent["g-recaptcha-response"] == "g-token" and sent["FileName"] == "putty.exe"
    assert sent["__RequestVerificationToken"] == "TOKEN+/="


def test_chomikuj_visitor_captcha_goes_to_solver():
    p = chomikuj.ChomikujPlugin()
    ctx = ctx_for([("GET", CH_URL, Resp(text=CH_PAGE)),
                   ("POST", "/action/License/Download", Resp(body=CH_CAPTCHA))])
    with pytest.raises(CaptchaRequired) as e:
        p.resolve(CH_URL, ctx)
    assert (e.value.kind, e.value.sitekey, e.value.site_url) == ("recaptcha", "6LchomikSitekey-000000000000", CH_URL)


def test_chomikuj_wrong_captcha_and_big_file():
    p = chomikuj.ChomikujPlugin()
    with pytest.raises(PluginError, match="rejected the captcha"):
        p.resolve(CH_URL, ctx_for([("GET", CH_URL, Resp(text=CH_PAGE)),
                                   ("POST", "/action/License/Download", Resp(body=CH_CAPTCHA))], code="bad"))
    with pytest.raises(LoginRequired) as e:
        p.resolve(CH_URL, ctx_for([("GET", CH_URL, Resp(text=CH_PAGE)),
                                   ("POST", "/action/License/Download", Resp(body=CH_BIG))]))
    assert "over 1 MB" in e.value.hint


def test_chomikuj_account_confirms_and_downloads():
    p = chomikuj.ChomikujPlugin()
    home = '<form><input name="__RequestVerificationToken" type="hidden" value="HOME" /></form>'
    ctx = ctx_for([("GET", "https://chomikuj.pl/", lambda u, kw: Resp(text=home if u.endswith(".pl/") else CH_PAGE)),
                   ("POST", "TopBarLogin", Resp(body={"IsSuccess": True, "Data": {"LoggedIn": True}})),
                   ("POST", "DownloadWarningAccept", Resp(body=CH_REDIRECT)),
                   ("POST", "/action/License/Download", Resp(body=CH_WARNING))],
                  account=Acc(username="mychomik"))
    res = p.resolve(CH_URL, ctx)
    assert res.url == CH_REDIRECT["redirectUrl"]
    login = posted(ctx, "TopBarLogin")[0]
    assert login["Login"] == "mychomik" and login["__RequestVerificationToken"] == "HOME"
    assert posted(ctx, "DownloadWarningAccept")[0]["SerializedOrgFile"] == "BBB="


def test_chomikuj_account_errors():
    p = chomikuj.ChomikujPlugin()
    bad = {"IsSuccess": False, "Message": "Niepoprawne dane logowania", "Data": None, "Type": 3}
    with pytest.raises(LoginRequired) as e:
        p.resolve(CH_URL, ctx_for([("GET", "chomikuj.pl/", Resp(text=CH_PAGE)),
                                   ("POST", "TopBarLogin", Resp(body=bad))], account=Acc()))
    assert "Niepoprawne" in e.value.hint
    p = chomikuj.ChomikujPlugin()
    with pytest.raises(PaywallRequired):
        p.resolve(CH_URL, ctx_for([("GET", "chomikuj.pl/", Resp(text=CH_PAGE)),
                                   ("POST", "TopBarLogin", Resp(body={"IsSuccess": True, "Data": {"LoggedIn": True}})),
                                   ("POST", "/action/License/Download", Resp(body=CH_BIG))], account=Acc()))


def test_chomikuj_offline_and_grab():
    p = chomikuj.ChomikujPlugin()
    with pytest.raises(PluginError, match="offline"):
        p.resolve(CH_URL, ctx_for([("GET", CH_URL, Resp(404, "<title>Nie znaleziono - błąd 404</title>"))]))
    g = p.grab(CH_URL, ctx_for([("GET", CH_URL, Resp(text=CH_PAGE))]))
    assert (g.filename, g.size, g.needs_captcha, g.needs_login) == ("putty.exe", 444 * 1024, True, False)
    big = CH_PAGE.replace("444 KB", "21,57 MB")
    assert p.grab(CH_URL, ctx_for([("GET", CH_URL, Resp(text=big))])).needs_login


# ---------------------------------------------------------------------------
# MixDrop
# ---------------------------------------------------------------------------
_DIG = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _enc(n, base):
    s = ""
    while True:
        s = _DIG[n % base] + s
        n //= base
        if not n:
            return s


def pack(source, base=62):
    """A small p.a.c.k.e.r encoder, enough to make test fixtures."""
    words = []
    for w in re.findall(r"\b\w+\b", source):
        if w not in words:
            words.append(w)
    payload = re.sub(r"\b\w+\b", lambda m: _enc(words.index(m.group(0)), base), source)
    payload = payload.replace("\\", "\\\\").replace("'", "\\'")
    return ("eval(function(p,a,c,k,e,d){e=function(c){return c};if(!''.replace(/^/,String)){while(c--)"
            "{d[c]=k[c]||c}k=[function(e){return d[e]}];e=function(){return'\\\\w+'};c=1};while(c--)"
            "{if(k[c]){p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c])}}return p}"
            f"('{payload}',{base},{len(words)},'{'|'.join(words)}'.split('|'),0,{{}}))")


MD_SCRIPT = ("MDCore.ref=\"abc123def45\";MDCore.poster=\"//s-delivery9.mxcontent.net/thumbs/abc.jpg\";"
             "MDCore.wurl=\"//s-delivery9.mxcontent.net/v/0a1b2c3d4e5f.mp4?s=SIG_xyz&e=1790000000&_t=1789\";"
             + "".join(f"var w{i}=x{i};" for i in range(80)))      # > 62 words -> two-digit tokens
MD_EMBED = f"""<html><head><title>MixDrop - Watch clip.mp4</title></head><body>
<script>MDCore.ref = "abc123def45";</script><script>{pack(MD_SCRIPT)}</script></body></html>"""
MD_FILE = """<html><head><title>MixDrop - Watch clip.mp4</title></head><body>
<div class="title"><b>clip.mp4</b></div><span>12.5 MB</span>
<iframe width="100%" src="//mixdrop.ag/e/abc123def45" allowfullscreen></iframe></body></html>"""
MD_GONE = "<html><title>mixdrop.co</title><h2>WE ARE SORRY</h2><p>We can't find the video you are looking for.</p>"


def test_unpacker_known_sample_and_roundtrip():
    sample = ("eval(function(p,a,c,k,e,r){e=String;if(!''.replace(/^/,String)){while(c--)r[c]=k[c]||c;"
              "k=[function(e){return r[e]}];e=function(){return'\\\\w+'};c=1};while(c--)if(k[c])p=p.replace("
              "new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c]);return p}('0 2=1',3,3,'var||a'.split('|'),0,{}))")
    assert mixdrop.unpack(sample) == "var a=1"
    assert mixdrop.unpack(pack(MD_SCRIPT)) == MD_SCRIPT
    assert mixdrop.unpack(pack("var it='quoted';", base=36)) == "var it='quoted';"
    assert mixdrop.unpack("<p>no packed script</p>") == ""


def test_mixdrop_parse_url():
    assert mixdrop.parse_url("https://mixdrop.ag/f/abc123def45") == ("https://mixdrop.ag", "f", "abc123def45")
    assert mixdrop.parse_url("https://www.mixdrop.co/e/abc123def45/clip.mp4") == ("https://www.mixdrop.co", "e",
                                                                                   "abc123def45")
    for host in ("mixdrop.top", "mixdrop.to", "mixdrop.ps", "mixdrop.si", "mixdrop.sx", "mixdrop.bz",
                 "mxdrop.click", "m1xdrop.com"):
        assert mixdrop.parse_url(f"https://{host}/e/abc123def45"), host
    assert mixdrop.parse_url("https://mixdrop.ag/") is None
    assert mixdrop.parse_url("https://notmixdrop.example/f/abc123def45") is None


def test_mixdrop_file_page_to_media_url():
    p = mixdrop.MixDropPlugin()
    ctx = ctx_for([("GET", "/f/abc123def45", Resp(text=MD_FILE)),
                   ("GET", "/e/abc123def45", Resp(text=MD_EMBED))])
    res = p.resolve("https://mixdrop.ag/f/abc123def45", ctx)
    assert res.url == "https://s-delivery9.mxcontent.net/v/0a1b2c3d4e5f.mp4?s=SIG_xyz&e=1790000000&_t=1789"
    assert res.filename == "clip.mp4" and res.size == int(12.5 * 1024 ** 2)
    assert res.headers["referer"] == "https://mixdrop.ag/e/abc123def45"


def test_mixdrop_embed_link_and_errors():
    p = mixdrop.MixDropPlugin()
    res = p.resolve("https://mxdrop.click/e/abc123def45", ctx_for([("GET", "/e/abc123def45", Resp(text=MD_EMBED))]))
    assert res.url.startswith("https://s-delivery9.mxcontent.net/v/") and res.filename == "clip.mp4"
    with pytest.raises(PluginError, match="offline"):
        p.resolve("https://mixdrop.co/f/abc123def45", ctx_for([("GET", "/f/", Resp(text=MD_GONE))]))
    with pytest.raises(PluginError, match="offline"):
        p.grab("https://mixdrop.co/e/abc123def45", ctx_for([("GET", "/e/", Resp(text=MD_GONE))]))
    with pytest.raises(PluginError, match="player script not found"):
        p.resolve("https://mixdrop.co/e/abc123def45", ctx_for([("GET", "/e/", Resp(text="<html>processing</html>"))]))
