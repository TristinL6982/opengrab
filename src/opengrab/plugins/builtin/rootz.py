"""Rootz (rootz.so, also www.rootz.so) single-file links: ``/d/<shortId>``.

Flow, as the site's own file page runs it:

1. ``GET /d/<shortId>``: the server-rendered page carries a ``pageToken``
   prop for the download component (404 when the file does not exist).
2. ``GET /api/files/download-by-short?shortId=<id>`` with the header
   ``X-Page-Token: <pageToken>`` answers ``{"success": true, "data": {...}}``
   with ``fileId``, ``fileName``, ``size``, ``status``, ``downloadAllowed``,
   ``passwordProtected`` and ``remainingDownloads``.
3. The "Download" button sends the browser to
   ``/api/files/proxy-download/<fileId>``, which redirects to the signed
   storage URL. When the visitor is rate limited it redirects back to the file
   page with ``?error=rate_limited&retry_after=<seconds>`` instead; the plugin
   checks that with a HEAD request (no body) and waits short delays, or
   reports the wait.

Password-protected files need the password form in the browser.
"""
from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from ..base import GrabResult, PluginContext, PluginError, ResolveResult, SitePlugin

HOSTS = ("rootz.so", "www.rootz.so")
ID_RE = re.compile(r"^/d/([A-Za-z0-9_\-]{3,32})/?$")
TOKEN_RE = re.compile(r'pageToken\\?"\s*:\s*\\?"([^"\\]+)')
MAX_WAIT = 90
sleep = time.sleep          # tests replace this


def short_id(url: str) -> str | None:
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https") or p.netloc.lower() not in HOSTS:
        return None
    m = ID_RE.match(p.path)
    return m.group(1) if m else None


def page_token(page: str) -> str:
    m = TOKEN_RE.search(page)
    return m.group(1) if m else ""


class RootzPlugin(SitePlugin):
    id = "rootz"
    name = "Rootz"
    site = "rootz.so"
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "rootz.so file links (/d/<id>)."

    def supports(self, url: str) -> bool:
        return short_id(url) is not None

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        sid = short_id(url)
        if sid is None:
            raise PluginError("not a Rootz file link")
        data, _base = self._info(url, sid, ctx)
        return GrabResult(title=data["name"] or sid, filename=data["name"], size=data["size"],
                          site=self.site, plugin_id=self.id)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        sid = short_id(url)
        if sid is None:
            raise PluginError("not a Rootz file link")
        data, base = self._info(url, sid, ctx)
        page_url = f"{base}/d/{sid}"
        proxy = f"{base}/api/files/proxy-download/{quote(data['file_id'])}"
        for attempt in range(3):
            wait = self._rate_limit_wait(proxy, page_url, ctx)
            if wait is None:
                break
            if wait > MAX_WAIT or attempt == 2:   # no point sleeping without a re-check
                raise PluginError(f"Rootz rate limit: wait {max(1, -(-wait // 60))} minutes")
            sleep(max(wait, 1))
        return ResolveResult(url=proxy, filename=data["name"], size=data["size"],
                             headers={"referer": page_url})

    # ------------------------------------------------------------------------
    def _info(self, url: str, sid: str, ctx: PluginContext) -> tuple[dict, str]:
        base = f"https://{urlsplit(url).netloc.lower()}"
        page_url = f"{base}/d/{sid}"
        r = ctx.client.get(page_url)
        if r.status_code == 404:
            raise PluginError("Rootz file is offline (not found or deleted)")
        if r.status_code >= 400:
            raise PluginError(f"Rootz page answered HTTP {r.status_code}")
        token = page_token(r.text)
        if not token:
            raise PluginError("Rootz page has no download token (layout changed?)")
        r2 = ctx.client.get(f"{base}/api/files/download-by-short?shortId={quote(sid)}",
                            headers={"x-page-token": token, "referer": page_url,
                                     "accept": "application/json"})
        try:
            body = r2.json()
        except ValueError:
            raise PluginError(f"Rootz API answered HTTP {r2.status_code} without JSON")
        if not isinstance(body, dict):
            body = {}
        data = body.get("data")
        if not body.get("success") or not isinstance(data, dict) or not data.get("fileId"):
            err = str(body.get("error") or "no file data")
            if r2.status_code == 404 or "not found" in err.lower():
                raise PluginError("Rootz file is offline (not found or deleted)")
            raise PluginError(f"Rootz: {err}")
        status = str(data.get("status") or "active")
        if status == "deleted":
            raise PluginError("Rootz file is offline (deleted)")
        if status != "active":
            raise PluginError(f"Rootz file is {status}")
        if data.get("passwordProtected"):
            raise PluginError("Rootz file is password protected; download it in the browser")
        if data.get("remainingDownloads") == 0:
            raise PluginError("Rootz: this link has no downloads left")
        if data.get("downloadAllowed") is False:
            raise PluginError("Rootz does not allow downloading this file (owner limit)")
        try:
            size = int(data.get("size")) or None
        except (TypeError, ValueError):
            size = None
        return {"file_id": str(data["fileId"]), "name": str(data.get("fileName") or ""),
                "size": size}, base

    @staticmethod
    def _rate_limit_wait(proxy: str, page_url: str, ctx: PluginContext) -> int | None:
        """Seconds the host asks to wait, or None when the download may start."""
        r = ctx.client.request("HEAD", proxy, headers={"referer": page_url}, follow=False)
        loc = r.headers.get("location") or ""
        if r.status_code == 429:
            ra = str(r.headers.get("retry-after") or "60")
            return int(ra) if ra.isdigit() else 60
        if r.status_code == 404:
            raise PluginError("Rootz file is offline (not found or deleted)")
        if not loc:
            return None
        q = parse_qs(urlsplit(urljoin(proxy, loc)).query)
        err = (q.get("error") or [""])[0]
        if err == "rate_limited":
            try:
                return int((q.get("retry_after") or ["60"])[0])
            except ValueError:
                return 60
        if err == "404":
            raise PluginError("Rootz file is offline (not found or deleted)")
        if err == "503":
            raise PluginError("Rootz storage is temporarily unavailable; try again later")
        return None
