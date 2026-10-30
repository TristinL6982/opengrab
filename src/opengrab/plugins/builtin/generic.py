"""Generic page plugin: the universal grabber.

For hosts with no dedicated plugin, fetch the page (anti-bot client) and
extract:
  * og: / twitter / meta description for the title
  * <a href> links whose text/href looks like a download (file exts,
    'download' in text/href, magnet links)
  * inline <video>/<source> URLs and JSON "url" fields in embedded data
If the page itself looks like a file (content sniff), download it.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse, urljoin, unquote

from ..base import (SitePlugin, GrabResult, ResolveResult, PluginError,
                    CaptchaRequired, PluginContext)
from .direct import FILE_EXTS, _filename_from_cd
from ..dirindex import is_dir_index, parse_dir_index, split_entries


def _meta_title(html: str) -> str:
    for pat in (
        r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
        r'<meta[^>]+content="([^"]+)"[^>]+property="og:title"',
        r'<meta[^>]+property="og:title"[^>]+content=[\'"]([^\'"]+)[\'"]',
        r"<title[^>]*>([^<]+)</title>",
    ):
        m = re.search(pat, html, re.I | re.S)
        if m:
            t = m.group(1).strip()
            if t:
                return t[:300]
    return ""


def _extract_candidates(html: str, base: str) -> list[tuple[str, str]]:
    cands: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(href: str, label: str = "") -> None:
        if not href or href.startswith(("javascript:", "mailto:", "#", "data:")):
            return
        absu = urljoin(base, href.strip())
        if absu in seen:
            return
        seen.add(absu)
        cands.append((absu, label))

    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                         html, re.I | re.S):
        href, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        low = (href + " " + text).lower()
        if any(k in low for k in ("download", "get file", "get your file",
                                  "click here", "download now", "direct")):
            add(href, text or "download")
        elif any(low.endswith(e) or f"\"{e}" in low for e in FILE_EXTS):
            add(href, text)
    # magnet
    for m in re.finditer(r'magnet:\?xt=[^"\'<>\s]+', html):
        add(m.group(0), "magnet")
    # video/source/embed
    for m in re.finditer(r'<(?:video|source|embed)[^>]+src=["\']([^"\']+)["\']', html, re.I):
        add(m.group(1), "media")
    # blob urls inside embedded JSON (common in modern file hosts)
    for m in re.finditer(r'["\'`](https?:[^"\'`]+?\.(?:mp4|mkv|zip|rar|7z|pdf|iso|nfo|mp3|exe|apk|avi|mov|webm|flac|tar|gz|docx?|xlsx?|pptx?|iso|img|bin|rom|srt|ass|cbz|cbr))["\'`]', html, re.I):
        add(m.group(1), "embedded")
    return cands


class GenericPagePlugin(SitePlugin):
    id = "generic"
    name = "Generic page grabber"
    site = ""
    origin = "builtin"
    supports_captcha = True
    supports_login = False
    universal = True
    description = ("Falls back for any host: parses the page for download "
                   "links, embedded media and magnets.")

    def supports(self, url: str) -> bool:
        return True        # universal fallback

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        host = urlparse(url).netloc
        try:
            r = ctx.client.get(url, timeout=ctx.settings.grab_timeout_s)
        except CaptchaRequired:
            raise  # W2: a challenge page; the engine parks the link
        except Exception as e:
            raise PluginError(f"fetch failed: {e}")
        if r.status_code == 451 or r.status_code == 402:
            from ..base import PaywallRequired
            raise PaywallRequired(note=f"HTTP {r.status_code}: paid/restricted")
        html = r.text
        if r.status_code >= 400:
            raise PluginError(f"HTTP {r.status_code}")
        title = _meta_title(html)
        # captcha present?
        cap = ctx.extract_captcha(html, url)
        if cap[1] and not re.search(r'(?i)(captcha|recaptcha|hcaptcha|verify)', title or ""):
            # don't hard-block; just flag (engine may ask for manual solve)
            pass
        kids = _extract_candidates(html, str(r.url))
        # if the page itself is a file (e.g. .iso link redirected to binary)
        cd = r.headers.get("content-disposition", "")
        ct = r.headers.get("content-type", "").lower()
        if "attachment" in cd.lower() or "octet-stream" in ct:
            fname = _filename_from_cd(cd) or unquote(urlparse(str(r.url)).path.rsplit("/", 1)[-1])
            size = int(r.headers["content-length"]) if r.headers.get("content-length", "").isdigit() else None
            return GrabResult(title=title or fname, filename=fname, size=size,
                              site=host, plugin_id=self.id,
                              direct_url=str(r.url))
        # Check for directory index
        if is_dir_index(html):
            entries = parse_dir_index(html, str(r.url))
            files, subdirs = split_entries(entries)
            return GrabResult(title=title or url, site=host, plugin_id=self.id, children=[(u, name) for u, name in files[:500]], extra={"final_url": str(r.url), "dir_index": True, "subdirs": [u for u, _ in subdirs]})
        return GrabResult(
            title=title or url,
            site=host,
            plugin_id=self.id,
            children=[(u, label) for u, label in kids[:50]],
            needs_captcha=bool(cap[1]),
            extra={"final_url": str(r.url)},
        )

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        """Generic resolve: re-fetch and return the page (engine will list
        children to the user) — or, if the URL already points at a file,
        return it directly."""
        g = self.grab(url, ctx)
        if g.direct_url:
            return ResolveResult(url=g.direct_url, filename=g.filename,
                                 size=g.size)
        raise PluginError(
            "page has child links; pick one (or add a site plugin)")
