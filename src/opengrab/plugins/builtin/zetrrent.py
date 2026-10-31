"""zEtrrent (zetrrent.com) links: a host for user-uploaded ``.torrent`` files.

Links look like ``https://www.zetrrent.com/<code>/file`` (15 characters);
page titles read "zEtrrent.com — Download — <name>.torrent". The site's
terms say it stores only the torrent metadata, not the content. What a
visitor downloads is the small ``.torrent`` file (or a magnet link), which
OpenGrab's torrent support then handles.

The whole site sits behind a Cloudflare managed challenge. The HTTP client
reports that as a challenge (CaptchaRequired); the engine parks the link and
the user clears it in the browser, after which the clearance cookie is used
for the page requests here. Nothing is bypassed.

On the page the plugin takes, in order: a link to a ``.torrent`` file or a
``/download`` route of the same code, then a magnet link (returned as a
child link, so the torrent engine picks it up), then a download form
(posted once, with any captcha going to ``ctx.solve_captcha`` and a
countdown of up to 90 s waited out).
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import unquote, urljoin, urlsplit

from ..base import GrabResult, PluginContext, PluginError, ResolveResult, SitePlugin
from . import _hostflow as hf

SITE = "zetrrent.com"
BASE = "https://www.zetrrent.com"
CODE_RE = re.compile(r"^/([A-Za-z0-9]{8,32})(?:/(?:file|download)?)?/?$")
RESERVED = {"page", "blog", "login", "register", "upload", "account", "search", "api",
            "contact", "terms", "privacy", "dmca", "faq"}


def parse(url: str) -> str | None:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or (p.hostname or "").lower() not in (SITE, "www." + SITE):
        return None
    m = CODE_RE.match(p.path)
    if not m or m.group(1).lower() in RESERVED:
        return None
    return m.group(1)


def page_name(page: str) -> str:
    m = re.search(r"<title>\s*([^<]+?)\s*</title>", page or "", re.I)
    if not m:
        return ""
    title = _html.unescape(m.group(1))
    parts = [x.strip() for x in re.split(r"\s+[—–|-]\s+", title) if x.strip()]
    name = parts[-1] if len(parts) > 1 else ""
    return "" if name.lower() in ("download", "zetrrent.com") else name


def torrent_link(page: str, code: str, base: str) -> str:
    for m in re.finditer(r'href="([^"]+)"', page or ""):
        href = _html.unescape(m.group(1))
        low = href.lower()
        if low.startswith(("magnet:", "javascript:", "#")):
            continue
        path = urlsplit(urljoin(base, href)).path
        if path.lower().endswith(".torrent") or re.search(r"/%s/(?:download|dl|get)\b" % re.escape(code), path, re.I) \
                or re.search(r"/(?:download|dl|get)/%s\b" % re.escape(code), path, re.I):
            return urljoin(base, href)
    return ""


class ZetrrentPlugin(SitePlugin):
    id = "zetrrent"
    name = "zEtrrent"
    site = SITE
    origin = "builtin"
    supports_captcha = True
    supports_login = False
    description = "zetrrent.com .torrent file pages (the .torrent file or its magnet link)."

    def supports(self, url: str) -> bool:
        return parse(url) is not None

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        code = parse(url)
        if code is None:
            raise PluginError("not a zEtrrent link")
        return GrabResult(title=code, site=SITE, plugin_id=self.id)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        code = parse(url)
        if code is None:
            raise PluginError("not a zEtrrent link")
        page_url = f"{BASE}/{code}/file"
        r = ctx.client.get(page_url)       # a Cloudflare challenge raises CaptchaRequired
        if r.status_code in (404, 410):
            raise PluginError("zEtrrent file is offline (not found or deleted)")
        if r.status_code >= 400:
            raise PluginError(f"zEtrrent answered HTTP {r.status_code}")
        page = r.text
        name = page_name(page)
        link = torrent_link(page, code, page_url)
        if link:
            return ResolveResult(url=link, filename=name, headers={"referer": page_url})
        magnets = ctx.find_magnets(page)
        if magnets:
            m = _html.unescape(magnets[0])
            return ResolveResult(url="", filename=name, children=[[m, name or code]])
        for a, fields, body in hf.forms(page):
            action = urljoin(page_url, a.get("action") or page_url)
            if (a.get("method") or "get").lower() != "post" or hf.host_of(action) not in (SITE, "www." + SITE):
                continue
            if not re.search(r"download", body + " " + action, re.I):
                continue
            fields.update(hf.answer_captcha(ctx, hf.captcha(page, page_url), page_url))
            hf.wait_countdown(hf.countdown(page), "zEtrrent")
            r2 = ctx.client.post(action, data=fields, headers={"referer": page_url}, follow=False)
            loc = (r2.headers.get("location") or "").strip()
            if r2.status_code in hf.REDIRECTS and loc:
                nxt = urljoin(action, loc)
                if nxt.startswith("magnet:"):
                    return ResolveResult(url="", filename=name, children=[[nxt, name or code]])
                return ResolveResult(url=nxt, filename=name or unquote(hf.name_from_url(nxt)),
                                     headers={"referer": page_url})
            ct = (r2.headers.get("content-type") or "").lower()
            if "bittorrent" in ct or "octet-stream" in ct:
                raise PluginError("zEtrrent sends the .torrent only in answer to the form; "
                                  "the engine cannot replay a POST download")
            link = torrent_link(r2.text, code, action)
            if link:
                return ResolveResult(url=link, filename=name, headers={"referer": page_url})
            break
        if re.search(r"not found|removed|deleted|does not exist", hf.text(page)[:2000], re.I):
            raise PluginError("zEtrrent file is offline (not found or deleted)")
        raise PluginError("zEtrrent page has no .torrent link the plugin knows (the site may have changed)")
