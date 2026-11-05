"""DataNodes (datanodes.to) single-file links.

Share links are ``https://datanodes.to/<file_code>/<filename>``. Two ways in:

* With an API key (free from the DataNodes account page, stored as an
  OpenGrab account for ``datanodes.to`` with the key in ``data["api_key"]`` or
  as the password): ``/api/file/direct_link?file_code=..&key=..`` answers
  ``{"status": 200, "result": {"url": ..., "size": ...}}``. The site makes
  this premium-only on some accounts; a refusal falls back to the page flow.
* The page flow a visitor sees. DataNodes runs XFileSharing, so the shared
  flow in ``_xfs`` does the work; this site's quirks are in ``PROFILE``:

  - every page request says it comes from ``https://datanodes.to/users``;
    without that referer the site refuses ("Not allowed from domain you're
    coming from");
  - the share link answers 302 to ``/download`` and sets a ``file_code``
    cookie; that page has the ``op=download1`` form;
  - the second step is drawn by JavaScript: ``rand="..."``, ``dl-token="..."``
    and ``countdown="N"`` are attributes, and the Turnstile widget sits
    escaped in ``captcha-html="..."``; the post needs ``g_captch__a=1``;
  - the answer is JSON ``{"url": ...}`` (URL-encoded) or a redirect.

Turnstile on this host refuses headless browsers, so a solver that cannot
produce a token leaves the link for the user (CaptchaRequired).
"""
from __future__ import annotations

import re
import time
from urllib.parse import quote, unquote, urlsplit

from ..base import GrabResult, PluginContext, PluginError, ResolveResult, SitePlugin
from . import _xfs

HOST = "datanodes.to"
BASE = "https://datanodes.to"
CODE_RE = re.compile(r"^[A-Za-z0-9]{8,20}$")
RESERVED = {"download", "premium", "login", "account", "api", "upload", "app",
            "faq", "tos", "contact", "register", "dmca", "privacy", "users"}
PROFILE = _xfs.Profile(name="DataNodes", referer=BASE + "/users", captcha_kind="turnstile",
                       download2={"g_captch__a": "1"})
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
        share = f"{BASE}/{code}/{quote(name)}" if name else f"{BASE}/{code}"
        return _xfs.resolve(share, ctx, PROFILE, code=code, name=name,
                            wait_fn=lambda s: sleep(s))

    # -- API key ------------------------------------------------------------
    def _api(self, code: str, key: str, name: str, ctx: PluginContext) -> ResolveResult | None:
        r = ctx.client.get(f"{BASE}/api/file/direct_link?file_code={quote(code)}&key={quote(key)}")
        try:
            body = r.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        status = body.get("status")
        if status == 404 or "not found" in str(body.get("msg", "")).lower():
            raise PluginError("DataNodes file is offline (not found or deleted)")
        if status != 200:
            return None   # premium-only on this account, bad key: use the page flow
        result = body.get("result")
        link = str(result.get("url") or "") if isinstance(result, dict) else ""
        if not link:
            return None
        size = str(result.get("size") or "")
        return ResolveResult(url=link, filename=name, size=int(size) if size.isdigit() else None,
                             headers={"referer": BASE + "/"})
