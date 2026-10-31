"""FuckingFast (fuckingfast.co / fuckingfast.net) single-file links.

Share links look like ``https://fuckingfast.co/<id>#<filename>``; the file name
travels in the URL fragment. The page's "DOWNLOAD" button is an htmx element
with ``hx-post="/f/<id>/go"``. Pressing it posts there (``HX-Request: true``)
and the server answers with an ``HX-Redirect`` header naming the direct
``https://dl.fuckingfast.co/dl/...`` link, which stays valid for 24 hours.
Older pages had the link inline in ``window.open("...")``; that is still read.

When the page carries a Cloudflare Turnstile widget and the server refuses the
post without a token, the widget's sitekey goes to the solver chain
(``ctx.solve_captcha``) and the post is repeated with the answer.

Direct ``dl.fuckingfast.co/dl/...`` links are passed through unchanged.
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import unquote, urljoin, urlsplit

from ..base import GrabResult, PluginContext, PluginError, ResolveResult, SitePlugin

DOMAINS = ("fuckingfast.co", "fuckingfast.net")
ID_RE = re.compile(r"^/([A-Za-z0-9]{6,32})/?$")
DL_RE = re.compile(r"^/dl/[A-Za-z0-9_\-]+")
INLINE_RE = re.compile(r'window\.open\(\s*["\'](https://(?:dl\.)?fuckingfast\.(?:co|net)/dl/[^"\']+)["\']')
DEAD_MARK = "file not found or deleted"


def _host_ok(host: str) -> bool:
    return any(host == d or host == "www." + d for d in DOMAINS)


def parse(url: str) -> tuple[str, str] | None:
    """("page", id) for a share link, ("direct", url) for a dl link, else None."""
    p = urlsplit(url.strip())
    host = p.netloc.lower()
    if p.scheme not in ("http", "https"):
        return None
    if any(host == "dl." + d for d in DOMAINS) and DL_RE.match(p.path):
        return "direct", url.strip()
    if _host_ok(host):
        if DL_RE.match(p.path):
            return "direct", url.strip()
        m = ID_RE.match(p.path)
        if m and m.group(1).lower() not in ("help", "login", "privacy", "terms", "contact", "speedtest"):
            return "page", m.group(1)
    return None


def fragment_name(url: str) -> str:
    frag = urlsplit(url).fragment
    return unquote(frag) if frag and not frag.startswith("http") else ""


def parse_size(text: str) -> int | None:
    m = re.search(r"Size:\s*([\d.]+)\s*([KMGT]?)B", text or "", re.I)
    if not m:
        return None
    mult = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
    return int(float(m.group(1)) * mult[m.group(2).upper()])


def page_name(page: str) -> str:
    m = re.search(r"<title>([^<]+)</title>", page, re.I)
    title = _html.unescape(m.group(1)).strip() if m else ""
    return "" if title.lower() in ("", "fuckingfast", "not found") else title


class FuckingFastPlugin(SitePlugin):
    id = "fuckingfast"
    name = "FuckingFast"
    site = "fuckingfast.co"
    origin = "builtin"
    supports_captcha = True
    supports_login = False
    description = "fuckingfast.co share links (/<id>#name) and dl.fuckingfast.co direct links."

    def supports(self, url: str) -> bool:
        return parse(url) is not None

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        parsed = parse(url)
        if parsed is None:
            raise PluginError("not a FuckingFast link")
        name = fragment_name(url) or unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        return GrabResult(title=name, filename=name, site=self.site, plugin_id=self.id)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        parsed = parse(url)
        if parsed is None:
            raise PluginError("not a FuckingFast link")
        kind, value = parsed
        if kind == "direct":
            return ResolveResult(url=value, filename=fragment_name(url))
        fid = value
        p = urlsplit(url)
        origin = f"https://{p.netloc.lower()}"
        page_url = f"{origin}/{fid}"
        r = ctx.client.get(page_url)
        page = r.text
        if r.status_code == 404 or DEAD_MARK in page.lower():
            raise PluginError("FuckingFast file is offline (not found or deleted)")
        if r.status_code == 429 or re.search(r"rate limit", page, re.I):
            raise PluginError("FuckingFast rate limit; the host asks to wait a few minutes")
        if r.status_code >= 400:
            raise PluginError(f"FuckingFast page answered HTTP {r.status_code}")
        name = fragment_name(url) or page_name(page)
        size = parse_size(re.sub(r"<[^>]+>", " ", page))
        link = self._link(page, page_url, origin, fid, url, ctx)
        return ResolveResult(url=link, filename=name, size=size, headers={"referer": page_url})

    # ------------------------------------------------------------------------
    def _link(self, page: str, page_url: str, origin: str, fid: str, share_url: str,
              ctx: PluginContext) -> str:
        m = INLINE_RE.search(page)
        if m:
            return m.group(1)
        m = re.search(r'hx-post="([^"]*/go)"', page)
        go = urljoin(origin + "/", _html.unescape(m.group(1))) if m else f"{origin}/f/{fid}/go"
        headers = {"hx-request": "true", "hx-current-url": share_url, "origin": origin,
                   "referer": page_url, "content-type": "application/x-www-form-urlencoded"}
        r = ctx.client.post(go, data={}, headers=headers, follow=False)
        link = self._redirect(r)
        if link:
            return link
        if r.status_code == 429:
            raise PluginError("FuckingFast rate limit; the host asks to wait a few minutes")
        sitekey = re.search(r'data-sitekey="([^"]+)"', page)
        if sitekey and (r.status_code in (400, 401, 403) or "captcha" in r.text.lower()):
            token = ctx.solve_captcha(kind="turnstile", sitekey=sitekey.group(1), site_url=page_url)
            r = ctx.client.post(go, data={"cf-turnstile-response": token}, headers=headers,
                                follow=False)
            link = self._redirect(r)
            if link:
                return link
            raise PluginError("FuckingFast refused the captcha answer")
        if r.status_code == 404:
            raise PluginError("FuckingFast file is offline (not found or deleted)")
        raise PluginError("FuckingFast needs the browser download flow "
                          f"(download button answered HTTP {r.status_code} without a link)")

    @staticmethod
    def _redirect(r) -> str:
        target = (r.headers.get("hx-redirect") or r.headers.get("location") or "").strip()
        return target if "/dl/" in target else ""
