"""Site links: torrent trackers (Rutor, Tapochek), paste and multi-link pages,
direct-download servers, FTP/FTPS and magnet links without the add-on.

Offline only: HTTP sites use a fake client with small fixtures modelled on
the live pages (2026-10-30); FTP runs against a tiny FTP server started in
the test (pyftpdlib is not installed, so the server is a few lines here).
"""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

from opengrab.http_client import Response
from opengrab.plugins.base import LoginRequired, PluginContext, PluginError


# ---------------------------------------------------------------------------
# fake HTTP client / context
# ---------------------------------------------------------------------------
class FakeClient:
    """Routes (method, url) -> Response; records every request."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls: list[tuple[str, str, dict]] = []
        self.cookies: dict[str, str] = {}
        self.logged_in = False

    def request(self, method, url, data=None, headers=None, timeout=None, **kw):
        self.calls.append((method, url, {"data": data, "headers": dict(headers or {})}))
        key = (method, url)
        if key not in self.routes:
            return Response(404, {"content-type": "text/html"}, b"not found", url)
        r = self.routes[key]
        return r(data, headers) if callable(r) else r

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def set_cookie(self, url, name, value):
        self.cookies[name] = value


def html(body: str, status: int = 200, charset: str = "utf-8", url: str = "") -> Response:
    return Response(status, {"content-type": f"text/html; charset={charset}"},
                    body.encode(charset, "xmlcharrefreplace"), url)


class Accounts:
    def __init__(self, acc=None):
        self.acc = acc

    def find(self, site):
        return self.acc if self.acc and site == "tapochek.net" else None

    def for_domain(self, host):
        return self.acc if self.acc and host.endswith("tapochek.net") else None


def ctx_for(client, account=None) -> PluginContext:
    settings = SimpleNamespace(timeout_s=10, grab_timeout_s=10)
    return PluginContext(client=client, settings=settings,
                         accounts=Accounts(account) if account else None)


# ---------------------------------------------------------------------------
# A. Rutor
# ---------------------------------------------------------------------------
RUTOR_TOPIC = """<html><head><title>rutor.info :: Some Game (2026) PC</title></head><body>
<div id="all"><h1>Some Game [v 1.2] (2026) PC | RePack</h1>
<div id="download">
<a href="magnet:?xt=urn:btih:d69d80365bd21221ecae16c4b0fe37f2d1e3385f&amp;dn=rutor.info_Some+Game&amp;tr=udp://opentor.net:6969"><img src="//cdn/i/magnet.gif"></a>
<a href="//d.rutor.info/download/1102751"><img src="//cdn/t/down.png"> Download Some_Game.torrent</a>
</div></div></body></html>"""


def test_rutor_topic_becomes_magnet_child():
    from opengrab.plugins.builtin.trackers import RutorPlugin
    url = "https://rutor.info/torrent/1102751"
    c = FakeClient({("GET", url): html(RUTOR_TOPIC, url=url)})
    p = RutorPlugin()
    r = p.resolve(url, ctx_for(c))
    assert r.url == ""
    assert len(r.children) == 1
    child, title = r.children[0]
    assert child.startswith("magnet:?xt=urn:btih:d69d80365bd21221ecae16c4b0fe37f2d1e3385f&dn=")
    assert "&amp;" not in child
    assert title == "Some Game [v 1.2] (2026) PC | RePack"
    assert r.extra["torrent_url"] == "https://d.rutor.info/download/1102751"
    assert len(c.calls) == 1          # grab + resolve fetch the topic once


def test_rutor_without_magnet_gives_torrent_url_and_mirrors_work():
    from opengrab.plugins.builtin.trackers import RutorPlugin
    url = "https://rutor.is/torrent/77/some-slug"
    page = RUTOR_TOPIC.replace("magnet:?xt=urn:btih:", "#")      # no magnet on the page
    page = page.replace("//d.rutor.info/", "//d.rutor.is/")
    c = FakeClient({("GET", url): html(page, url=url)})
    g = RutorPlugin().grab(url, ctx_for(c))
    assert g.children == [["https://d.rutor.is/download/1102751", "Some Game [v 1.2] (2026) PC | RePack"]]


def test_rutor_download_url_is_a_direct_torrent_file():
    from opengrab.plugins.builtin.trackers import RutorPlugin
    p = RutorPlugin()
    url = "https://d.rutor.info/download/1102751"
    assert p.supports(url)
    r = p.resolve(url, ctx_for(FakeClient()))
    assert r.url == url and r.filename == "rutor_1102751.torrent"
    assert r.headers["referer"] == "https://rutor.info/torrent/1102751"
    # only topic and download pages are claimed; the rest is the generic grabber's
    assert not p.supports("https://rutor.info/search/0/0/000/0/test")
    assert not p.supports("https://example.com/torrent/1")


# ---------------------------------------------------------------------------
# A. Tapochek
# ---------------------------------------------------------------------------
TAP = "https://tapochek.net/"
TAP_TOPIC = TAP + "viewtopic.php?t=290618"
GUEST_TOPIC = """<html><head><title>Hell is Us (RUS|ENG) [DL|GOG]</title></head><body>
<form action="./login.php" method="post"><input name="login_username"></form>
<fieldset class="attach"><legend>Download</legend>
<h1 class="attach_link"><a href="profile.php?mode=register">Скачать (бесплатно)!</a></h1>
</fieldset></body></html>"""
MEMBER_TOPIC = """<html><head><title>Hell is Us (RUS|ENG) [DL|GOG]</title></head><body>
<a href="./login.php?logout=1">Выход</a>
<fieldset class="attach"><legend>Download</legend>
<a href="./download.php?id=123456" class="dl-stub">Скачать .torrent</a>
</fieldset></body></html>"""


def _login_route(client, ok=True, captcha=False):
    def handle(data, headers):
        q = parse_qs(data, encoding="cp1251")
        client.login_form = q
        if captcha:
            return html('<form><img src="captcha.php"><input name="cap_code"></form>', charset="cp1251")
        if ok and q.get("login_password") == ["s3cret"]:
            client.logged_in = True
            return html('<a href="login.php?logout=1">Выход</a>', charset="cp1251")
        return html("<p>Неверное имя или пароль</p>", charset="cp1251")
    return handle


def _topic_route(client):
    return lambda data, headers: html(MEMBER_TOPIC if client.logged_in else GUEST_TOPIC,
                                      charset="windows-1251", url=TAP_TOPIC)


def test_tapochek_without_account_asks_for_login():
    from opengrab.plugins.builtin.trackers import TapochekPlugin
    c = FakeClient()
    c.routes[("GET", TAP_TOPIC)] = _topic_route(c)
    with pytest.raises(LoginRequired) as ei:
        TapochekPlugin().grab(TAP_TOPIC, ctx_for(c))
    assert "Settings, Accounts" in ei.value.hint and "tapochek.net" in ei.value.hint


def test_tapochek_logs_in_with_the_form_and_returns_torrent_child():
    from opengrab.plugins.builtin.trackers import TapochekPlugin
    c = FakeClient()
    c.routes[("GET", TAP_TOPIC)] = _topic_route(c)
    c.routes[("POST", TAP + "login.php")] = _login_route(c)
    acc = SimpleNamespace(site="tapochek.net", username="Пользователь", password="s3cret", data={})
    r = TapochekPlugin().resolve(TAP_TOPIC, ctx_for(c, acc))
    assert r.children == [[TAP + "download.php?id=123456", "Hell is Us (RUS|ENG) [DL|GOG]"]]
    # the site's own form fields, cp1251 like the forum page
    assert c.login_form["login_username"] == ["Пользователь"]
    assert c.login_form["login_password"] == ["s3cret"]
    assert c.login_form["login"] == ["Вход"]
    # credentials are never in a URL
    assert all("s3cret" not in u for _, u, _ in c.calls)


def test_tapochek_prefers_magnet_and_skips_login_when_page_has_one():
    from opengrab.plugins.builtin.trackers import TapochekPlugin
    page = MEMBER_TOPIC.replace("</fieldset>",
                                '<a class="magnet-link" href="magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef01&amp;tr=x">m</a></fieldset>')
    c = FakeClient({("GET", TAP_TOPIC): html(page, charset="cp1251", url=TAP_TOPIC)})
    g = TapochekPlugin().grab(TAP_TOPIC, ctx_for(c))     # no account needed
    assert g.children[0][0] == "magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef01&tr=x"


def test_tapochek_bad_password_and_captcha_are_login_states():
    from opengrab.plugins.builtin.trackers import TapochekPlugin
    acc = SimpleNamespace(site="tapochek.net", username="u", password="wrong", data={})
    c = FakeClient()
    c.routes[("GET", TAP_TOPIC)] = _topic_route(c)
    c.routes[("POST", TAP + "login.php")] = _login_route(c)
    with pytest.raises(LoginRequired) as ei:
        TapochekPlugin().grab(TAP_TOPIC, ctx_for(c, acc))
    assert "did not accept" in ei.value.hint
    c.routes[("POST", TAP + "login.php")] = _login_route(c, captcha=True)
    with pytest.raises(LoginRequired) as ei:
        TapochekPlugin().grab(TAP_TOPIC, ctx_for(c, acc))
    assert "captcha" in ei.value.hint.lower()


def test_tapochek_download_url_logs_in_then_is_direct():
    from opengrab.plugins.builtin.trackers import TapochekPlugin
    url = TAP + "download.php?id=123456"
    c = FakeClient()
    c.routes[("GET", TAP + "index.php")] = html("<p>guest</p>", charset="cp1251")
    c.routes[("POST", TAP + "login.php")] = _login_route(c)
    acc = SimpleNamespace(site="tapochek.net", username="u", password="s3cret",
                          data={"cookie_header": "bb_data=abc"})
    p = TapochekPlugin()
    assert p.supports(url) and p.supports(TAP_TOPIC)
    assert not p.supports(TAP + "index.php")
    r = p.resolve(url, ctx_for(c, acc))
    assert r.url == url and r.filename.endswith(".torrent")
    assert c.logged_in and c.cookies == {"bb_data": "abc"}
    with pytest.raises(LoginRequired):
        TapochekPlugin().grab(url, ctx_for(FakeClient()))


# ---------------------------------------------------------------------------
# B. pastes and multi-link pages
# ---------------------------------------------------------------------------
PASTE = """<html><head><title>My links - ZPaste</title></head><body>
<nav><a href="/">Home</a> <a href="/new">New paste</a> <a href="https://twitter.com/zpaste">tw</a></nav>
<pre class="paste-content">Part 1: https://rapidgator.net/file/abc123/movie.part1.rar.html
Part 2: https://rapidgator.net/file/def456/movie.part2.rar.html
Mirror: <a href="https://1fichier.com/?xyz789">https://1fichier.com/?xyz789</a>
Torrent: magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&amp;dn=Movie
FTP: ftp://files.example.org/pub/movie.mkv.
Direct: https://tunnel5.dlproxy.uk/dl/AbCdEf</pre>
<footer><a href="/terms">Terms</a></footer></body></html>"""


def test_paste_returns_every_link_in_the_paste():
    from opengrab.plugins.builtin.pastes import PastePlugin
    url = "https://zpaste.net/Xy12Ab"
    c = FakeClient({("GET", url): html(PASTE, url=url)})
    p = PastePlugin()
    assert p.supports(url) and not p.supports("https://zpaste.net/")
    r = p.resolve(url, ctx_for(c))
    urls = [u for u, _ in r.children]
    assert urls == [
        "https://rapidgator.net/file/abc123/movie.part1.rar.html",
        "https://rapidgator.net/file/def456/movie.part2.rar.html",
        "https://1fichier.com/?xyz789",
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Movie",
        "ftp://files.example.org/pub/movie.mkv",
        "https://tunnel5.dlproxy.uk/dl/AbCdEf",
    ]
    assert r.children[3][1] == "Movie"


def test_paste_plain_text_and_raw_fallback():
    from opengrab.plugins.builtin.pastes import PastePlugin
    url = "https://pastebin.com/AbCd1234"
    page = '<html><body><div id="app"></div><a href="/raw/AbCd1234">raw</a></body></html>'
    raw = Response(200, {"content-type": "text/plain; charset=utf-8"},
                   b"https://mega.nz/file/aaa#key\r\nhttps://gofile.io/d/bbb\r\n", url)
    c = FakeClient({("GET", url): html(page, url=url),
                    ("GET", "https://pastebin.com/raw/AbCd1234"): raw})
    g = PastePlugin().grab(url, ctx_for(c))
    assert [u for u, _ in g.children] == ["https://mega.nz/file/aaa#key", "https://gofile.io/d/bbb"]


def test_empty_paste_is_an_error():
    from opengrab.plugins.builtin.pastes import PastePlugin
    url = "https://zpaste.net/empty"
    c = FakeClient({("GET", url): html("<pre>just text</pre>", url=url)})
    with pytest.raises(PluginError):
        PastePlugin().grab(url, ctx_for(c))


MULTI = """<html><head><title>Release - Blog</title></head><body>
<a href="https://blog.example/">Home</a> <a href="https://blog.example/category/movies">Movies</a>
<p>Download links:</p>
<a href="https://rapidgator.net/file/abc123">RG</a>
<a href="https://nitroflare.com/view/DEF456">NF</a>
<a href="https://katfile.com/xyz/file.rar.html">KF</a>
<a href="https://www.facebook.com/sharer.php?u=x">share</a>
</body></html>"""


def test_multilink_page_gives_one_child_per_hoster_link_where_generic_does_not():
    from opengrab.plugins.builtin.generic import GenericPagePlugin
    from opengrab.plugins.builtin.pastes import MultiLinkPlugin
    url = "https://blog.example/release-1"
    c = FakeClient({("GET", url): html(MULTI, url=url)})
    settings = SimpleNamespace(timeout_s=10, grab_timeout_s=10)
    gen = GenericPagePlugin().grab(url, PluginContext(client=c, settings=settings))
    assert gen.children == []              # the gap this plugin fills
    r = MultiLinkPlugin().resolve(url, ctx_for(c))
    assert [u for u, _ in r.children] == ["https://rapidgator.net/file/abc123",
                                          "https://nitroflare.com/view/DEF456",
                                          "https://katfile.com/xyz/file.rar.html"]
    c.routes[("GET", url)] = html('<a href="https://blog.example/about">about</a>', url=url)
    with pytest.raises(PluginError):
        MultiLinkPlugin().grab(url, ctx_for(c))


def test_registry_order_for_the_new_links():
    from opengrab.plugins.registry import PluginRegistry
    reg = PluginRegistry()
    reg.scan()
    assert not [e for e in reg.errors_snapshot() if "builtin" in e["where"]]

    def ids(u):
        return [p.id for p in reg.for_url(u)]
    assert ids("https://rutor.info/torrent/1")[0] == "rutor"
    assert ids("https://tapochek.net/viewtopic.php?t=1")[0] == "tapochek"
    assert ids("https://zpaste.net/abc")[0] == "paste"
    assert ids("https://stor11.mocha.my/f/abc")[0] == "directhost"
    assert ids("ftp://ftp.example.org/pub/")[0] == "ftp"
    other = ids("https://blog.example/page")
    assert other.index("generic") < other.index("multilink")   # fallback after generic


# ---------------------------------------------------------------------------
# C. direct-download servers
# ---------------------------------------------------------------------------
class HeadOnly(FakeClient):
    def get(self, url, **kw):
        raise AssertionError("must not GET (could download the whole file)")


def test_direct_host_any_url_is_a_file_with_referer():
    from opengrab.plugins.builtin.dlproxy import DirectHostPlugin, is_direct_host
    p = DirectHostPlugin()
    assert p.supports("https://tunnel5.dlproxy.uk/AbCd")
    assert p.supports("https://stor11.mocha.my/x/y?token=1")
    assert not p.supports("https://mocha.my/")             # the main site is a web site
    assert not is_direct_host("https://dlproxy.uk.evil.example/x")
    url = "https://tunnel5.dlproxy.uk/AbCd"
    c = HeadOnly({("HEAD", url): Response(200, {
        "content-type": "application/octet-stream", "content-length": "1048576",
        "content-disposition": 'attachment; filename="Movie 2026.mkv"'}, b"", url)})
    r = p.resolve(url, ctx_for(c))
    assert (r.url, r.filename, r.size) == (url, "Movie 2026.mkv", 1048576)
    assert r.headers == {"referer": "https://tunnel5.dlproxy.uk/"}
    assert c.calls[0][2]["headers"]["referer"] == "https://tunnel5.dlproxy.uk/"


def test_direct_host_name_from_content_type_and_head_refused():
    from opengrab.plugins.builtin.dlproxy import DirectHostPlugin
    p = DirectHostPlugin()
    url = "https://stor11.mocha.my/v/9f8e7d"
    c = HeadOnly({("HEAD", url): Response(200, {"content-type": "video/mp4"}, b"", url)})
    g = p.grab(url, ctx_for(c))
    assert g.filename == "9f8e7d.mp4" and g.direct_url == url and g.size is None
    # HEAD not allowed: still handed to the downloader (it probes with a ranged GET)
    c = HeadOnly({("HEAD", url): Response(405, {}, b"", url)})
    assert p.resolve(url, ctx_for(c)).url == url


def test_direct_host_missing_file_or_web_page_fails():
    from opengrab.plugins.builtin.dlproxy import DirectHostPlugin
    p = DirectHostPlugin()
    url = "https://tunnel5.dlproxy.uk/gone"
    with pytest.raises(PluginError, match="not found"):
        p.grab(url, ctx_for(HeadOnly({("HEAD", url): Response(404, {}, b"", url)})))
    page = Response(200, {"content-type": "text/html"}, b"", url)
    with pytest.raises(PluginError, match="web page"):
        p.grab(url, ctx_for(HeadOnly({("HEAD", url): page})))


# ---------------------------------------------------------------------------
# D. FTP — a tiny local FTP server (PASV, SIZE, REST, RETR, MLSD, LIST)
# ---------------------------------------------------------------------------
class MiniFtpServer:
    def __init__(self, files: dict[str, bytes], user="anonymous", password=None,
                 mlsd=True, rest=True):
        self.files = files
        self.user, self.password = user, password
        self.mlsd, self.rest = mlsd, rest
        self.log: list[str] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.slow = 0.0
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self.sock.close()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _dirs(self, path):
        path = path.rstrip("/") + "/"
        names = {}
        for f, data in self.files.items():
            if f.startswith(path) and f != path:
                rest = f[len(path):]
                if "/" in rest:
                    names[rest.split("/", 1)[0]] = None
                else:
                    names[rest] = len(data)
        return names

    def _session(self, c):
        f = c.makefile("rb")

        def send(line):
            c.sendall((line + "\r\n").encode())
        send("220 mini ftp")
        user, rest_at, pasv = "", 0, None
        try:
            for raw in f:
                line = raw.decode().rstrip("\r\n")
                cmd, _, arg = line.partition(" ")
                cmd = cmd.upper()
                self.log.append(cmd if cmd != "PASS" else "PASS ***")
                if cmd == "USER":
                    user = arg
                    send("331 password please")
                elif cmd == "PASS":
                    ok = user == self.user and (self.password is None or arg == self.password)
                    send("230 ok" if ok else "530 Login incorrect.")
                elif cmd == "TYPE":
                    send("200 type set")
                elif cmd == "SIZE":
                    if arg in self.files:
                        send(f"213 {len(self.files[arg])}")
                    else:
                        send("550 no such file")
                elif cmd == "PASV":
                    pasv = socket.socket()
                    pasv.bind(("127.0.0.1", 0))
                    pasv.listen(1)
                    p = pasv.getsockname()[1]
                    send(f"227 Entering Passive Mode (127,0,0,1,{p >> 8},{p & 255})")
                elif cmd == "REST":
                    if not self.rest:
                        send("502 REST not implemented")
                    else:
                        rest_at = int(arg)
                        send("350 restarting")
                elif cmd in ("RETR", "MLSD", "LIST"):
                    if cmd == "MLSD" and not self.mlsd:
                        send("500 unknown command")
                        continue
                    if cmd == "RETR" and arg not in self.files:
                        send("550 no such file")
                        continue
                    send("150 opening data connection")
                    d, _ = pasv.accept()
                    pasv.close()
                    try:
                        if cmd == "RETR":
                            data = self.files[arg][rest_at:]
                            rest_at = 0
                            for i in range(0, len(data), 4096):
                                d.sendall(data[i:i + 4096])
                                if self.slow:
                                    time.sleep(self.slow)
                        else:
                            out = []
                            for name, size in self._dirs(arg or "/").items():
                                if cmd == "MLSD":
                                    kind = "dir" if size is None else "file"
                                    out.append(f"type={kind};size={size or 0}; {name}")
                                else:
                                    perm = "drwxr-xr-x" if size is None else "-rw-r--r--"
                                    out.append(f"{perm} 1 ftp ftp {size or 0} Jan 01 00:00 {name}")
                            d.sendall(("\r\n".join(out) + "\r\n").encode())
                    except OSError:
                        pass
                    finally:
                        d.close()
                    send("226 done")
                elif cmd == "QUIT":
                    send("221 bye")
                    break
                else:
                    send("502 not implemented")
        except OSError:
            pass
        finally:
            c.close()


DATA = bytes(range(256)) * 400          # 102400 bytes


@pytest.fixture
def ftpd():
    srv = MiniFtpServer({"/pub/file.bin": DATA, "/pub/notes.txt": b"hello\n",
                         "/pub/sub/deep.bin": b"x"})
    yield srv
    srv.close()


def _settings(tmp_path, **kw):
    from opengrab.settings import Settings
    return Settings(download_dir=str(tmp_path), **kw)


def _download(url, tmp_path, name="file.bin", **kw):
    from opengrab.engine.downloader import LinkDownload
    from opengrab.models import Link
    dl = LinkDownload(Link(url=url, filename=name), url, tmp_path,
                      settings=_settings(tmp_path), **kw)
    return dl, dl.run


def test_parse_ftp_urls():
    from opengrab.engine.ftp import parse_ftp_url, is_ftp_url
    t = parse_ftp_url("ftp://user:p%40ss@host.example:2121/dir/My%20File.iso")
    assert (t.host, t.port, t.user, t.password, t.path, t.tls) == \
        ("host.example", 2121, "user", "p@ss", "/dir/My File.iso", False)
    assert "p@ss" not in t.safe_url() and "p%40ss" not in t.safe_url()
    a = parse_ftp_url("ftp://ftp.gnu.org/gnu/")
    assert (a.user, a.is_dir, a.name) == ("anonymous", True, "gnu")
    s = parse_ftp_url("ftps://h/x")
    assert s.tls and not s.implicit_tls and parse_ftp_url("ftps://h:990/x").implicit_tls
    assert is_ftp_url("FTP://h/x") and not is_ftp_url("https://h/x")


def test_ftp_plugin_file_and_directory(ftpd):
    from opengrab.plugins.builtin.ftp import FtpPlugin
    base = f"ftp://127.0.0.1:{ftpd.port}"
    p = FtpPlugin()
    ctx = ctx_for(FakeClient())
    r = p.resolve(base + "/pub/file.bin", ctx)
    assert (r.url, r.filename, r.size) == (base + "/pub/file.bin", "file.bin", len(DATA))
    g = p.grab(base + "/pub/", ctx)
    assert sorted(g.children) == [(base + "/pub/file.bin", "file.bin"),
                                  (base + "/pub/notes.txt", "notes.txt")]
    assert g.extra["subdirs"] == [base + "/pub/sub/"]
    ftpd.mlsd = False                        # LIST fallback
    g = p.grab(base + "/pub/", ctx)
    assert sorted(n for _, n in g.children) == ["file.bin", "notes.txt"]
    with pytest.raises(PluginError, match="not found"):
        p.grab(base + "/pub/missing.bin", ctx)


def test_ftp_download_full_and_resume_with_rest(ftpd, tmp_path):
    url = f"ftp://127.0.0.1:{ftpd.port}/pub/file.bin"
    dl, run = _download(url, tmp_path)
    path = run()
    assert Path(path).read_bytes() == DATA
    assert dl.progress()[0] == len(DATA) and dl.progress()[1] == len(DATA)
    assert "SIZE" in ftpd.log and "REST" not in ftpd.log
    # resume: a .part with the first 30000 bytes -> REST 30000
    out = tmp_path / "again"
    out.mkdir()
    (out / "file.bin.part").write_bytes(DATA[:30000])
    ftpd.log.clear()
    dl, run = _download(url, out)
    assert Path(run()).read_bytes() == DATA
    assert "REST" in ftpd.log
    assert dl.start_done == 30000


def test_ftp_pause_closes_and_resumes_with_rest(ftpd, tmp_path):
    from opengrab.engine.downloader import _RateLimiter
    url = f"ftp://127.0.0.1:{ftpd.port}/pub/file.bin"
    pause = threading.Event()
    seen = []

    class Limiter(_RateLimiter):
        def consume(self, n, stop_ev=None):
            seen.append(n)
            if sum(seen) >= 20000 and not hasattr(self, "paused"):
                self.paused = True
                pause.set()
                threading.Timer(0.3, pause.clear).start()
    ftpd.slow = 0.005
    _, run = _download(url, tmp_path, pause_event=pause, rate_limiter=Limiter(0))
    assert Path(run()).read_bytes() == DATA
    assert ftpd.log.count("RETR") == 2 and "REST" in ftpd.log


def test_ftp_restarts_when_server_cannot_resume(ftpd, tmp_path):
    ftpd.rest = False
    url = f"ftp://127.0.0.1:{ftpd.port}/pub/file.bin"
    (tmp_path / "file.bin.part").write_bytes(b"garbage!" * 100)
    _, run = _download(url, tmp_path)
    assert Path(run()).read_bytes() == DATA


def test_ftp_stop_event_rate_limiter_and_login_error(ftpd, tmp_path):
    from opengrab.engine.downloader import DownloadError, _RateLimiter
    url = f"ftp://127.0.0.1:{ftpd.port}/pub/file.bin"
    stop = threading.Event()
    consumed = []

    class Limiter(_RateLimiter):
        def consume(self, n, stop_ev=None):
            consumed.append(n)
            if sum(consumed) >= 20000:
                stop.set()
    ftpd.slow = 0.01
    _, run = _download(url, tmp_path, stop_event=stop, rate_limiter=Limiter(0))
    with pytest.raises(DownloadError) as ei:
        run()
    assert ei.value.cancelled
    assert sum(consumed) >= 20000                 # every block went through the limiter
    assert (tmp_path / "file.bin.part").stat().st_size < len(DATA)
    # wrong login: clear message, not transient, password not echoed
    locked = MiniFtpServer({"/f": b"x"}, user="bob", password="right")
    try:
        _, run = _download(f"ftp://bob:hunter2@127.0.0.1:{locked.port}/f", tmp_path, name="f")
        with pytest.raises(DownloadError) as ei:
            run()
        assert "login failed" in str(ei.value) and "hunter2" not in str(ei.value)
        assert not ei.value.transient
        _, run = _download(f"ftp://bob:right@127.0.0.1:{locked.port}/f", tmp_path, name="f")
        assert Path(run()).read_bytes() == b"x"
    finally:
        locked.close()


# ---------------------------------------------------------------------------
# E. magnet links without the BitTorrent add-on
# ---------------------------------------------------------------------------
def test_magnet_without_torrent_addon_says_install_it_in_settings(tmp_path, monkeypatch):
    from opengrab.engine import torrents
    from opengrab.engine.manager import DownloadManager
    from opengrab.models import LinkStatus
    monkeypatch.setenv("OPENGRAB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(torrents, "libtorrent_available", lambda: False)
    mgr = DownloadManager(_settings(tmp_path / "dl"))
    try:
        link = mgr.add_url("magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=x")
        deadline = time.time() + 10
        while link.status != LinkStatus.ERROR and time.time() < deadline:
            time.sleep(0.05)
        assert link.status == LinkStatus.ERROR
        assert link.plugin == "torrent"
        assert "BitTorrent" in link.error and "add-on" in link.error
        assert "Install it in Settings" in link.error
    finally:
        mgr.shutdown()
