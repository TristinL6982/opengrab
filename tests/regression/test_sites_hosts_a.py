"""Site plugins: MediaFire, FuckingFast, DataNodes, VikingFile, Rootz, FileDitch.

Offline: a fake client answers from small hand-written fixtures shaped like
the real pages and API answers (seen on 2026-10-31).
"""
import json

import pytest

from opengrab.plugins.base import CaptchaRequired, PluginContext, PluginError
from opengrab.plugins.builtin import datanodes, fileditch, fuckingfast, mediafire, rootz, vikingfile
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


class Acc:
    def __init__(self, password="", data=None):
        self.username, self.password, self.data = "me", password, data or {}


class Accounts:
    def __init__(self, acc):
        self.acc = acc
        self.find = lambda site: acc
        self.for_domain = lambda host: acc


def ctx_for(routes, code="", account=None):
    c = PluginContext(Client(routes), settings=None,
                      accounts=Accounts(account) if account else None)
    c.manual_code = code       # stands in for a solved captcha
    return c


# ---------------------------------------------------------------------------
# registry: the six plugins load and claim their hosts
# ---------------------------------------------------------------------------
def test_registry_picks_the_site_plugins():
    reg = PluginRegistry()
    reg.scan(force=True)
    for pid in ("mediafire", "fuckingfast", "datanodes", "vikingfile", "rootz", "fileditch"):
        assert pid in reg.plugins, (pid, reg.errors)
    cases = {
        "https://www.mediafire.com/file/abc123def45/x.zip/file": "mediafire",
        "https://fuckingfast.co/w7sfvwatp70x#a.rar": "fuckingfast",
        "https://datanodes.to/3i5s52vxv4ts/a.zip": "datanodes",
        "https://vikingfile.com/f/TPRSfLvcIu": "vikingfile",
        "https://www.rootz.so/d/BHXge": "rootz",
        "https://fileditchfiles.st/charlie21/aa59bb5b707fb30f002e/cat.mp4": "fileditch",
    }
    for url, pid in cases.items():
        assert reg.for_url(url)[0].id == pid, url


# ---------------------------------------------------------------------------
# MediaFire
# ---------------------------------------------------------------------------
MF_KEY = "wdx5lu4c37sm1cv"
MF_INFO = {"response": {"result": "Success", "file_info": {
    "quickkey": MF_KEY, "filename": "tool-1.2.exe", "size": "641489",
    "privacy": "public", "password_protected": "no"}}}
MF_GONE = {"response": {"result": "Error", "error": 110, "message": "Unknown or Invalid QuickKey"}}
MF_DIRECT = f"https://download2276.mediafire.com/tok3n/{MF_KEY}/tool-1.2.exe"
MF_PAGE = f"""<html><div class="download_link" id="download_link">
<a class="preparing" href="#"><span>Preparing Download</span></a>
<a class="input popsok" aria-label="Download file" href="{MF_DIRECT}" id="downloadButton">
Download (626.45KB)</a></div></html>"""


def test_mediafire_supports():
    p = mediafire.MediaFirePlugin()
    for u in (f"https://www.mediafire.com/file/{MF_KEY}/tool-1.2.exe/file",
              f"https://mediafire.com/file/{MF_KEY}",
              f"http://www.mediafire.com/download/{MF_KEY}",
              f"https://www.mediafire.com/view/{MF_KEY}/tool.exe",
              f"https://www.mediafire.com/?{MF_KEY}",
              "https://www.mediafire.com/folder/azvdrnqccpybs/test01/",
              "https://app.mediafire.com/azvdrnqccpybs",
              "https://www.mediafire.com/#azvdrnqccpybs"):
        assert p.supports(u), u
    for u in ("https://www.mediafire.com/", "https://www.mediafire.com/upgrade/",
              f"https://download2276.mediafire.com/x/{MF_KEY}/a.exe",
              f"https://evil.example/file/{MF_KEY}"):
        assert not p.supports(u), u


def test_mediafire_file_resolves_to_direct_link():
    ctx = ctx_for([("GET", "file/get_info.php", Resp(body=MF_INFO)),
                   ("GET", f"/file/{MF_KEY}/", Resp(text=MF_PAGE))])
    url = f"https://www.mediafire.com/file/{MF_KEY}/tool-1.2.exe/file"
    g = mediafire.MediaFirePlugin().grab(url, ctx)
    assert (g.filename, g.size, g.direct_url) == ("tool-1.2.exe", 641489, None)
    r = mediafire.MediaFirePlugin().resolve(url, ctx)
    assert r.url == MF_DIRECT
    assert r.filename == "tool-1.2.exe" and r.size == 641489
    assert r.headers["referer"].startswith(f"https://www.mediafire.com/file/{MF_KEY}")


def test_mediafire_scrambled_url():
    import base64
    page = f'<a class="input popsok" href="#" data-scrambled-url="{base64.b64encode(MF_DIRECT.encode()).decode()}">x</a>'
    assert mediafire.find_download_link(page) == MF_DIRECT


def test_mediafire_offline_and_password():
    gone = ctx_for([("GET", "file/get_info.php", Resp(404, body=MF_GONE))])
    with pytest.raises(PluginError, match="offline"):
        mediafire.MediaFirePlugin().resolve(f"https://www.mediafire.com/file/{MF_KEY}", gone)
    locked = json.loads(json.dumps(MF_INFO))
    locked["response"]["file_info"]["password_protected"] = "yes"
    ctx = ctx_for([("GET", "file/get_info.php", Resp(body=locked))])
    with pytest.raises(PluginError, match="password"):
        mediafire.MediaFirePlugin().resolve(f"https://www.mediafire.com/file/{MF_KEY}", ctx)


def test_mediafire_page_redirected_to_error_is_offline():
    ctx = ctx_for([("GET", "file/get_info.php", Resp(body=MF_INFO)),
                   ("GET", f"/file/{MF_KEY}/",
                    Resp(text="<html>gone</html>",
                         url="https://www.mediafire.com/error.php?errno=320"))])
    with pytest.raises(PluginError, match="offline"):
        mediafire.MediaFirePlugin().resolve(f"https://www.mediafire.com/file/{MF_KEY}", ctx)


def test_mediafire_captcha_page_goes_to_solver():
    page = ('<form action="/file/abc/x/file" method="POST">'
            '<div class="g-recaptcha" data-sitekey="SITEKEY1"></div></form>')
    routes = [("GET", "file/get_info.php", Resp(body=MF_INFO)),
              ("GET", f"/file/{MF_KEY}/", Resp(text=page))]
    with pytest.raises(CaptchaRequired) as e:       # no solver chain: the user is asked
        mediafire.MediaFirePlugin().resolve(f"https://www.mediafire.com/file/{MF_KEY}", ctx_for(routes))
    assert e.value.kind == "recaptcha"
    ctx = ctx_for(routes + [("POST", "/file/abc/x/file", Resp(text=MF_PAGE))], code="ANSWER")
    r = mediafire.MediaFirePlugin().resolve(f"https://www.mediafire.com/file/{MF_KEY}", ctx)
    assert r.url == MF_DIRECT
    post = [c for c in ctx.client.calls if c[0] == "POST"][0]
    assert post[2]["data"] == {"g-recaptcha-response": "ANSWER"}


def _folder_routes():
    def content(url, kw):
        if "folder_key=ROOTKEY111" in url and "content_type=files" in url:
            if "chunk=1" in url:
                return Resp(body={"response": {"result": "Success", "folder_content": {
                    "files": [{"quickkey": "aaaaaaaaaaaaaaa", "filename": "a b.zip"}],
                    "more_chunks": "yes"}}})
            return Resp(body={"response": {"result": "Success", "folder_content": {
                "files": [{"quickkey": "bbbbbbbbbbbbbbb", "filename": "b.zip"}],
                "more_chunks": "no"}}})
        if "folder_key=ROOTKEY111" in url:
            return Resp(body={"response": {"result": "Success", "folder_content": {
                "folders": [{"folderkey": "SUBKEY22222", "name": "sub"}], "more_chunks": "no"}}})
        if "content_type=files" in url:
            return Resp(body={"response": {"result": "Success", "folder_content": {
                "files": [{"quickkey": "ccccccccccccccc", "filename": "c.txt"}], "more_chunks": "no"}}})
        return Resp(body={"response": {"result": "Success", "folder_content": {
            "folders": [], "more_chunks": "no"}}})
    return [("GET", "folder/get_info.php",
             Resp(body={"response": {"result": "Success", "folder_info": {"name": "pack"}}})),
            ("GET", "folder/get_content.php", content)]


def test_mediafire_folder_lists_children_recursively():
    ctx = ctx_for(_folder_routes())
    url = "https://www.mediafire.com/folder/ROOTKEY111/pack"
    g = mediafire.MediaFirePlugin().grab(url, ctx)
    assert g.title == "pack"
    assert g.children == [
        ["https://www.mediafire.com/file/aaaaaaaaaaaaaaa/a%20b.zip/file", "a b.zip"],
        ["https://www.mediafire.com/file/bbbbbbbbbbbbbbb/b.zip/file", "b.zip"],
        ["https://www.mediafire.com/file/ccccccccccccccc/c.txt/file", "sub/c.txt"],
    ]
    r = mediafire.MediaFirePlugin().resolve(url, ctx)
    assert r.url == "" and len(r.children) == 3


def test_mediafire_legacy_query_key_falls_back_to_folder():
    routes = [("GET", "file/get_info.php", Resp(404, body=MF_GONE))] + _folder_routes()
    g = mediafire.MediaFirePlugin().grab("https://www.mediafire.com/?ROOTKEY111", ctx_for(routes))
    assert len(g.children) == 3


def test_mediafire_private_folder():
    ctx = ctx_for([("GET", "folder/get_info.php",
                    Resp(body={"response": {"result": "Error", "message": "Unknown folder"}}))])
    with pytest.raises(PluginError, match="offline or private"):
        mediafire.MediaFirePlugin().resolve("https://www.mediafire.com/folder/ROOTKEY111", ctx)


# ---------------------------------------------------------------------------
# FuckingFast
# ---------------------------------------------------------------------------
FF_ID = "w7sfvwatp70x"
FF_DL = "https://dl.fuckingfast.co/dl/Zx9_abc-DEF"
FF_PAGE = f"""<html><head><title>part01.rar</title></head><body x-data="App()">
<p>Size: 1.5 GB</p>
<button class="link-button" hx-post="/f/{FF_ID}/go" hx-trigger="click">DOWNLOAD</button>
<div id="cf-turnstile" data-sitekey="0xFFKEY"></div></body></html>"""


def test_fuckingfast_supports():
    p = fuckingfast.FuckingFastPlugin()
    assert p.supports(f"https://fuckingfast.co/{FF_ID}#Game.part01.rar")
    assert p.supports(f"https://fuckingfast.net/{FF_ID}")
    assert p.supports(FF_DL)
    assert not p.supports("https://fuckingfast.co/help")
    assert not p.supports("https://fuckingfast.co/")
    assert not p.supports(f"https://notfuckingfast.co/{FF_ID}")


def test_fuckingfast_go_returns_hx_redirect():
    ctx = ctx_for([("GET", f"/{FF_ID}", Resp(text=FF_PAGE)),
                   ("POST", f"/f/{FF_ID}/go", Resp(200, "", headers={"HX-Redirect": FF_DL}))])
    url = f"https://fuckingfast.co/{FF_ID}#Game.part01.rar"
    assert fuckingfast.FuckingFastPlugin().grab(url, ctx).filename == "Game.part01.rar"
    r = fuckingfast.FuckingFastPlugin().resolve(url, ctx)
    assert r.url == FF_DL and r.filename == "Game.part01.rar"
    assert r.size == int(1.5 * 1024 ** 3)
    assert r.headers == {"referer": f"https://fuckingfast.co/{FF_ID}"}
    post = [c for c in ctx.client.calls if c[0] == "POST"][0]
    assert post[2]["headers"]["hx-request"] == "true"
    assert post[2]["headers"]["hx-current-url"] == url


def test_fuckingfast_inline_window_open_and_page_title():
    page = f'<title>tool.zip</title><script>function download(){{window.open("{FF_DL}")}}</script>'
    ctx = ctx_for([("GET", f"/{FF_ID}", Resp(text=page))])
    r = fuckingfast.FuckingFastPlugin().resolve(f"https://fuckingfast.co/{FF_ID}", ctx)
    assert r.url == FF_DL and r.filename == "tool.zip"


def test_fuckingfast_turnstile_goes_to_solver():
    def go(url, kw):
        if (kw.get("data") or {}).get("cf-turnstile-response") == "TOKEN":
            return Resp(200, "", headers={"HX-Redirect": FF_DL})
        return Resp(403, "captcha verification failed")
    routes = [("GET", f"/{FF_ID}", Resp(text=FF_PAGE)), ("POST", "/go", go)]
    with pytest.raises(CaptchaRequired) as e:
        fuckingfast.FuckingFastPlugin().resolve(f"https://fuckingfast.co/{FF_ID}", ctx_for(routes))
    assert e.value.kind == "turnstile"
    r = fuckingfast.FuckingFastPlugin().resolve(f"https://fuckingfast.co/{FF_ID}",
                                                ctx_for(routes, code="TOKEN"))
    assert r.url == FF_DL


def test_fuckingfast_offline_rate_limit_and_no_link():
    dead = '<html><title>Not Found</title><p>File Not Found Or Deleted</p></html>'
    with pytest.raises(PluginError, match="offline"):
        fuckingfast.FuckingFastPlugin().resolve(
            f"https://fuckingfast.co/{FF_ID}", ctx_for([("GET", f"/{FF_ID}", Resp(404, dead))]))
    with pytest.raises(PluginError, match="rate limit"):
        fuckingfast.FuckingFastPlugin().resolve(
            f"https://fuckingfast.co/{FF_ID}", ctx_for([("GET", f"/{FF_ID}", Resp(429, "slow down"))]))
    page = FF_PAGE.replace('data-sitekey="0xFFKEY"', "")
    ctx = ctx_for([("GET", f"/{FF_ID}", Resp(text=page)), ("POST", "/go", Resp(500, "oops"))])
    with pytest.raises(PluginError, match="browser download flow"):
        fuckingfast.FuckingFastPlugin().resolve(f"https://fuckingfast.co/{FF_ID}", ctx)


def test_fuckingfast_direct_link_passes_through():
    r = fuckingfast.FuckingFastPlugin().resolve(FF_DL + "#a.rar", ctx_for([]))
    assert r.url == FF_DL + "#a.rar" and r.filename == "a.rar"


# ---------------------------------------------------------------------------
# DataNodes
# ---------------------------------------------------------------------------
DN_CODE = "3i5s52vxv4ts"
DN_URL = f"https://datanodes.to/{DN_CODE}/tool.zip"
DN_DIRECT = "https://s7.datanodes.to/d/abcdef/tool.zip"
DN_PAGE = f"""<div id="app"><form id="downloadForm" method="POST" action="/download">
<input type="hidden" name="op" value="download2"><input type="hidden" name="id" value="{DN_CODE}">
<input type="hidden" name="rand" value="r4nd"><input type="hidden" name="referer" value="">
<download-countdown :seconds="5" :has-captcha="true" data-sitekey="0xDNKEY"></download-countdown>
</form></div>"""
DN_DEAD = """<div id="app"><h1>File Not Found</h1><p>The file was deleted by its owner</p>
<form><input type="hidden" name="op" value="report_file"></form></div>"""


def test_datanodes_supports():
    p = datanodes.DataNodesPlugin()
    assert p.supports(DN_URL)
    assert p.supports(f"https://datanodes.to/{DN_CODE}")
    assert not p.supports("https://datanodes.to/download")
    assert not p.supports("https://datanodes.to/premium")
    assert not p.supports(f"https://datanodes.example/{DN_CODE}/x")


def test_datanodes_page_flow(monkeypatch):
    slept = []
    monkeypatch.setattr(datanodes, "sleep", slept.append)
    routes = [("GET", f"/{DN_CODE}", Resp(text=DN_PAGE, url="https://datanodes.to/download")),
              ("POST", "/download", Resp(body={"url": "https%3A%2F%2Fs7.datanodes.to%2Fd%2Fabcdef%2Ftool.zip"}))]
    with pytest.raises(CaptchaRequired) as e:
        datanodes.DataNodesPlugin().resolve(DN_URL, ctx_for(routes))
    assert e.value.kind == "turnstile"
    ctx = ctx_for(routes, code="TOKEN")
    r = datanodes.DataNodesPlugin().resolve(DN_URL, ctx)
    assert r.url == DN_DIRECT and r.filename == "tool.zip"
    assert slept == [5]
    posts = [c for c in ctx.client.calls if c[0] == "POST"]
    assert len(posts) == 1                       # the host allows exactly one post
    form = posts[0][2]["data"]
    assert form["op"] == "download2" and form["id"] == DN_CODE and form["rand"] == "r4nd"
    assert form["method_free"] and form["cf-turnstile-response"] == "TOKEN"


def test_datanodes_redirect_answer(monkeypatch):
    monkeypatch.setattr(datanodes, "sleep", lambda s: None)
    routes = [("GET", f"/{DN_CODE}", Resp(text=DN_PAGE)),
              ("POST", "/download", Resp(302, "", headers={"Location": DN_DIRECT}))]
    assert datanodes.DataNodesPlugin().resolve(DN_URL, ctx_for(routes, code="T")).url == DN_DIRECT


def test_datanodes_api_key_skips_the_page():
    acc = Acc(data={"api_key": "K3Y"})
    ctx = ctx_for([("GET", "/api/file/direct_link",
                    Resp(body={"status": 200, "result": {"url": DN_DIRECT, "size": 1234}}))],
                  account=acc)
    r = datanodes.DataNodesPlugin().resolve(DN_URL, ctx)
    assert (r.url, r.size) == (DN_DIRECT, 1234)
    assert "key=K3Y" in ctx.client.calls[0][1]


def test_datanodes_api_refusal_falls_back_to_page(monkeypatch):
    monkeypatch.setattr(datanodes, "sleep", lambda s: None)
    ctx = ctx_for([("GET", "/api/file/direct_link", Resp(body={"status": 403, "msg": "not allowed"})),
                   ("GET", f"/{DN_CODE}", Resp(text=DN_PAGE)),
                   ("POST", "/download", Resp(body={"url": DN_DIRECT}))],
                  code="T", account=Acc(password="K3Y"))
    assert datanodes.DataNodesPlugin().resolve(DN_URL, ctx).url == DN_DIRECT


def test_datanodes_offline_long_wait_and_unknown_page(monkeypatch):
    monkeypatch.setattr(datanodes, "sleep", lambda s: None)
    with pytest.raises(PluginError, match="offline"):
        datanodes.DataNodesPlugin().resolve(DN_URL, ctx_for([("GET", f"/{DN_CODE}", Resp(404, DN_DEAD))]))
    with pytest.raises(PluginError, match="offline"):
        datanodes.DataNodesPlugin().resolve(DN_URL, ctx_for([("GET", f"/{DN_CODE}", Resp(200, DN_DEAD))]))
    slow = DN_PAGE.replace(':seconds="5"', ':seconds="1800"')
    with pytest.raises(PluginError, match="wait 30 minutes"):
        datanodes.DataNodesPlugin().resolve(DN_URL, ctx_for([("GET", f"/{DN_CODE}", Resp(text=slow))], code="T"))
    with pytest.raises(PluginError, match="browser download flow"):
        datanodes.DataNodesPlugin().resolve(DN_URL, ctx_for([("GET", f"/{DN_CODE}", Resp(text="<div id=app></div>"))]))
    routes = [("GET", f"/{DN_CODE}", Resp(text=DN_PAGE)), ("POST", "/download", Resp(200, "<html>step 1</html>"))]
    with pytest.raises(PluginError, match="browser download flow"):
        datanodes.DataNodesPlugin().resolve(DN_URL, ctx_for(routes, code="T"))


# ---------------------------------------------------------------------------
# VikingFile
# ---------------------------------------------------------------------------
VK_HASH = "TPRSfLvcIu"
VK_LINK = "https://s3.vik1ngfile.site/download/abc/tool.zip?token=x"
VK_PAGE = """<html><head><title>tool.zip</title></head><body>
<div id="captcha-download"><div class="cf-turnstile" data-sitekey="0xVKKEY" data-callback="cloudflareCallback"></div></div>
<a id="download-link" class="hidden" href="#">Download</a></body></html>"""


def _vk_routes(post_answer, exist=True):
    return [("POST", "/api/check-file",
             Resp(body=[{"exist": exist, "hash": VK_HASH, "name": "tool.zip", "size": 12345}])),
            ("GET", f"/f/{VK_HASH}", Resp(text=VK_PAGE, url=f"https://vik1ngfile.site/f/{VK_HASH}")),
            ("POST", f"vik1ngfile.site/f/{VK_HASH}", post_answer)]


def test_vikingfile_supports():
    p = vikingfile.VikingFilePlugin()
    assert p.supports(f"https://vikingfile.com/f/{VK_HASH}")
    assert p.supports(f"https://vik1ngfile.site/f/{VK_HASH}")
    assert p.supports("https://vikingfile.com/d/AbCdEf123")
    assert not p.supports("https://vikingfile.com/api")
    assert not p.supports("https://vikingfile.com/f/")
    assert not p.supports(f"https://vikingfile.example/f/{VK_HASH}")


def test_vikingfile_turnstile_then_link():
    with pytest.raises(CaptchaRequired) as e:
        vikingfile.VikingFilePlugin().resolve(f"https://vikingfile.com/f/{VK_HASH}",
                                              ctx_for(_vk_routes(Resp(body={"link": VK_LINK}))))
    assert e.value.kind == "turnstile"
    ctx = ctx_for(_vk_routes(Resp(body={"link": VK_LINK})), code="TOKEN")
    g = vikingfile.VikingFilePlugin().grab(f"https://vikingfile.com/f/{VK_HASH}", ctx)
    assert (g.filename, g.size) == ("tool.zip", 12345)
    r = vikingfile.VikingFilePlugin().resolve(f"https://vikingfile.com/f/{VK_HASH}", ctx)
    assert (r.url, r.filename, r.size) == (VK_LINK, "tool.zip", 12345)
    assert r.headers["referer"] == f"https://vik1ngfile.site/f/{VK_HASH}"
    post = [c for c in ctx.client.calls if c[0] == "POST" and "/f/" in c[1]][0]
    assert post[2]["data"] == {"cf-turnstile-response": "TOKEN"}


def test_vikingfile_offline_and_errors():
    p = vikingfile.VikingFilePlugin()
    with pytest.raises(PluginError, match="offline"):
        p.resolve(f"https://vikingfile.com/f/{VK_HASH}", ctx_for(_vk_routes(None, exist=False)))
    with pytest.raises(PluginError, match="VikingFile: Download limit reached"):
        p.resolve(f"https://vikingfile.com/f/{VK_HASH}",
                  ctx_for(_vk_routes(Resp(body={"error": "Download limit reached"})), code="T"))
    with pytest.raises(PluginError, match="captcha"):
        p.resolve(f"https://vikingfile.com/f/{VK_HASH}",
                  ctx_for(_vk_routes(Resp(body={"error": "Invalid captcha"})), code="T"))
    routes = [("POST", "/api/check-file", Resp(500, "x")),
              ("GET", f"/f/{VK_HASH}", Resp(404, "<title>File not found</title>"))]
    with pytest.raises(PluginError, match="offline"):
        p.resolve(f"https://vikingfile.com/f/{VK_HASH}", ctx_for(routes))


def test_vikingfile_folder_children():
    page = ('<title>My pack</title><a href="/f/AAAAAAAAAA">a.zip</a>'
            '<a href="https://vikingfile.com/f/BBBBBBBBBB"><span>b.zip</span></a><a href="/f/AAAAAAAAAA">a.zip</a>')
    ctx = ctx_for([("GET", "/d/AbCdEf123", Resp(text=page))])
    g = vikingfile.VikingFilePlugin().grab("https://vikingfile.com/d/AbCdEf123", ctx)
    assert g.title == "My pack"
    assert g.children == [["https://vikingfile.com/f/AAAAAAAAAA", "a.zip"],
                          ["https://vikingfile.com/f/BBBBBBBBBB", "b.zip"]]


# ---------------------------------------------------------------------------
# Rootz
# ---------------------------------------------------------------------------
RZ_PAGE = '<script>self.__next_f.push([1,"{\\"shortId\\":\\"BHXge\\",\\"pageToken\\":\\"pt-123.abc\\"}"])</script>'
RZ_OK = {"success": True, "data": {"fileId": "f1d-uuid", "fileName": "tool.zip", "size": 999,
                                   "status": "active", "downloadAllowed": True,
                                   "passwordProtected": False}}


def _rz_routes(api_body, head=None, api_status=200):
    return [("GET", "/d/BHXge", Resp(text=RZ_PAGE)),
            ("GET", "download-by-short", Resp(api_status, body=api_body)),
            ("HEAD", "proxy-download", head or Resp(302, "", headers={"Location": "https://cdn.example/s?sig=1"}))]


def test_rootz_supports():
    p = rootz.RootzPlugin()
    assert p.supports("https://www.rootz.so/d/BHXge") and p.supports("https://rootz.so/d/1cCEKG")
    assert not p.supports("https://rootz.so/docs") and not p.supports("https://rootz.example/d/BHXge")


def test_rootz_resolves_to_proxy_download():
    ctx = ctx_for(_rz_routes(RZ_OK))
    g = rootz.RootzPlugin().grab("https://www.rootz.so/d/BHXge", ctx)
    assert (g.filename, g.size) == ("tool.zip", 999)
    r = rootz.RootzPlugin().resolve("https://www.rootz.so/d/BHXge", ctx)
    assert r.url == "https://www.rootz.so/api/files/proxy-download/f1d-uuid"
    assert r.headers == {"referer": "https://www.rootz.so/d/BHXge"}
    api = [c for c in ctx.client.calls if "download-by-short" in c[1]][0]
    assert api[2]["headers"]["x-page-token"] == "pt-123.abc"


def test_rootz_rate_limit(monkeypatch):
    slept = []
    monkeypatch.setattr(rootz, "sleep", slept.append)
    answers = iter([Resp(302, "", headers={"Location": "/d/BHXge?error=rate_limited&retry_after=20"}),
                    Resp(302, "", headers={"Location": "https://cdn.example/s"})])
    ctx = ctx_for(_rz_routes(RZ_OK, head=lambda u, kw: next(answers)))
    assert rootz.RootzPlugin().resolve("https://www.rootz.so/d/BHXge", ctx).url.endswith("/f1d-uuid")
    assert slept == [20]
    long = Resp(302, "", headers={"Location": "/d/BHXge?error=rate_limited&retry_after=1800"})
    with pytest.raises(PluginError, match="wait 30 minutes"):
        rootz.RootzPlugin().resolve("https://www.rootz.so/d/BHXge", ctx_for(_rz_routes(RZ_OK, head=long)))


def test_rootz_offline_and_restrictions():
    p = rootz.RootzPlugin()
    with pytest.raises(PluginError, match="offline"):
        p.resolve("https://rootz.so/d/BHXge", ctx_for([("GET", "/d/BHXge", Resp(404, "nope"))]))
    with pytest.raises(PluginError, match="offline"):
        p.resolve("https://rootz.so/d/BHXge",
                  ctx_for(_rz_routes({"success": False, "error": "File not found"}, api_status=404)))
    for change, msg in (({"status": "deleted"}, "deleted"),
                        ({"passwordProtected": True}, "password"),
                        ({"downloadAllowed": False}, "does not allow"),
                        ({"remainingDownloads": 0}, "no downloads left")):
        body = json.loads(json.dumps(RZ_OK))
        body["data"].update(change)
        with pytest.raises(PluginError, match=msg):
            p.resolve("https://rootz.so/d/BHXge", ctx_for(_rz_routes(body)))
    with pytest.raises(PluginError, match="token"):
        p.resolve("https://rootz.so/d/BHXge", ctx_for([("GET", "/d/BHXge", Resp(text="<html></html>"))]))


# ---------------------------------------------------------------------------
# FileDitch
# ---------------------------------------------------------------------------
FD_PATH = "/charlie21/aa59bb5b707fb30f002e/cat.mp4"


def test_fileditch_supports_and_paths():
    p = fileditch.FileDitchPlugin()
    for host in ("fileditchfiles.st", "itsfileditchfiles.st", "fileditchvids.st", "fileditchhot.st",
                 "fileditchfiles.me", "fileditchstuff.me", "embedditch.st"):
        assert p.supports(f"https://{host}{FD_PATH}"), host
    assert fileditch.file_path(f"https://fileditchfiles.me/file.php?f={FD_PATH}") == FD_PATH
    assert fileditch.file_path("https://fileditchfiles.st/a/b%20c.zip") == "/a/b c.zip"
    for u in ("https://new.fileditch.com/", "https://new.fileditch.com/api.html",
              f"https://fileditchfiles.st/api{FD_PATH}", "https://fileditchfiles.st/x",
              "https://fileditchfiles.st/a/../b", f"https://notfileditch.example{FD_PATH}"):
        assert not p.supports(u), u


def test_fileditch_status_and_browser_flow():
    ok = Resp(body={"file": FD_PATH, "status": True, "size": 50720388, "views": 80})
    ctx = ctx_for([("GET", "fileditchfiles.st/api/", ok)])
    p = fileditch.FileDitchPlugin()
    g = p.grab(f"https://fileditchvids.st{FD_PATH}", ctx)
    assert (g.filename, g.size) == ("cat.mp4", 50720388)
    assert ctx.client.calls[0][1] == f"https://fileditchfiles.st/api{FD_PATH}"
    with pytest.raises(PluginError, match="browser download flow"):
        p.resolve(f"https://fileditchfiles.st{FD_PATH}", ctx)
    r = p.resolve(f"https://fileditchhot.st{FD_PATH}", ctx)
    assert (r.url, r.filename, r.size) == (f"https://fileditchhot.st{FD_PATH}", "cat.mp4", 50720388)


def test_fileditch_gone_unknown_and_rate_limited():
    p = fileditch.FileDitchPlugin()
    gone = ctx_for([("GET", "/api/", Resp(body={"file": FD_PATH, "status": False, "size": None}))])
    with pytest.raises(PluginError, match="offline"):
        p.resolve(f"https://fileditchfiles.st{FD_PATH}", gone)
    unknown = ctx_for([("GET", "/api/", Resp(body={"file": FD_PATH, "status": "unknown", "size": None}))])
    with pytest.raises(PluginError, match="try again"):
        p.grab(f"https://fileditchfiles.st{FD_PATH}", unknown)
    limited = ctx_for([("GET", "/api/", Resp(429, body={"error": "rate limited"}))])
    with pytest.raises(PluginError, match="rate limit"):
        p.grab(f"https://fileditchfiles.st{FD_PATH}", limited)
