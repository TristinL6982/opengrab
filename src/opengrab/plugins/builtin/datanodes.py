"""DataNodes (datanodes.to) single-file links.

Share links are ``https://datanodes.to/<file_code>/<filename>``. Two ways in:

* With an API key (free from the DataNodes account page, stored as an
  OpenGrab account for ``datanodes.to`` with the key in ``data["api_key"]`` or
  as the password): ``/api/file/direct_link?file_code=..&key=..`` answers
  ``{"status": 200, "result": {"url": ..., "size": ...}}``. The site makes
  this premium-only on some accounts; a refusal falls back to the page flow.
* The page flow a visitor sees: the share link answers 302 to ``/download``
  and sets a ``file_code`` cookie. That page is a Vue app: the "Free
  Download" form (``op=download1``/``download2``, ``id``, ``rand``,
  ``method_free``) is gated by a short countdown and a Cloudflare Turnstile
  widget. The plugin waits out a visible countdown of up to 90 s, sends the
  Turnstile sitekey to the solver chain (``ctx.solve_captcha``) and posts the
  form exactly once; the answer is JSON ``{"url": ...}`` or a redirect to the
  file server.

Turnstile on this host refuses headless browsers, so a solver that cannot
produce a token leaves the link for the user (CaptchaRequired). When the page
has neither a form nor a sitekey the plugin raises "needs the browser
download flow".
"""
from __future__ import annotations

import html as _html
import json
import re
import time
from urllib.parse import quote, unquote, urlsplit

from ..base import GrabResult, PluginContext, PluginError, ResolveResult, SitePlugin

HOST = "datanodes.to"
BASE = "https://datanodes.to"
CODE_RE = re.compile(r"^[A-Za-z0-9]{8,20}$")
RESERVED = {"download", "premium", "login", "account", "api", "upload", "app",
            "faq", "tos", "contact", "register", "dmca", "privacy"}
DEAD_RE = re.compile(r"file not found|file was deleted|no such file|file expired|has been removed",
                     re.I)
MAX_WAIT = 90
sleep = time.sleep          # tests replace this


def parse(url: str) -> tuple[str, str] | None:
    """(file_code, filename) for a DataNodes share link, else None."""
    p = urlsplit(url.strip())
    host = p.netloc.lower()
    if p.scheme not in ("http", "https") or host not in (HOST, "www." + HOST):
        return None
    parts = [x for x in p.path.split("/") if x]
    if not parts or parts[0].lower() in RESERVED or not CODE_RE.match(parts[0]):
        return None
    return parts[0], unquote(parts[1]) if len(parts) > 1 else ""


def _attr(page: str, name: str, prop: bool = False) -> str:
    """Value of a form input or a Vue prop (``:name="'x'"``); with ``prop``
    also of a plain attribute (``name="x"``)."""
    pats = [rf'<input[^>]*name="{name}"[^>]*value="([^"]*)"',
            rf'<input[^>]*value="([^"]*)"[^>]*name="{name}"',
            rf':{name}="\'([^\']*)\'"']
    if prop:
        pats.append(rf'(?<![\w:-]){name}="([^"]*)"')
    for pat in pats:
        m = re.search(pat, page, re.I)
        if m:
            return _html.unescape(m.group(1))
    return ""


def countdown(page: str) -> int:
    m = (re.search(r':(?:seconds|countdown|timer)="(\d+)"', page, re.I)
         or re.search(r'id="countdown"[^>]*>\s*(?:<[^>]+>\s*)*(\d+)', page, re.I))
    return int(m.group(1)) if m else 0


def api_key(ctx: PluginContext) -> str:
    acc = ctx.account_for(site=HOST) if ctx is not None else None
    if acc is None:
        return ""
    data = getattr(acc, "data", None) or {}
    return str(data.get("api_key") or getattr(acc, "password", "") or "").strip()


class DataNodesPlugin(SitePlugin):
    id = "datanodes"
    name = "DataNodes"
    site = HOST
    origin = "builtin"
    supports_captcha = True
    supports_login = True
    description = "datanodes.to file links (API key or the free download page with Turnstile)."

    def supports(self, url: str) -> bool:
        return parse(url) is not None

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        parsed = parse(url)
        if parsed is None:
            raise PluginError("not a DataNodes file link")
        code, name = parsed
        return GrabResult(title=name or code, filename=name, site=HOST, plugin_id=self.id)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        parsed = parse(url)
        if parsed is None:
            raise PluginError("not a DataNodes file link")
        code, name = parsed
        key = api_key(ctx)
        if key:
            res = self._api(code, key, name, ctx)
            if res is not None:
                return res
        return self._page_flow(code, name, ctx)

    # -- API key ------------------------------------------------------------
    def _api(self, code: str, key: str, name: str, ctx: PluginContext) -> ResolveResult | None:
        r = ctx.client.get(f"{BASE}/api/file/direct_link?file_code={quote(code)}&key={quote(key)}")
        try:
            body = r.json()
        except ValueError:
            return None
        status = body.get("status") if isinstance(body, dict) else None
        if status == 404 or "not found" in str(body.get("msg", "")).lower():
            raise PluginError("DataNodes file is offline (not found or deleted)")
        if status != 200:
            return None   # premium-only on this account, bad key: use the page flow
        result = body.get("result") or {}
        link = str(result.get("url") or "")
        if not link:
            return None
        size = result.get("size")
        return ResolveResult(url=link, filename=name, size=int(size) if size else None,
                             headers={"referer": BASE + "/"})

    # -- the page a visitor sees ----------------------------------------------
    def _page_flow(self, code: str, name: str, ctx: PluginContext) -> ResolveResult:
        share = f"{BASE}/{code}/{quote(name)}" if name else f"{BASE}/{code}"
        r = ctx.client.get(share)
        page = r.text
        sitekey = _attr(page, "data-sitekey", True) or _attr(page, "sitekey", True)
        ops = re.findall(r'<input[^>]*name="op"[^>]*value="(download\d?)"', page, re.I)
        op = ops[0] if ops else ""
        if r.status_code == 404 or (not op and not sitekey and DEAD_RE.search(page)):
            raise PluginError("DataNodes file is offline (not found or deleted)")
        if r.status_code >= 400:
            raise PluginError(f"DataNodes page answered HTTP {r.status_code}")
        page_url = r.url or f"{BASE}/download"
        if not op and not sitekey:
            raise PluginError("DataNodes needs the browser download flow "
                              "(no download form on the page)")
        if not name:
            name = _attr(page, "file-name", True) or _attr(page, "fname")
            name = "" if name.lower() == "download" else name
        wait = countdown(page)
        if wait > MAX_WAIT:
            raise PluginError(f"the host asks to wait {wait // 60 or 1} minutes")
        form = {"op": "download2" if op in ("", "download1") else op,
                "id": _attr(page, "id") or code,
                "rand": _attr(page, "rand"),
                "referer": _attr(page, "referer"),
                "method_free": "Free Download >>",
                "method_premium": "",
                "dl": "1"}
        if sitekey:
            form["cf-turnstile-response"] = ctx.solve_captcha(kind="turnstile", sitekey=sitekey,
                                                              site_url=page_url)
        if wait:
            sleep(wait)
        headers = {"referer": page_url, "origin": BASE,
                   "cookie": f"file_code={code}; lang=english"}
        r2 = ctx.client.post(f"{BASE}/download", data=form, headers=headers, follow=False)
        link = self._link(r2)
        if not link:
            text = r2.text.lower()
            if "wrong captcha" in text or "verification failed" in text:
                raise PluginError("DataNodes refused the captcha answer")
            if re.search(r"you have to wait|download limit|reached the limit", text):
                raise PluginError("DataNodes download limit reached; the host asks to wait")
            raise PluginError("DataNodes needs the browser download flow "
                              f"(form answered HTTP {r2.status_code} without a link)")
        return ResolveResult(url=link, filename=name, headers={"referer": BASE + "/"})

    @staticmethod
    def _link(r) -> str:
        loc = (r.headers.get("location") or "").strip()
        if loc.startswith("http") and "datanodes.to/download" not in loc:
            return loc
        try:
            body = json.loads(r.text)
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("url"):
            return unquote(str(body["url"]))
        m = re.search(r'href="(https?://[^"]*datanodes\.to/d/[^"]+)"', r.text)
        return _html.unescape(m.group(1)) if m else ""
