"""Paste pages and multi-link pages -> one child link per download link.

PastePlugin (zpaste.net and similar paste sites): reads the paste text and
returns EVERY link in it (http/https/ftp/magnet, also plain-text URLs that
are not clickable) as children, so each link goes to its own plugin.

MultiLinkPlugin (universal fallback, after the generic grabber): a page that
lists hoster links (rapidgator, 1fichier, mega, ...) without "download" in
the link text or a file extension in the URL is not picked up by
generic.py, which only keeps download-looking anchors. When the generic
grabber cannot finish a page, this plugin collects the hoster, magnet, ftp
and file links on it, one child per link.

Add a paste site to PASTE_HOSTS or a file host to HOSTER_DOMAINS to extend.
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import urljoin, urlparse

from ..base import (GrabResult, PaywallRequired, PluginContext, PluginError,
                    ResolveResult, SitePlugin)
from .direct import FILE_EXTS
from ._hostflow import leaves_public_net
from .dlproxy import is_direct_host
from .trackers import page_text

PASTE_HOSTS: tuple[str, ...] = (
    "zpaste.net", "pastebin.com", "paste.ee", "pastes.io", "rentry.co", "rentry.org",
    "controlc.com", "justpaste.it", "dpaste.org", "dpaste.com", "paste2.org",
    "pastebin.pl", "paste.fo", "pastelink.net", "ghostbin.site",
)

HOSTER_DOMAINS: tuple[str, ...] = (
    "rapidgator.net", "rg.to", "nitroflare.com", "nitro.download", "1fichier.com",
    "uploaded.net", "ul.to", "turbobit.net", "hitfile.net", "filefactory.com",
    "mediafire.com", "mega.nz", "mega.co.nz", "ddownload.com", "ddl.to",
    "katfile.com", "uptobox.com", "dropbox.com", "drive.google.com", "pixeldrain.com",
    "gofile.io", "krakenfiles.com", "send.cm", "workupload.com", "fikper.com",
    "hexload.com", "filestore.me", "uploadhaven.com", "usersdrive.com",
    "clicknupload.click", "alfafile.net", "file.al", "k2s.cc", "keep2share.cc",
    "fboom.me", "tezfiles.com", "upload.ee", "sendspace.com", "4shared.com",
    "userscloud.com", "uploadrar.com", "dailyuploads.net", "filespace.com",
    "mixdrop.co", "buzzheavier.com", "qiwi.gg", "datanodes.to", "transfer.it",
    "wetransfer.com", "we.tl", "zpaste.net",
)

URL_RE = re.compile(r"""(?:https?|ftps?)://[^\s<>"'`\\]+|magnet:\?xt=[^\s<>"'`\\]+""", re.I)
HREF_RE = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*["']([^"']+)["']""", re.I)
# where paste sites keep the paste text
CONTENT_RE = re.compile(
    r"<(textarea|pre|code)\b[^>]*>(.*?)</\1>"
    r"|<(div|section|article)\b[^>]*(?:id|class)=[\"'][^\"']*"
    r"(?:paste|content|entry|text|raw|code)[^\"']*[\"'][^>]*>(.*?)</\3>",
    re.I | re.S)
TRAILING = ".,;:!?)]}>'\""
MAX_CHILDREN = 500


def _clean_url(u: str) -> str:
    u = _html.unescape(u.strip())
    while u and u[-1] in TRAILING:
        # keep a ")" that closes a "(" inside the URL (wikipedia style)
        if u[-1] == ")" and u.count("(") >= u.count(")"):
            break
        u = u[:-1]
    return u


def extract_links(text: str, base: str = "") -> list[str]:
    """Every link in a piece of html or plain text, in order, deduplicated:
    anchors (made absolute against ``base``) and bare URLs/magnets. Links to
    localhost or private addresses are dropped when ``base`` is public."""
    out: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        u = _clean_url(u)
        if not u or u.lower().startswith(("javascript:", "mailto:", "data:", "#")):
            return
        if base and not re.match(r"^(?:[a-z][a-z0-9+.-]*:)", u, re.I):
            u = urljoin(base, u)
        if not re.match(r"^(?:https?|ftps?)://|^magnet:\?", u, re.I):
            return
        if base and leaves_public_net(base, u):
            return   # a public page pointing at this computer or the LAN
        key = u.rstrip("/")
        if key not in seen:
            seen.add(key)
            out.append(u)

    found: list[tuple[int, str]] = []
    for m in HREF_RE.finditer(text):
        found.append((m.start(), m.group(1)))
    plain = _html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?i)<wbr\s*/?>", "", text)))
    for m in URL_RE.finditer(text):
        found.append((m.start(), m.group(0)))
    for m in URL_RE.finditer(plain):   # URLs split by tags, e.g. <wbr>
        found.append((10 ** 9 + m.start(), m.group(0)))
    for _, u in sorted(found, key=lambda x: x[0]):
        add(u)
    return out


def _host_matches(host: str, domains: tuple[str, ...]) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def is_download_link(u: str) -> bool:
    """A link worth its own download: magnet, ftp, a file host, a direct
    server or a URL ending in a file extension."""
    low = u.lower()
    if low.startswith(("magnet:", "ftp://", "ftps://")):
        return True
    p = urlparse(u)
    if _host_matches(p.hostname or "", HOSTER_DOMAINS) and p.path not in ("", "/"):
        return True
    if is_direct_host(u):
        return True
    return _has_file_ext(u)


def _has_file_ext(u: str) -> bool:
    name = urlparse(u).path.lower().rsplit("/", 1)[-1]
    return "." in name and "." + name.rsplit(".", 1)[-1] in FILE_EXTS


def _fetch(ctx: PluginContext, url: str):
    try:
        r = ctx.client.get(url, timeout=getattr(ctx.settings, "grab_timeout_s", None))
    except PluginError:
        raise            # CaptchaRequired (Cloudflare & co.) reaches the engine
    except Exception as e:
        raise PluginError(f"fetch failed: {e}")
    if r.status_code in (402, 451):
        raise PaywallRequired(note=f"HTTP {r.status_code}")
    if r.status_code >= 400:
        raise PluginError(f"HTTP {r.status_code}")
    return r


def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return re.sub(r"\s+", " ", _html.unescape(m.group(1))).strip()[:300] if m else ""


def _label(u: str) -> str:
    if u.lower().startswith("magnet:"):
        m = re.search(r"[?&]dn=([^&]+)", u)
        from urllib.parse import unquote_plus
        return unquote_plus(m.group(1))[:200] if m else "magnet"
    p = urlparse(u)
    return (p.path.rstrip("/").rsplit("/", 1)[-1] or p.hostname or u)[:200]


class PastePlugin(SitePlugin):
    id = "paste"
    name = "Paste pages"
    site = "zpaste.net"
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "zpaste.net and other paste sites: every link in the paste becomes a child."
    hosts = PASTE_HOSTS

    def supports(self, url: str) -> bool:
        p = urlparse(url)
        return (p.scheme in ("http", "https") and p.path not in ("", "/")
                and _host_matches(p.hostname or "", self.hosts))

    def paste_links(self, url: str, ctx: PluginContext) -> tuple[str, list[str]]:
        r = _fetch(ctx, url)
        final = str(r.url or url)
        text = page_text(r)
        ct = (r.headers.get("content-type") or "").lower()
        if ct.startswith("text/plain"):
            return "", extract_links(text, final)
        title = _title(text)
        own = (urlparse(final).hostname or "").lower()
        region = "\n".join((m.group(2) or m.group(4) or "") for m in CONTENT_RE.finditer(text))
        links = [u for u in extract_links(region, final)
                 if (urlparse(u).hostname or "").lower() != own or is_download_link(u)]
        if not links:
            # some sites only render the text via script: try the raw view
            m = re.search(r"""href=["']([^"']*/raw[^"']*)["']""", text, re.I)
            raw = urljoin(final, _html.unescape(m.group(1))) if m else ""
            if raw and urlparse(raw).hostname == urlparse(final).hostname and raw != final:
                rr = _fetch(ctx, raw)
                links = extract_links(page_text(rr), raw)
        if not links:
            # last resort: every outbound link on the page
            links = [u for u in extract_links(text, final)
                     if (urlparse(u).hostname or "").lower() != own or is_download_link(u)]
        return title, links

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        title, links = self.paste_links(url, ctx)
        if not links:
            raise PluginError("no links in this paste")
        return GrabResult(title=title or url, site=urlparse(url).netloc, plugin_id=self.id,
                          children=[[u, _label(u)] for u in links[:MAX_CHILDREN]])

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        g = self.grab(url, ctx)
        return ResolveResult(url="", children=list(g.children))


class MultiLinkPlugin(SitePlugin):
    id = "multilink"
    name = "Multi-link pages"
    site = ""
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    universal = True       # after the generic grabber (module order)
    description = ("Fallback for pages listing many hoster links: one child per "
                   "hoster, magnet, ftp or file link.")

    def supports(self, url: str) -> bool:
        return urlparse(url).scheme in ("http", "https")

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        r = _fetch(ctx, url)
        final = str(r.url or url)
        text = page_text(r)
        own = (urlparse(final).hostname or "").lower()
        links = [u for u in extract_links(text, final) if is_download_link(u)
                 and u.rstrip("/") != final.rstrip("/")
                 # the site's own navigation is not a download list
                 and ((urlparse(u).hostname or "").lower() != own
                      or u.lower().startswith("magnet:") or _has_file_ext(u))]
        if not links:
            raise PluginError("no hoster links on this page")
        return GrabResult(title=_title(text) or url, site=urlparse(url).netloc,
                          plugin_id=self.id,
                          children=[[u, _label(u)] for u in links[:MAX_CHILDREN]])

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        g = self.grab(url, ctx)
        return ResolveResult(url="", children=list(g.children))
