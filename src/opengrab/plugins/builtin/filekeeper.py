"""
filekeeper.net (XFileSharing based): free and premium downloads.

Free: the file page (https://filekeeper.net/<code>) moves the code into a
cookie, redirects to /download and shows a short countdown and the
download form. The plugin waits the countdown (up to 90 s), answers any
captcha the page shows through ctx.solve_captcha, posts the form
(op=download2) and follows the redirect to the file server.

Premium: signs in with the account's login and password (op=login with the
form token, like the site's login page); premium file pages then redirect
straight to the file or offer the same form without a countdown.
"""
from __future__ import annotations

import html as _html
import re
import time
from urllib.parse import urljoin, urlsplit

from ..base import (GrabResult, LoginRequired, PaywallRequired, PluginError,
                    ResolveResult, SitePlugin)

_sleep = time.sleep          # tests replace this

SITE = "filekeeper.net"
ORIGIN = "https://filekeeper.net"
MAX_COUNTDOWN = 90
_CODE = re.compile(r"^/(?:d/|f/)?([a-z0-9]{12})(?:/[^/]*)?(?:\.html?)?/?$", re.I)


def file_code(url: str) -> str | None:
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or host not in (SITE, "www." + SITE):
        return None
    m = _CODE.match(p.path)
    return m.group(1).lower() if m else None


def _attrs(tag: str) -> dict[str, str]:
    return {k.lower(): _html.unescape(v if v is not None else w)
            for k, v, w in re.findall(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', tag)}


def _text(html: str) -> str:
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", _html.unescape(t))


def _inputs(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in re.findall(r"(?is)<input\b[^>]*>", body):
        a = _attrs(tag)
        name = a.get("name")
        if not name:
            continue
        typ = a.get("type", "text").lower()
        if typ in ("checkbox", "radio") and "checked" not in tag.lower():
            continue
        out[name] = a.get("value", "")
    return out


def _offline(status: int, html: str) -> bool:
    if status in (404, 410):
        return True
    return bool(re.search(r"file you were looking for could not be found|"
                          r"The file (?:was|has been) (?:deleted|removed)", _text(html), re.I)
                or re.search(r"<title>[^<]*File Not Found", html, re.I))


def _check_page(html: str) -> None:
    t = _text(html)
    m = re.search(r"You have to wait\s+(?:(\d+)\s*hours?,?\s*)?(?:(\d+)\s*minutes?,?\s*)?(?:(\d+)\s*seconds?)?", t, re.I)
    if m and any(m.groups()):
        h, mi, s = (int(x or 0) for x in m.groups())
        total = h * 3600 + mi * 60 + s
        raise PluginError(f"FileKeeper free download limit: wait {max(1, -(-total // 60))} minutes")
    if re.search(r"available for Premium Users only|only premium users can download|"
                 r"You can download files up to \d+\s*[KMG]b only", t, re.I):
        raise PaywallRequired(note="FileKeeper: this file needs a premium account")
    if re.search(r"reached the download.limit|download limit (?:reached|exceeded)", t, re.I):
        raise PluginError("FileKeeper download limit reached: wait 60 minutes")


_WIDGETS = (("cf-turnstile", "turnstile", "cf-turnstile-response"),
            ("h-captcha", "hcaptcha", "h-captcha-response"),
            ("g-recaptcha", "recaptcha", "g-recaptcha-response"))


def _captcha(html: str, base: str):
    """('widget', kind, sitekey, field) or ('image', url, field) or None."""
    for m in re.finditer(r"(?is)<div\b[^>]*data-sitekey\s*=[^>]*>", html):
        a = _attrs(m.group(0))
        for marker, kind, field in _WIDGETS:
            if marker in a.get("class", ""):
                return ("widget", kind, a.get("data-sitekey", ""), field)
    m = re.search(r'<img\b[^>]*src="([^"]*captchas?/[^"]+)"', html, re.I)
    if m and re.search(r'name="code"', html):
        return ("image", urljoin(base, _html.unescape(m.group(1))), "code")
    return None


def _countdown(html: str) -> int:
    for pat in (r'data-countdown="(\d+)"', r'id="(?:countdown_str|cxc|countdown)"[^>]*>\D{0,40}?(\d+)',
                r'class="seconds"[^>]*>\s*(\d+)'):
        m = re.search(pat, html, re.I)
        if m:
            return int(m.group(1))
    return 0


def _meta(html: str) -> tuple[str, int | None]:
    name = ""
    for pat in (r'data-(?:file-?name|name)="([^"]+)"', r'name="fname"\s+value="([^"]+)"',
                r'<h1[^>]*>\s*([^<]{1,250}?)\s*</h1>', r"<title>\s*Download\s+([^<]+?)\s*</title>"):
        m = re.search(pat, html, re.I)
        if m and not re.search(r"file not found|404", m.group(1), re.I):
            name = _html.unescape(m.group(1)).strip()
            break
    size = None
    m = re.search(r'data-(?:file-?)?size="(\d+)"', html, re.I)
    if m:
        size = int(m.group(1))
    else:
        m = re.search(r"\(?\b(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)\b\)?", _text(html))
        if m:
            mult = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}[m.group(2).upper()]
            size = int(float(m.group(1)) * mult)
    return name, size


def _form_fields(html: str, code: str) -> tuple[str, dict] | None:
    """(action, fields) of the download form, from data-* attributes or a classic XFS form."""
    holder = re.search(r'(?is)<[a-z]+\b[^>]*\bdata-code="[^"]*"[^>]*>', html)
    if holder:
        a = _attrs(holder.group(0))
        if a.get("data-has-password") == "true":
            raise PluginError("FileKeeper: the file is password protected")
        return "", {"op": "download2", "id": a.get("data-code") or code,
                    "rand": a.get("data-rand", ""), "referer": a.get("data-referer", ""),
                    "method_free": a.get("data-method") or "Free download", "down_direct": "1"}
    for m in re.finditer(r"(?is)<form\b([^>]*)>(.*?)</form>", html):
        attrs, body = _attrs(m.group(1)), m.group(2)
        fields = _inputs(body)
        if fields.get("op") in ("download1", "download2"):
            if "password" in fields:
                raise PluginError("FileKeeper: the file is password protected")
            fields.setdefault("id", code)
            if fields["op"] == "download1":
                fields.setdefault("method_free", "Free Download")
            fields.setdefault("down_direct", "1")
            return attrs.get("action", ""), fields
    return None


def _direct_in_page(html: str) -> str:
    for pat in (r'id="direct_link"[^>]*>\s*<a\b[^>]*href="([^"]+)"',
                r'<a\b[^>]*href="(https?://(?!(?:www\.)?filekeeper\.net)[^"]+/d/[^"]+)"'):
        m = re.search(pat, html, re.I)
        if m:
            return _html.unescape(m.group(1))
    return ""


class FileKeeperPlugin(SitePlugin):
    id = "filekeeper"
    name = "FileKeeper"
    site = SITE
    origin = "builtin"
    supports_captcha = True
    supports_login = True
    description = "filekeeper.net: free downloads (countdown, captcha) and premium accounts."

    def __init__(self):
        self._sessions: dict[str, float] = {}

    def supports(self, url) -> bool:
        return file_code(url) is not None

    # -- HTTP helpers -----------------------------------------------------------
    def _go(self, ctx, method: str, url: str, data=None, referer: str = ""):
        """Request with manual redirects so every Set-Cookie on the way is kept.
        Returns (response, final_url, off_site_url)."""
        headers = {"referer": referer} if referer else {}
        for _ in range(6):
            kw = {"follow": False, "detect_challenges": False, "headers": headers}
            if data is not None:
                kw["data"] = data
            r = ctx.client.request(method, url, **kw)
            loc = r.headers.get("location", "")
            if r.status_code not in (301, 302, 303, 307, 308) or not loc:
                return r, url, ""
            nxt = urljoin(url, loc)
            if (urlsplit(nxt).hostname or "").lower() not in (SITE, "www." + SITE):
                return r, url, nxt
            url, method, data = nxt, "GET", None
        raise PluginError("FileKeeper redirected too many times")

    def _account(self, ctx):
        try:
            return ctx.account_for(site=SITE)
        except Exception:
            return None

    @staticmethod
    def _api_key(acc) -> str:
        data = getattr(acc, "data", None) or {}
        return str(data.get("api_key") or data.get("api_token") or "").strip()

    def _api_direct_link(self, ctx, acc, code: str):
        """The official API (filekeeper.net/pages/integrations): with the API
        key from the user's account settings, /api/file/direct_link returns
        the download link without the page flow. None means "use the page"."""
        key = self._api_key(acc)
        if not key:
            return None
        from urllib.parse import quote
        try:
            info = ctx.client.get(f"{ORIGIN}/api/file/info?key={quote(key)}&file_code={quote(code)}",
                                  detect_challenges=False).json()
            files = info.get("result") or []
            meta = files[0] if isinstance(files, list) and files else {}
            if int(meta.get("status") or info.get("status") or 0) == 404:
                raise PluginError("FileKeeper: file not found (offline)")
            r = ctx.client.get(f"{ORIGIN}/api/file/direct_link?key={quote(key)}&file_code={quote(code)}",
                               detect_challenges=False).json()
        except PluginError:
            raise
        except Exception:
            return None  # API unreachable or not JSON: the page flow still works
        if int(r.get("status") or 0) != 200:
            return None  # e.g. the key's account may not fetch this file through the API
        res = r.get("result")
        url = res.get("url") if isinstance(res, dict) else res if isinstance(res, str) else ""
        if not url:
            return None
        size = meta.get("size") or (res.get("size") if isinstance(res, dict) else None)
        return ResolveResult(url=str(url), filename=str(meta.get("name") or ""),
                             size=int(size) if str(size or "").isdigit() else None,
                             headers={"referer": ORIGIN + "/"})

    def _login(self, ctx, acc) -> None:
        if self._sessions.get(acc.id, 0) > time.time() - 6 * 3600:
            return
        try:
            from ...accountauth import cookie_header_for
            header = cookie_header_for(acc, ORIGIN + "/", time.time())
        except Exception:
            header = ""
        if header and hasattr(ctx.client, "set_cookie"):
            for part in header.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    ctx.client.set_cookie(ORIGIN + "/", k, v)
            self._sessions[acc.id] = time.time()
            return
        if not acc.username or not acc.password:
            if acc.premium:
                raise LoginRequired(hint="FileKeeper account needs a login and password (or cookies)")
            return
        page = ctx.client.get(ORIGIN + "/login", detect_challenges=False).text
        form = re.search(r'(?is)<form\b[^>]*name="FL"[^>]*>(.*?)</form>', page)
        fields = _inputs(form.group(1)) if form else {"op": "login"}
        fields.update({"op": "login", "login": acc.username, "password": acc.password})
        cap = _captcha(form.group(1) if form else "", ORIGIN)
        if cap and cap[0] == "widget":
            fields[cap[3]] = ctx.solve_captcha(kind=cap[1], sitekey=cap[2], site_url=ORIGIN + "/login")
        elif cap:
            fields[cap[2]] = ctx.solve_captcha(image_url=cap[1], kind="text", site_url=ORIGIN + "/login")
        r, _, _ = self._go(ctx, "POST", ORIGIN + "/", data=fields, referer=ORIGIN + "/login")
        t = _text(r.text)
        if re.search(r"Incorrect Login or Password|Wrong captcha|account (?:is )?banned", t, re.I) \
                or 'name="FL"' in r.text:
            raise LoginRequired(hint="FileKeeper rejected the login or password")
        self._sessions[acc.id] = time.time()

    # -- plugin API ---------------------------------------------------------------
    def grab(self, url, ctx) -> GrabResult:
        code = file_code(url)
        if code is None:
            raise PluginError("not a FileKeeper file link")
        r, _, off = self._go(ctx, "GET", f"{ORIGIN}/{code}")
        if off:
            return GrabResult(title=code, site=SITE, plugin_id=self.id, extra={"file_code": code})
        if _offline(r.status_code, r.text):
            raise PluginError("FileKeeper: file not found (offline)")
        name, size = _meta(r.text)
        return GrabResult(title=name or code, filename=name, size=size, site=SITE,
                          plugin_id=self.id, extra={"file_code": code})

    def resolve(self, url, ctx) -> ResolveResult:
        code = file_code(url)
        if code is None:
            raise PluginError("not a FileKeeper file link")
        acc = self._account(ctx)
        premium = False
        if acc is not None:
            via_api = self._api_direct_link(ctx, acc, code)
            if via_api is not None:
                return via_api
            self._login(ctx, acc)
            premium = bool(acc.premium)
        start = f"{ORIGIN}/{code}"
        r, page_url, off = self._go(ctx, "GET", start)
        if off:
            return ResolveResult(url=off, headers={"referer": ORIGIN + "/"})
        html = r.text
        if _offline(r.status_code, html):
            raise PluginError("FileKeeper: file not found (offline)")
        _check_page(html)
        name, size = _meta(html)
        for _step in range(3):              # download1 (choice) -> download2 -> file
            form = _form_fields(html, code)
            if form is None:
                direct = _direct_in_page(html)
                if direct:
                    return ResolveResult(url=direct, filename=name, size=size,
                                         headers={"referer": page_url})
                raise PluginError("FileKeeper: download form not found (page layout changed?)")
            action, fields = form
            if fields.get("op") == "download2":
                if premium:
                    fields.pop("method_free", None)
                    fields["method_premium"] = "1"
                cap = _captcha(html, page_url)
                if cap and cap[0] == "widget":
                    fields[cap[3]] = ctx.solve_captcha(kind=cap[1], sitekey=cap[2], site_url=page_url)
                elif cap:
                    fields[cap[2]] = ctx.solve_captcha(image_url=cap[1], kind="text", site_url=page_url)
                elif re.search(r'data-has-captcha="true"', html):
                    raise PluginError("FileKeeper: captcha type on this page is not supported yet")
                wait = 0 if premium else _countdown(html)
                if wait > MAX_COUNTDOWN:
                    raise PluginError(f"FileKeeper free download limit: wait {max(1, -(-wait // 60))} minutes")
                if wait:
                    _sleep(wait + 1)
            target = urljoin(page_url, action) if action else page_url
            r, page_url, off = self._go(ctx, "POST", target, data=fields, referer=page_url)
            if off:
                return ResolveResult(url=off, filename=name, size=size,
                                     headers={"referer": ORIGIN + "/"})
            html = r.text
            _check_page(html)
            if re.search(r"Wrong captcha", _text(html), re.I):
                raise PluginError("FileKeeper rejected the captcha answer")
            if re.search(r"Skipped countdown", _text(html), re.I):
                raise PluginError("FileKeeper says the countdown was skipped, retry")
            direct = _direct_in_page(html)
            if direct:
                return ResolveResult(url=direct, filename=name, size=size,
                                     headers={"referer": page_url})
        raise PluginError("FileKeeper did not return a download link")
