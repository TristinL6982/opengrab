"""GoFile (gofile.io) folders and files.

With a GoFile premium account (its API token added under Accounts) the
listing comes straight from the official API (gofile.io/api, premium-only).
Free visitors get listings only through GoFile's own web client, which signs
each request with a token generated in the page. So OpenGrab opens the folder page in a real
browser window (see opengrab.browserpage), lets GoFile's own code fetch the
listing, and reads it back: names, sizes, MD5 checksums and the download
links on GoFile's storage servers. Downloads then use the visitor's
``accountToken`` cookie, as the browser would.

One file in the folder: it is downloaded directly. Several files or
subfolders: each becomes its own download (``https://gofile.io/d/<code>#file=<id>``),
served from a short-lived listing cache so the browser opens once per folder.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable, Optional

from ..base import GrabResult, LoginRequired, PaywallRequired, PluginError, ResolveResult, SitePlugin

URL_RE = re.compile(r"^https?://(?:www\.)?gofile\.io/d/([A-Za-z0-9-]+)/?(?:#file=([0-9A-Za-z-]+))?$")
CACHE_SECONDS = 30 * 60

# GoFile's own client modules, run inside the page (a visitor's browser does the same).
LIST_JS = """(async () => {
  const contents = await import('/js/services/contents.js');
  const token = (document.cookie.match(/(?:^|; )accountToken=([^;]+)/) || [])[1];
  if (!token) return {error: 'no-token'};
  try {
    const r = await contents.getFolder(token, %s, {pageSize: 1000});
    return {data: (r && r.data) || r, token: token};
  } catch (e) {
    return {error: String((e && (e.apiStatus || e.message)) || e)};
  }
})()"""


def _default_page():
    from ...browserpage import BrowserPage
    return BrowserPage()


PAGE_FACTORY: Callable = _default_page   # tests replace this
_cache: dict = {}
_cache_lock = threading.Lock()


def parse(url: str) -> tuple[Optional[str], Optional[str]]:
    m = URL_RE.match(url.strip())
    return (m.group(1), m.group(2)) if m else (None, None)


def fetch_listing(code: str, clock=time.time) -> dict:
    """{"data": <folder>, "token": ..., "ua": ...}, cached per folder."""
    with _cache_lock:
        hit = _cache.get(code)
        if hit and clock() - hit["at"] < CACHE_SECONDS:
            return hit
        import json as _json
        with PAGE_FACTORY() as page:
            page.goto(f"https://gofile.io/d/{code}")
            page.wait_for("document.cookie.includes('accountToken=')", timeout=30)
            res = page.evaluate(LIST_JS % _json.dumps(code), timeout=60) or {}
            ua = page.user_agent()
        if res.get("error"):
            err = str(res["error"])
            if "notPremium" in err:
                raise PaywallRequired("GoFile only lets premium accounts download this")
            if "notFound" in err or "404" in err:
                raise PluginError("this GoFile link does not exist or was deleted")
            raise PluginError(f"GoFile refused the listing: {err}")
        data = res.get("data") or {}
        if data.get("canAccess") is False:
            if data.get("password") or data.get("passwordStatus"):
                raise LoginRequired(hint="this GoFile folder is password protected")
            raise PluginError("GoFile does not allow access to this folder")
        hit = {"at": clock(), "data": data, "token": res.get("token", ""), "ua": ua}
        _cache[code] = hit
        return hit


def fetch_listing_api(code: str, token: str, client, clock=time.time) -> Optional[dict]:
    """Official API (gofile.io/api): GET /contents/{code} answers premium
    accounts directly. Returns None for non-premium tokens (use the website)."""
    key = f"api:{code}"
    with _cache_lock:
        hit = _cache.get(key)
        if hit and clock() - hit["at"] < CACHE_SECONDS:
            return hit
    r = client.get(f"https://api.gofile.io/contents/{code}?pageSize=1000&maxdepth=1",
                   headers={"authorization": f"Bearer {token}"}, detect_challenges=False)
    try:
        body = r.json()
    except ValueError:
        raise PluginError(f"GoFile's API answered HTTP {r.status_code}")
    status = str(body.get("status") or "")
    if status == "error-notPremium":
        return None
    if status == "error-token":
        raise LoginRequired(hint="GoFile rejected the API token in your account")
    if status == "error-notFound":
        raise PluginError("this GoFile link does not exist or was deleted")
    if status == "error-rateLimit":
        raise PluginError("GoFile's rate limit was reached; wait 5 minutes")
    if status != "ok":
        raise PluginError(f"GoFile's API refused the listing: {status or r.status_code}")
    data = body.get("data") or {}
    if data.get("canAccess") is False:
        raise LoginRequired(hint="this GoFile folder is password protected")
    hit = {"at": clock(), "data": data, "token": token, "ua": ""}
    with _cache_lock:
        _cache[key] = hit
    return hit


def _account_token(ctx) -> str:
    try:
        acc = ctx.account_for(site="gofile.io") if ctx is not None else None
    except Exception:
        acc = None
    if acc is None:
        return ""
    data = getattr(acc, "data", None) or {}
    return str(data.get("api_token") or data.get("api_key") or getattr(acc, "password", "") or "").strip()


def _download(item: dict, listing: dict) -> ResolveResult:
    if not item.get("link"):
        raise PluginError(f"GoFile gave no download link for {item.get('name', 'the file')}")
    headers = {"cookie": f"accountToken={listing['token']}", "referer": "https://gofile.io/"}
    if listing.get("ua"):
        headers["user-agent"] = listing["ua"]
    extra = {"expected_hash": "md5:" + item["md5"]} if re.fullmatch(r"[0-9a-f]{32}", str(item.get("md5", ""))) else {}
    return ResolveResult(url=item["link"], filename=str(item.get("name") or ""),
                         size=int(item["size"]) if item.get("size") else None, headers=headers, extra=extra)


class GoFilePlugin(SitePlugin):
    id = "gofile"
    name = "GoFile"
    site = "gofile.io"
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "GoFile folders and files (opens the folder once in your browser, then downloads directly)."

    def supports(self, url: str) -> bool:
        return parse(url)[0] is not None

    def grab(self, url, ctx) -> GrabResult:
        raise NotImplementedError  # resolve does it all; the browser opens only once

    def resolve(self, url, ctx) -> ResolveResult:
        code, file_id = parse(url)
        if code is None:
            raise PluginError("not a GoFile link")
        token = _account_token(ctx)
        listing = fetch_listing_api(code, token, ctx.client) if token else None
        if listing is None:
            listing = fetch_listing(code)  # free visitors: GoFile's own page in the browser
        data = listing["data"]
        children = list((data.get("children") or {}).values())
        if data.get("type") == "file":
            return _download(data, listing)
        if file_id:
            for item in children:
                if item.get("id") == file_id:
                    return _download(item, listing)
            raise PluginError("that file is no longer in the GoFile folder")
        files = [c for c in children if c.get("type") == "file"]
        folders = [c for c in children if c.get("type") == "folder"]
        if len(files) == 1 and not folders:
            return _download(files[0], listing)
        if not files and not folders:
            raise PluginError("this GoFile folder is empty")
        kids = [[f"https://gofile.io/d/{code}#file={f['id']}", str(f.get("name") or f["id"])] for f in files]
        kids += [[f"https://gofile.io/d/{f['code']}", str(f.get("name") or f["code"])] for f in folders if f.get("code")]
        return ResolveResult(url="", filename=str(data.get("name") or code), children=kids)
