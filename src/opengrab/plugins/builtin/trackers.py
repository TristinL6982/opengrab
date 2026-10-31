"""Torrent tracker topic pages: Rutor and Tapochek.

A topic page becomes ONE child link: the magnet link when the page has one,
otherwise the .torrent download URL. The child goes through add_url again:
a magnet goes straight to the BitTorrent add-on; a .torrent URL comes back
to this plugin, is downloaded as a small file, and the engine opens finished
.torrent files in the add-on (settings.torrent_open_files).

* Rutor (rutor.info, rutor.is and mirrors): no account needed.
* Tapochek (tapochek.net): .torrent files need a user account. The plugin
  logs in with the site's normal login form using the account saved in
  Settings, Accounts (site "tapochek.net"); without one it raises
  LoginRequired. A login captcha is never bypassed: the user is told to log
  in in the browser and import the cookies into the account instead.

Mirror lists are tuples below; add a domain to support another mirror.
"""
from __future__ import annotations

import html as _html
import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from ..base import (GrabResult, LoginRequired, PluginContext, PluginError,
                    ResolveResult, SitePlugin)

RUTOR_HOSTS: tuple[str, ...] = ("rutor.info", "rutor.is", "rutor.org", "new-rutor.org")
TAPOCHEK_HOSTS: tuple[str, ...] = ("tapochek.net",)

MAGNET_RE = re.compile(r'magnet:\?xt=urn:[^"\'<>\s]+', re.I)


def _host_in(url: str, hosts: tuple[str, ...]) -> str:
    """The listed domain the URL belongs to ("" when none)."""
    host = (urlparse(url).hostname or "").lower()
    for h in hosts:
        if host == h or host.endswith("." + h):
            return h
    return ""


def page_text(r) -> str:
    """Decode a response with the charset it declares (Tapochek is cp1251)."""
    ct = (r.headers.get("content-type") or "").lower()
    m = re.search(r"charset=([\w-]+)", ct)
    raw = r.content or b""
    if not m:
        head = raw[:2048].decode("ascii", "ignore")
        m = re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head, re.I)
    enc = m.group(1) if m else "utf-8"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _clean_title(t: str) -> str:
    t = _html.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()
    return re.sub(r"\s+", " ", t)[:300]


def find_magnet(html: str) -> str:
    m = MAGNET_RE.search(html or "")
    return _html.unescape(m.group(0)) if m else ""


def _fetch(ctx: PluginContext, url: str, **kw):
    try:
        r = ctx.client.get(url, timeout=getattr(ctx.settings, "grab_timeout_s", None), **kw)
    except PluginError:
        raise                               # CaptchaRequired & co. reach the engine
    except Exception as e:
        raise PluginError(f"fetch failed: {e}")
    if r.status_code in (404, 410):
        raise PluginError(f"topic not found (HTTP {r.status_code})")
    if r.status_code >= 400:
        raise PluginError(f"HTTP {r.status_code}")
    return r


# The engine calls grab() and then resolve() for the same link: keep the
# last page result for a moment so a topic is fetched (and logged in) once.
_RECENT_S = 60.0


def _remember(plugin, url: str, g: GrabResult) -> GrabResult:
    plugin._recent = (url, time.monotonic(), g)
    return g


def _recent(plugin, url: str):
    last = getattr(plugin, "_recent", None)
    if last and last[0] == url and time.monotonic() - last[1] < _RECENT_S:
        return last[2]
    return None


# ---------------------------------------------------------------------------
# Rutor
# ---------------------------------------------------------------------------
class RutorPlugin(SitePlugin):
    id = "rutor"
    name = "Rutor"
    site = "rutor.info"
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "rutor.info / rutor.is topic pages -> magnet (or .torrent) child link."
    hosts = RUTOR_HOSTS

    TOPIC_RE = re.compile(r"^/torrent/(\d+)")
    DOWNLOAD_RE = re.compile(r"^/download/(\d+)")

    def supports(self, url: str) -> bool:
        if not _host_in(url, self.hosts):
            return False
        path = urlparse(url).path
        return bool(self.TOPIC_RE.match(path) or self.DOWNLOAD_RE.match(path))

    def _download_result(self, url: str) -> GrabResult:
        tid = self.DOWNLOAD_RE.match(urlparse(url).path).group(1)
        mirror = _host_in(url, self.hosts)
        return GrabResult(title=f"rutor {tid}", filename=f"rutor_{tid}.torrent",
                          site=urlparse(url).netloc, plugin_id=self.id, direct_url=url,
                          direct_headers={"referer": f"https://{mirror}/torrent/{tid}"})

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        return _remember(self, url, self._grab(url, ctx))

    def _grab(self, url: str, ctx: PluginContext) -> GrabResult:
        path = urlparse(url).path
        if self.DOWNLOAD_RE.match(path):
            return self._download_result(url)
        r = _fetch(ctx, url)
        html = page_text(r)
        title = ""
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
        if m:
            title = _clean_title(m.group(1))
        block = html
        i = html.find('id="download"')
        if i >= 0:
            block = html[i:i + 4000]
        magnet = find_magnet(block) or find_magnet(html)
        tm = re.search(r'href="([^"]*/download/\d+)"', block) or \
            re.search(r'href="([^"]*/download/\d+)"', html)
        torrent_url = urljoin(str(r.url or url), _html.unescape(tm.group(1))) if tm else ""
        child = magnet or torrent_url
        if not child:
            raise PluginError("no magnet or .torrent link on this rutor page")
        return GrabResult(title=title or url, site=urlparse(url).netloc, plugin_id=self.id,
                          children=[[child, title]],
                          extra={"magnet": magnet, "torrent_url": torrent_url})

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        g = _recent(self, url) or self.grab(url, ctx)
        if g.direct_url:
            return ResolveResult(url=g.direct_url, filename=g.filename,
                                 headers=g.direct_headers)
        return ResolveResult(url="", children=list(g.children), extra=g.extra)


# ---------------------------------------------------------------------------
# Tapochek (TorrentPier forum)
# ---------------------------------------------------------------------------
NEED_ACCOUNT_HINT = ("Tapochek only gives .torrent files to members. Add your "
                     "tapochek.net user name and password in Settings, Accounts "
                     "(site: tapochek.net), then choose Retry.")


class TapochekPlugin(SitePlugin):
    id = "tapochek"
    name = "Tapochek"
    site = "tapochek.net"
    origin = "builtin"
    supports_captcha = False
    supports_login = True
    description = "tapochek.net topic pages -> magnet or .torrent child (needs an account)."
    hosts = TAPOCHEK_HOSTS

    def supports(self, url: str) -> bool:
        if not _host_in(url, self.hosts):
            return False
        return self._kind(url) != ""

    @staticmethod
    def _kind(url: str) -> str:
        p = urlparse(url)
        path = p.path.lower().rsplit("/", 1)[-1]
        q = parse_qs(p.query)
        if path == "viewtopic.php" and ("t" in q or "p" in q):
            return "topic"
        if (path == "download.php" and "id" in q) or (path == "dl.php" and "t" in q):
            return "download"
        return ""

    @staticmethod
    def _base(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme or 'https'}://{p.netloc}/"

    @staticmethod
    def _logged_in(html: str) -> bool:
        return bool(re.search(r"login\.php\?logout|mode=logout|logout=1", html or "", re.I))

    @staticmethod
    def _links(html: str, base: str) -> tuple[str, str]:
        magnet = find_magnet(html)
        m = (re.search(r'href="([^"]*download\.php\?id=\d+[^"]*)"', html, re.I)
             or re.search(r'href="([^"]*dl\.php\?t=\d+[^"]*)"', html, re.I))
        torrent = urljoin(base, _html.unescape(m.group(1))) if m else ""
        return magnet, torrent

    def _account(self, ctx: PluginContext, url: str):
        acc = ctx.account_for(site="tapochek.net")
        if acc is None:
            acc = ctx.account_for(url=url)
        return acc

    def _use_saved_cookies(self, ctx: PluginContext, acc, base: str) -> None:
        """An account may hold browser cookies instead of (or with) a password."""
        header = str((getattr(acc, "data", None) or {}).get("cookie_header") or "")
        setter = getattr(ctx.client, "set_cookie", None)
        if not header or setter is None:
            return
        for part in header.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                setter(base, k, v)

    def _login(self, ctx: PluginContext, acc, base: str) -> None:
        user = str(getattr(acc, "username", "") or "")
        password = str(getattr(acc, "password", "") or "")
        if not user or not password:
            raise LoginRequired(hint=NEED_ACCOUNT_HINT)
        # the forum is cp1251: encode the form the way a browser on it would
        body = urlencode({"login_username": user, "login_password": password,
                          "autologin": "1", "redirect": "index.php",
                          "login": "Вход"},   # "Вход"
                         encoding="cp1251", errors="xmlcharrefreplace")
        try:
            r = ctx.client.post(base + "login.php", data=body,
                                headers={"content-type": "application/x-www-form-urlencoded",
                                         "referer": base},
                                timeout=getattr(ctx.settings, "grab_timeout_s", None))
        except PluginError:
            raise
        except Exception as e:
            raise PluginError(f"tapochek login failed: {e}")
        html = page_text(r)
        if self._logged_in(html):
            return
        if re.search(r"cap_code|captcha", html, re.I):
            raise LoginRequired(hint=("Tapochek asks for a captcha at login. Log in once in "
                                      "your browser, then import its cookies into the "
                                      "tapochek.net account (Settings, Accounts) and Retry."))
        raise LoginRequired(hint=("Tapochek did not accept the saved login. Check the user "
                                  "name and password of the tapochek.net account."))

    def _ensure_login(self, ctx: PluginContext, url: str, html: str = "") -> bool:
        """Log in when needed. Returns True when a login request was sent."""
        if html and self._logged_in(html):
            return False
        acc = self._account(ctx, url)
        if acc is None:
            raise LoginRequired(hint=NEED_ACCOUNT_HINT)
        base = self._base(url)
        self._use_saved_cookies(ctx, acc, base)
        if not html:
            if self._logged_in(page_text(_fetch(ctx, base + "index.php"))):
                return False
        self._login(ctx, acc, base)
        return True

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        return _remember(self, url, self._grab(url, ctx))

    def _grab(self, url: str, ctx: PluginContext) -> GrabResult:
        host = urlparse(url).netloc
        if self._kind(url) == "download":
            self._ensure_login(ctx, url)
            q = parse_qs(urlparse(url).query)
            fid = (q.get("id") or q.get("t") or ["file"])[0]
            return GrabResult(title=f"tapochek {fid}", filename=f"tapochek_{fid}.torrent",
                              site=host, plugin_id=self.id, direct_url=url,
                              direct_headers={"referer": self._base(url)})
        r = _fetch(ctx, url)
        html = page_text(r)
        base = str(r.url or url)
        magnet, torrent = self._links(html, base)
        if not magnet and not torrent:
            # guests see a "register to download" stub: log in and look again
            if self._ensure_login(ctx, url, html):
                html = page_text(_fetch(ctx, url))
                magnet, torrent = self._links(html, base)
        title = ""
        m = (re.search(r'<a[^>]+class="[^"]*\btt-text\b[^"]*"[^>]*>(.*?)</a>', html, re.S | re.I)
             or re.search(r"<h1[^>]*class=\"maintitle\"[^>]*>(.*?)</h1>", html, re.S | re.I)
             or re.search(r"<title>(.*?)</title>", html, re.S | re.I))
        if m:
            title = _clean_title(m.group(1))
            title = re.sub(r"\s*(?:::|\|)\s*Tapochek.*$", "", title, flags=re.I)
        child = magnet or torrent
        if not child:
            raise PluginError("no magnet or .torrent link on this tapochek page "
                              "(deleted topic, or not a torrent topic)")
        return GrabResult(title=title or url, site=host, plugin_id=self.id,
                          children=[[child, title]],
                          extra={"magnet": magnet, "torrent_url": torrent})

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        g = _recent(self, url) or self.grab(url, ctx)
        if g.direct_url:
            return ResolveResult(url=g.direct_url, filename=g.filename,
                                 headers=g.direct_headers)
        return ResolveResult(url="", children=list(g.children), extra=g.extra)


# ---------------------------------------------------------------------------
# 1337x (topic pages carry the magnet link)
# ---------------------------------------------------------------------------
X1337_HOSTS: tuple[str, ...] = (
    "1337x.to", "1337x.st", "1337x.ws", "1337x.eu", "1337x.se", "1337x.is", "1337x.gd",
    "x1337x.ws", "x1337x.eu", "x1337x.se", "x1337x.cc", "1337xx.to", "1337x.unblockit.download")


class X1337Plugin(SitePlugin):
    id = "1337x"
    name = "1337x"
    site = "1337x.to"
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "1337x topic pages (and mirrors) -> magnet child link."
    hosts = X1337_HOSTS

    TOPIC_RE = re.compile(r"^/torrent/(\d+)/")

    def supports(self, url: str) -> bool:
        return bool(_host_in(url, self.hosts) and self.TOPIC_RE.match(urlparse(url).path))

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        return _remember(self, url, self._grab(url, ctx))

    def _grab(self, url: str, ctx: PluginContext) -> GrabResult:
        html = page_text(_fetch(ctx, url))
        title = ""
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I) or \
            re.search(r"<title>(.*?)</title>", html, re.S | re.I)
        if m:
            title = _clean_title(re.sub(r"(?i)^download\s+|\s+torrent\s*\|.*$", "", m.group(1)))
        magnet = find_magnet(html)
        if not magnet:
            raise PluginError("no magnet link on this 1337x page")
        return GrabResult(title=title or url, site=urlparse(url).netloc, plugin_id=self.id,
                          children=[[magnet, title]], extra={"magnet": magnet})

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        g = _recent(self, url) or self.grab(url, ctx)
        return ResolveResult(url="", children=list(g.children), extra=g.extra)
