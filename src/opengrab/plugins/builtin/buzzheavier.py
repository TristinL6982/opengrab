"""Buzzheavier (buzzheavier.com, bzzhr.co) file links.

Share links: ``https://buzzheavier.com/<id>`` (12 characters), also
``/d/<id>`` (the "{DownloadLink}" of Buzzheavier's custom landing pages),
on sub-domains such as ``ts.buzzheavier.com`` and with a ``?v=`` query.

Buzzheavier documents an API (buzzheavier.com/developers) for uploads and
the account's own file manager, but no download endpoint, so downloads use
the page: its "Download" button is an htmx element; a ``GET`` of
``/<id>/download`` with ``HX-Request: true`` answers with an ``HX-Redirect``
header naming the signed storage link (``https://trashbytes.net/d/<id>?v=...``,
formerly ``flashbang.sh``). That link is what the downloader fetches.

Storage links copied from the browser (``trashbytes.net/d/<id>?v=...``,
``flashbang.sh/d/<id>?v=...``) are claimed too: one ``HEAD`` checks them,
and an expired one gives "add the file's page link instead" with the
``buzzheavier.com/<id>`` page.
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import urljoin, urlsplit

from ..base import GrabResult, PluginContext, PluginError, ResolveResult, SitePlugin
from ._hostflow import EXPIRED, host_of, parse_size, probe_direct, text

SITE = "buzzheavier.com"
BASE = "https://buzzheavier.com"
PAGE_DOMAINS = ("buzzheavier.com", "bzzhr.co")
STORAGE_DOMAINS = ("trashbytes.net", "flashbang.sh")
NOT_PAGE_SUBS = ("w",)            # w.buzzheavier.com is the upload endpoint
ID_RE = re.compile(r"^/(?:d/|f/)?([a-z0-9]{12})(?:/download)?/?$", re.I)
STORE_RE = re.compile(r"^/d/([a-z0-9]{12})/?$", re.I)
RESERVED = {"pricing", "developers", "speedtest", "help", "privacy", "contact"}


def parse(url: str) -> tuple[str, str] | None:
    """("page", id) for a share link, ("storage", id) for a signed link."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None
    host = (p.hostname or "").lower()
    for d in STORAGE_DOMAINS:
        if host == d or host.endswith("." + d):
            m = STORE_RE.match(p.path)
            return ("storage", m.group(1)) if m else None
    for d in PAGE_DOMAINS:
        if host == d or (host.endswith("." + d) and host[: -len(d) - 1] not in NOT_PAGE_SUBS):
            m = ID_RE.match(p.path)
            if m and m.group(1).lower() not in RESERVED:
                return "page", m.group(1)
    return None


def page_meta(page: str) -> tuple[str, int | None]:
    name = ""
    for pat in (r'<meta\s+(?:name|property)="(?:og:)?title"\s+content="([^"]+)"',
                r"<title>\s*([^<]+?)\s*</title>",
                r'<h1[^>]*>\s*([^<]+?)\s*</h1>'):
        m = re.search(pat, page or "", re.I)
        if m:
            v = _html.unescape(m.group(1)).strip()
            if v.lower() not in ("buzzheavier.com", "buzzheavier", "not found", "bzzhr.co"):
                name = v
                break
    size = None
    m = re.search(r"Size\s*[:\-]?\s*([\d.,]+\s*[KMGT]?i?B)", text(page), re.I)
    if m:
        size = parse_size(m.group(1))
    return name, size


class BuzzheavierPlugin(SitePlugin):
    id = "buzzheavier"
    name = "Buzzheavier"
    site = SITE
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "buzzheavier.com / bzzhr.co file links and their trashbytes.net storage links."

    def supports(self, url: str) -> bool:
        return parse(url) is not None

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        parsed = parse(url)
        if parsed is None:
            raise PluginError("not a Buzzheavier link")
        return GrabResult(title=parsed[1], site=SITE, plugin_id=self.id)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        parsed = parse(url)
        if parsed is None:
            raise PluginError("not a Buzzheavier link")
        kind, fid = parsed
        if kind == "storage":
            try:
                return probe_direct(url, ctx, {"referer": BASE + "/"},
                                    is_site_page=lambda u: host_of(u) in PAGE_DOMAINS
                                    or host_of(u).startswith("ww"))
            except PluginError as e:
                if str(e).startswith(EXPIRED):
                    raise PluginError(f"Buzzheavier: {EXPIRED} ({BASE}/{fid})")
                raise
        page_url = f"{BASE}/{fid}"
        r = ctx.client.get(page_url)
        if r.status_code in (404, 410):
            raise PluginError("Buzzheavier file is offline (not found, deleted or expired)")
        if r.status_code >= 400:
            raise PluginError(f"Buzzheavier page answered HTTP {r.status_code}")
        name, size = page_meta(r.text)
        link = self._hx_link(ctx, page_url)
        if not link:
            why = (" (it shows a Turnstile check: open it in the browser)"
                   if "cf-turnstile" in r.text and "data-sitekey" in r.text else "")
            raise PluginError("Buzzheavier gave no download link" + why)
        return ResolveResult(url=link, filename=name, size=size, headers={"referer": BASE + "/"})

    @staticmethod
    def _hx_link(ctx, page_url: str) -> str:
        headers = {"hx-request": "true", "hx-current-url": page_url, "referer": page_url}
        r = ctx.client.get(page_url + "/download", headers=headers, follow=False)
        if r.status_code in (404, 410):
            raise PluginError("Buzzheavier file is offline (not found, deleted or expired)")
        for h in ("hx-redirect", "location"):
            v = (r.headers.get(h) or "").strip()
            if not v:
                continue
            link = urljoin(page_url, v)
            p = urlsplit(link)
            if (p.hostname or "").lower() in PAGE_DOMAINS and p.path.startswith("/d/"):
                # the site's own download route: it redirects to the storage server
                r2 = ctx.client.request("HEAD", link, headers={"referer": page_url}, follow=False)
                loc = (r2.headers.get("location") or "").strip()
                if r2.status_code in (301, 302, 303, 307, 308) and loc:
                    return urljoin(link, loc)
                return link
            if (p.hostname or "").lower() in PAGE_DOMAINS and parse(link) is not None:
                continue            # sent back to the file page
            if (p.hostname or "").lower() in PAGE_DOMAINS and p.path in ("", "/"):
                continue
            return link
        return ""
