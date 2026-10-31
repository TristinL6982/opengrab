"""GoFile storage-server links copied from the browser.

GoFile's download buttons point at ``https://file-<region>.gofile.io/download/web/<uuid>/<name>``
(older: ``store<N>.gofile.io`` / ``store-<region>.gofile.io``, also without
``/web``). The storage servers check the visitor's ``accountToken`` cookie.
A link copied out of the browser arrives without that cookie, so:

1. ``HEAD`` the link as is (no body download).
2. Refused (401/403)? Get a guest token from GoFile's documented account
   endpoint (``POST https://api.gofile.io/accounts``, the same call
   GoFile's own site makes for every visitor), cached for an hour, and
   ``HEAD`` again with ``cookie: accountToken=<token>``.
3. Still refused: the server only serves this file to the visitor who opened
   the folder, and the message asks for the folder link
   (``https://gofile.io/d/<code>``), which the "gofile" plugin handles.

An account token stored for gofile.io (Accounts, ``api_token``) is used
instead of a guest token.
"""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import urlsplit

from ..base import GrabResult, PluginContext, PluginError, ResolveResult, SitePlugin
from ._hostflow import EXPIRED, name_from_url, probe_direct

HOST_RE = re.compile(r"^(?:file-[a-z0-9-]+|store[a-z0-9-]*)\.gofile\.io$")
PATH_RE = re.compile(r"^/download/(?:web/)?([0-9a-f-]{36})/[^/]+$", re.I)
ACCOUNTS_API = "https://api.gofile.io/accounts"
TOKEN_SECONDS = 60 * 60
ADD_FOLDER = ("GoFile's server refused this copied download link; add the folder link "
              "(https://gofile.io/d/<code>) instead")
GONE = "this GoFile file does not exist anymore (or the link expired); add the folder link (https://gofile.io/d/<code>) instead"

_guest: dict = {}
_guest_lock = threading.Lock()


def parse(url: str) -> str | None:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None
    if not HOST_RE.match((p.hostname or "").lower()):
        return None
    m = PATH_RE.match(p.path)
    return m.group(1).lower() if m else None


def _stored_token(ctx) -> str:
    try:
        acc = ctx.account_for(site="gofile.io") if ctx is not None else None
    except Exception:
        acc = None
    if acc is None:
        return ""
    data = getattr(acc, "data", None) or {}
    return str(data.get("api_token") or data.get("api_key") or getattr(acc, "password", "") or "").strip()


def guest_token(client, clock=time.time) -> str:
    """A visitor token from GoFile's account endpoint (cached)."""
    with _guest_lock:
        hit = _guest.get("token")
        if hit and clock() - hit[1] < TOKEN_SECONDS:
            return hit[0]
    r = client.post(ACCOUNTS_API, json_body={}, detect_challenges=False)
    try:
        body = r.json()
    except ValueError:
        body = {}
    token = str(((body or {}).get("data") or {}).get("token") or "")
    if (body or {}).get("status") != "ok" or not token:
        raise PluginError(f"{ADD_FOLDER} (GoFile gave no visitor token: HTTP {r.status_code})")
    with _guest_lock:
        _guest["token"] = (token, clock())
    return token


class GoFileStoragePlugin(SitePlugin):
    id = "gofilestore"
    name = "GoFile storage links"
    site = "gofile.io"
    origin = "builtin"
    dedicated = True
    supports_captcha = False
    supports_login = False
    description = "Download links copied from GoFile (file-*.gofile.io / store*.gofile.io)."

    def supports(self, url: str) -> bool:
        return parse(url) is not None

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        if parse(url) is None:
            raise PluginError("not a GoFile storage link")
        name = name_from_url(url)
        return GrabResult(title=name or url, filename=name, site="gofile.io", plugin_id=self.id)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        if parse(url) is None:
            raise PluginError("not a GoFile storage link")
        base = {"referer": "https://gofile.io/"}
        token = _stored_token(ctx)
        if not token:
            try:
                return self._probe(url, ctx, base)
            except PluginError as e:
                if "refused" not in str(e):
                    raise
            token = guest_token(ctx.client)
        return self._probe(url, ctx, dict(base, cookie=f"accountToken={token}"), final=True)

    @staticmethod
    def _probe(url, ctx, headers, final: bool = False) -> ResolveResult:
        try:
            return probe_direct(url, ctx, headers,
                                is_site_page=lambda u: urlsplit(u).hostname in ("gofile.io", "www.gofile.io"))
        except PluginError as e:
            msg = str(e)
            if "refused" in msg:
                if final:
                    raise PluginError(ADD_FOLDER)
                raise
            if msg.startswith(EXPIRED):
                raise PluginError(GONE)
            raise
