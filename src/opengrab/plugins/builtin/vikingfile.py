"""VikingFile (vikingfile.com, file pages served from vik1ngfile.site).

Share links are ``https://vikingfile.com/f/<hash>`` (the site redirects them to
``https://vik1ngfile.site/f/<hash>``); ``/d/...`` links are folder pages.

* ``POST /api/check-file`` with ``hash=<hash>`` is the site's documented
  public API: it says whether the file exists and gives its name and size.
* The file page shows a Cloudflare Turnstile widget. The page's own script
  posts ``cf-turnstile-response=<token>`` back to the page URL and receives
  JSON ``{"link": <direct url>}`` or ``{"error": "..."}``. The plugin does the
  same with a token from the solver chain (``ctx.solve_captcha``); without a
  solver the engine asks the user (CaptchaRequired).
* Folder pages: every ``/f/<hash>`` link on the page becomes its own download.
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import urlsplit

from ..base import GrabResult, PluginContext, PluginError, ResolveResult, SitePlugin

API = "https://vikingfile.com/api"
HASH = r"[A-Za-z0-9]{6,32}"
FILE_RE = re.compile(rf"^/f/({HASH})/?$")
FOLDER_RE = re.compile(r"^/d/([A-Za-z0-9_\-]{4,64})(?:/.*)?$")


def _host_ok(host: str) -> bool:
    host = host.lower()
    if host in ("vikingfile.com", "www.vikingfile.com"):
        return True
    return re.fullmatch(r"(?:www\.)?vik1ngfile\.[a-z]{2,10}", host) is not None


def parse(url: str) -> tuple[str, str] | None:
    """("file", hash) or ("folder", id) for a VikingFile link, else None."""
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https") or not _host_ok(p.netloc):
        return None
    m = FILE_RE.match(p.path)
    if m:
        return "file", m.group(1)
    m = FOLDER_RE.match(p.path)
    if m:
        return "folder", m.group(1)
    return None


def check_file(ctx: PluginContext, file_hash: str) -> dict:
    """{"exist": bool, "name": str, "size": int|None}; exist None if unknown."""
    try:
        body = ctx.client.post(f"{API}/check-file", data={"hash": file_hash}).json()
    except Exception:
        return {"exist": None, "name": "", "size": None}
    if isinstance(body, list):
        body = body[0] if body else {}
    if not isinstance(body, dict):
        return {"exist": None, "name": "", "size": None}
    try:
        size = int(body.get("size")) or None
    except (TypeError, ValueError):
        size = None
    return {"exist": body.get("exist"), "name": str(body.get("name") or ""), "size": size}


def find_sitekey(page: str) -> str:
    m = (re.search(r'data-sitekey="([^"]+)"', page)
         or re.search(r'sitekey\s*:\s*["\']([^"\']+)["\']', page))
    return m.group(1) if m else ""


class VikingFilePlugin(SitePlugin):
    id = "vikingfile"
    name = "VikingFile"
    site = "vikingfile.com"
    origin = "builtin"
    supports_captcha = True
    supports_login = False
    description = "vikingfile.com file links (/f/<hash>, Turnstile) and folder pages (/d/...)."

    def supports(self, url: str) -> bool:
        return parse(url) is not None

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        parsed = parse(url)
        if parsed is None:
            raise PluginError("not a VikingFile link")
        kind, key = parsed
        if kind == "folder":
            title, children = self._folder(url, ctx)
            return GrabResult(title=title or key, site=self.site, plugin_id=self.id,
                              children=children)
        info = check_file(ctx, key)
        if info["exist"] is False:
            raise PluginError("VikingFile file is offline (not found or deleted)")
        return GrabResult(title=info["name"] or key, filename=info["name"], size=info["size"],
                          site=self.site, plugin_id=self.id)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        parsed = parse(url)
        if parsed is None:
            raise PluginError("not a VikingFile link")
        kind, key = parsed
        if kind == "folder":
            _title, children = self._folder(url, ctx)
            if not children:
                raise PluginError("VikingFile folder is empty or offline")
            return ResolveResult(url="", children=children)
        info = check_file(ctx, key)
        if info["exist"] is False:
            raise PluginError("VikingFile file is offline (not found or deleted)")
        r = ctx.client.get(f"https://vikingfile.com/f/{key}")
        page = r.text
        if r.status_code == 404 or re.search(r"<title>\s*File not found", page, re.I):
            raise PluginError("VikingFile file is offline (not found or deleted)")
        if r.status_code >= 400:
            raise PluginError(f"VikingFile page answered HTTP {r.status_code}")
        page_url = r.url or f"https://vikingfile.com/f/{key}"
        sitekey = find_sitekey(page)
        if not sitekey:
            raise PluginError("VikingFile needs the browser download flow "
                              "(no captcha widget on the page)")
        token = ctx.solve_captcha(kind="turnstile", sitekey=sitekey, site_url=page_url)
        r2 = ctx.client.post(page_url, data={"cf-turnstile-response": token},
                             headers={"referer": page_url,
                                      "content-type": "application/x-www-form-urlencoded"})
        try:
            body = r2.json()
        except ValueError:
            raise PluginError(f"VikingFile answered HTTP {r2.status_code} without a link")
        if not isinstance(body, dict):
            body = {}
        link = str(body.get("link") or "")
        if not link:
            err = str(body.get("error") or "no link in the answer")
            if re.search(r"captcha", err, re.I):
                raise PluginError("VikingFile refused the captcha answer")
            raise PluginError(f"VikingFile: {err}")
        name = info["name"] or _title(page)
        return ResolveResult(url=link, filename=name, size=info["size"],
                             headers={"referer": page_url})

    def _folder(self, url: str, ctx: PluginContext) -> tuple[str, list]:
        r = ctx.client.get(url)
        page = r.text
        if r.status_code == 404 or re.search(r"<title>\s*File not found", page, re.I):
            raise PluginError("VikingFile folder is offline (not found or deleted)")
        children, seen = [], set()
        for m in re.finditer(rf'href="(?:https?://[^/"]+)?/f/({HASH})"[^>]*>(.*?)</a>', page,
                             re.I | re.S):
            h = m.group(1)
            if h in seen:
                continue
            seen.add(h)
            label = _html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            children.append([f"https://vikingfile.com/f/{h}", label or h])
        return _title(page), children


def _title(page: str) -> str:
    m = re.search(r"<title>([^<]+)</title>", page, re.I)
    t = _html.unescape(m.group(1)).strip() if m else ""
    return "" if t.lower() in ("", "file not found", "upload", "vikingfile") else t
