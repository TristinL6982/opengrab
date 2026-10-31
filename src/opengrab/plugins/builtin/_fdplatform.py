"""
Shared client for the Turbobit / Hitfile platform (same back end, same
single-page app). Not a plugin itself: the leading underscore keeps the
registry from scanning it, and the base class has no id.

The site's own JavaScript talks to a JSON API at https://app.<host>/api:

  GET  /captcha                 -> {"driver", "index", "publicKey"}
  POST /download/info           -> {"file": {...}, "premium", "needCaptcha",
                                    "downloadUrls", "premiumOnlyDownload"}
  POST /download/premium/dhc    -> {"downloadUrls": [...]} (premium + captcha)
  POST /download/free/init      -> {"file", "ipBan": {"delay"}, ...}
  POST /download/free/captcha   -> {"delay": seconds}
  POST /download/free/prepare   -> {"success": true}   (after the delay)
  POST /download/free/start     -> {"downloadUrl": "..."}
  POST /auth/login              -> session cookie (needs a captcha answer)

The plugin walks the same steps a browser user does: the captcha goes to
ctx.solve_captcha, the delay the server sets is waited (up to 90 s) and
longer waits are raised as "wait N minutes".
"""
from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlsplit

from ..base import (GrabResult, LoginRequired, PaywallRequired, PluginError,
                    ResolveResult, SitePlugin)

_sleep = time.sleep          # tests replace this
MAX_WAIT = 90

_RESERVED = {"download", "premium", "faq", "rules", "helpdesk", "linkchecker",
             "payments", "user", "login", "register", "files", "reseller",
             "ybrowser", "example", "api", "assets", "static", "upload",
             "folder", "abuse", "contacts", "news", "partner", "partners", "lang"}

_ID = r"[A-Za-z0-9]{5,24}"

CAPTCHA_KINDS = {"recaptcha2": "recaptcha", "recaptcha": "recaptcha",
                 "hcaptcha": "hcaptcha", "turnstile": "turnstile", "gregwar": "text"}


def _wait_text(seconds: int) -> str:
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600} hours"
    if seconds >= 60:
        return f"{max(1, round(seconds / 60))} minutes"
    return f"{seconds} seconds"


class FdPlatformPlugin(SitePlugin):
    """Base for Turbobit/Hitfile. Subclasses set the class attributes."""
    id = ""
    origin = "builtin"
    supports_captcha = True
    supports_login = True
    canonical = ""            # "turbobit.net"
    hosts: tuple = ()         # every domain the site answers on
    label = ""                # "Turbobit"

    # -- URLs -----------------------------------------------------------------
    def _host_ok(self, host: str) -> bool:
        host = host[4:] if host.startswith("www.") else host
        return host in self.hosts

    def file_id(self, url: str) -> str | None:
        try:
            p = urlsplit(url)
        except ValueError:
            return None
        if p.scheme not in ("http", "https") or not self._host_ok((p.hostname or "").lower()):
            return None
        path = p.path
        m = re.match(rf"^/download/(?:free|started)/({_ID})/?$", path)
        if m:
            return m.group(1)
        m = re.match(rf"^/({_ID})(?:\.html|/[^/]+(?:\.html)?|/)?$", path)
        if m and m.group(1).lower() not in _RESERVED:
            return m.group(1)
        return None

    def supports(self, url) -> bool:
        return self.file_id(url) is not None

    @property
    def api_base(self) -> str:
        return f"https://app.{self.canonical}/api"

    def page_url(self, fid: str) -> str:
        return f"https://{self.canonical}/{fid}"

    # -- HTTP -------------------------------------------------------------------
    def _call(self, ctx, method: str, path: str, body: dict | None = None):
        headers = {"accept": "application/json, text/plain, */*",
                   "origin": f"https://{self.canonical}",
                   "referer": f"https://{self.canonical}/"}
        kw = {"headers": headers, "detect_challenges": False}
        if body is not None:
            kw["json_body"] = body
        r = ctx.client.request(method, self.api_base + path, **kw)
        try:
            data = r.json()
        except ValueError:
            data = {}
        return r.status_code, data if isinstance(data, dict) else {"data": data}

    def _fail(self, status: int, data: dict, what: str):
        name = str(data.get("error_name") or "")
        msg = str(data.get("message") or name or f"HTTP {status}")
        if status == 404 or name in ("file_is_not_available_for_download", "file_not_found",
                                     "file_not_found_in_storages") or re.search(r"not found", msg, re.I):
            raise PluginError(f"{self.label}: file not found (offline)")
        if status == 429 or name == "too_many_requests":
            raise PluginError(f"{self.label}: too many requests (429), wait 1 minutes")
        delay = self._delay(data)
        if delay:
            raise PluginError(f"{self.label} free download limit: wait {_wait_text(delay)}")
        raise PluginError(f"{self.label} {what} failed: {msg}")

    @staticmethod
    def _delay(data: dict) -> int:
        for key in ("ipBan", "ban", "data"):
            v = data.get(key)
            if isinstance(v, dict) and str(v.get("delay", "")).isdigit() and int(v["delay"]) > 0:
                return int(v["delay"])
        return 0

    # -- captcha ----------------------------------------------------------------
    def _captcha(self, ctx, site_url: str) -> tuple[str, int]:
        status, cfg = self._call(ctx, "GET", "/captcha")
        if status >= 400:
            raise PluginError(f"{self.label}: could not load the captcha settings (HTTP {status})")
        driver = str(cfg.get("driver") or "")
        index = int(cfg.get("index") or 0)
        key = str(cfg.get("publicKey") or "")
        kind = CAPTCHA_KINDS.get(driver)
        if kind is None:
            raise PluginError(f"{self.label}: captcha type '{driver}' is not supported yet")
        if kind == "text":
            # gregwar: publicKey is "<key>\n<base64 png>"; the answer is "<key>\n<text>"
            k, _, img = key.partition("\n")
            answer = ctx.solve_captcha(image_b64=img, kind="text", site_url=site_url)
            return f"{k}\n{answer}", index
        return ctx.solve_captcha(kind=kind, sitekey=key, site_url=site_url), index

    # -- accounts ----------------------------------------------------------------
    def _account(self, ctx):
        try:
            return ctx.account_for(site=self.canonical)
        except Exception:
            return None

    def _apply_cookies(self, ctx, acc) -> bool:
        try:
            from ...accountauth import cookie_header_for
            header = cookie_header_for(acc, f"https://app.{self.canonical}/", time.time())
        except Exception:
            header = ""
        if not header or not hasattr(ctx.client, "set_cookie"):
            return False
        for part in header.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                ctx.client.set_cookie(f"https://app.{self.canonical}/", k, v)
        return True

    def _logged_in(self, ctx) -> bool:
        status, data = self._call(ctx, "GET", "/user/info")
        return status == 200 and bool(data) and not data.get("error_name") and \
            data.get("user", data) not in (None, {}, [])

    def _login(self, ctx, acc) -> None:
        if self._apply_cookies(ctx, acc) or self._logged_in(ctx):
            return
        if not acc.username or not acc.password:
            if acc.premium:
                raise LoginRequired(hint=f"{self.label} account needs an e-mail and password (or cookies)")
            return                                  # nothing to sign in with: free download
        answer, index = self._captcha(ctx, f"https://{self.canonical}/login")
        status, data = self._call(ctx, "POST", "/auth/login",
                                  {"email": acc.username, "password": acc.password,
                                   "captcha": True, "captchaResponse": answer,
                                   "captchaIndex": index})
        if status >= 400:
            name = str(data.get("error_name") or "")
            if name == "invalid_captcha":
                raise PluginError(f"{self.label} rejected the login captcha answer")
            raise LoginRequired(hint=f"{self.label} rejected the login: {data.get('message') or name or status}")

    # -- plugin API -----------------------------------------------------------------
    def _short_domain(self, url: str) -> str:
        p = urlsplit(url)
        q = parse_qs(p.query).get("short_domain")
        if q and self._host_ok(q[0].lower()):
            return q[0].lower()
        host = (p.hostname or "").lower()
        host = host[4:] if host.startswith("www.") else host
        return "" if host == self.canonical else host

    def _info(self, ctx, fid: str, url: str = "") -> dict:
        self._call(ctx, "GET", "/site-data")       # session cookies, like the page does
        status, data = self._call(ctx, "POST", "/download/info",
                                  {"fileId": fid, "referrer": "", "site": None,
                                   "shortDomain": self._short_domain(url)})
        if status >= 400:
            self._fail(status, data, "file info")
        return data

    @staticmethod
    def _file_meta(data: dict) -> tuple[str, int | None]:
        f = data.get("file") or {}
        size = f.get("size")
        try:
            size = int(size) if size is not None else None
        except (TypeError, ValueError):
            size = None
        return str(f.get("name") or ""), size

    def grab(self, url, ctx) -> GrabResult:
        fid = self.file_id(url)
        if fid is None:
            raise PluginError(f"not a {self.label} file link")
        name, size = self._file_meta(self._info(ctx, fid, url))
        return GrabResult(title=name or fid, filename=name, size=size, site=self.canonical,
                          plugin_id=self.id, extra={"file_id": fid})

    def resolve(self, url, ctx) -> ResolveResult:
        fid = self.file_id(url)
        if fid is None:
            raise PluginError(f"not a {self.label} file link")
        acc = self._account(ctx)
        if acc is not None and (acc.premium or acc.password or acc.data):
            self._login(ctx, acc)
        info = self._info(ctx, fid, url)
        name, size = self._file_meta(info)
        referer = {"referer": f"https://{self.canonical}/"}
        if info.get("premium"):
            urls = info.get("downloadUrls") or []
            if not urls and info.get("needCaptcha"):
                answer, index = self._captcha(ctx, self.page_url(fid))
                status, data = self._call(ctx, "POST", "/download/premium/dhc",
                                          {"fileId": fid, "captchaResponse": answer,
                                           "captchaIndex": index})
                if status >= 400:
                    self._fail(status, data, "premium download")
                urls = data.get("downloadUrls") or []
            if urls:
                first = urls[0]
                direct = first.get("url") if isinstance(first, dict) else str(first)
                return ResolveResult(url=direct, filename=name, size=size, headers=referer)
            raise PluginError(f"{self.label}: premium account returned no download link")
        if info.get("premiumOnlyDownload"):
            raise PaywallRequired(note=f"{self.label}: the uploader allows premium downloads only")
        return self._free(ctx, fid, name, size, referer)

    def _free(self, ctx, fid, name, size, referer) -> ResolveResult:
        status, init = self._call(ctx, "POST", "/download/free/init", {"fileId": fid})
        if status >= 400:
            self._fail(status, init, "free download")
        delay = self._delay(init)
        if delay:
            raise PluginError(f"{self.label} free download limit: wait {_wait_text(delay)}")
        if init.get("file"):
            name, size = self._file_meta(init) if not name else (name, size)
        answer, index = self._captcha(ctx, f"https://{self.canonical}/download/free/{fid}")
        status, res = self._call(ctx, "POST", "/download/free/captcha",
                                 {"fileId": fid, "captchaResponse": answer, "captchaIndex": index})
        if status >= 400:
            if str(res.get("error_name")) == "invalid_captcha" or (res.get("errors") or {}).get("captcha"):
                raise PluginError(f"{self.label} rejected the captcha answer")
            self._fail(status, res, "captcha")
        wait = int(res.get("delay") or 0)
        if wait > MAX_WAIT:
            raise PluginError(f"{self.label} free download limit: wait {_wait_text(wait)}")
        if wait > 0:
            _sleep(wait + 1)
        status, prep = self._call(ctx, "POST", "/download/free/prepare", {"fileId": fid})
        if status >= 400 or not prep.get("success"):
            self._fail(status, prep, "free download preparation")
        status, start = self._call(ctx, "POST", "/download/free/start", {"fileId": fid})
        if status == 409:
            raise PluginError(f"{self.label}: the free download link expired, retry")
        if status >= 400:
            self._fail(status, start, "free download start")
        direct = str(start.get("downloadUrl") or "")
        if not direct:
            raise PluginError(f"{self.label} returned no download link")
        n2, s2 = self._file_meta(start)
        return ResolveResult(url=direct, filename=name or n2, size=size or s2, headers=referer)
