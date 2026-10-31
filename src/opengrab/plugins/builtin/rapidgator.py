"""
rapidgator.net (and rg.to): premium downloads through the official API v2,
free downloads through the public web flow.

Premium (https://rapidgator.net/article/api/index): user/login turns the
account's e-mail and password into an access token (cached per account and
renewed when the API says the session is gone), file/info gives name and
size, file/download gives the direct link. An account may also carry a
token in Account.data["api_token"]; a 2FA code can be put in
Account.data["code"] (it is only valid for a short time).

Free: the file page holds the file id and the countdown. The plugin starts
the timer (download/AjaxStartTimer), waits the countdown the page shows
(up to 90 s), asks for the link (download/AjaxGetDownloadLink), answers the
captcha on download/captcha through ctx.solve_captcha and reads the
download link from the answer. Waits between free downloads are raised as
PluginError("... wait N minutes"), files the site only gives to premium
users as PaywallRequired.
"""
from __future__ import annotations

import html as _html
import re
import time
from urllib.parse import quote, urljoin, urlsplit

from ..base import (GrabResult, LoginRequired, PaywallRequired, PluginError,
                    ResolveResult, SitePlugin)

_sleep = time.sleep          # tests replace this

SITE = "rapidgator.net"
ORIGIN = "https://rapidgator.net"
API = ORIGIN + "/api/v2"
HOSTS = ("rapidgator.net", "rg.to", "rapidgator.asia")
MAX_COUNTDOWN = 90
TOKEN_TTL = 6 * 3600
_PATH = re.compile(r"^/file/([0-9a-f]{32}|\d{3,12})(?:/([^/?#]+?))?(?:\.html)?/?$", re.I)


def parse_url(url: str) -> tuple[str, str] | None:
    """(file_id, name_from_url) for a Rapidgator file link, else None."""
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if p.scheme not in ("http", "https") or host not in HOSTS:
        return None
    m = _PATH.match(p.path)
    if not m:
        return None
    return m.group(1).lower(), m.group(2) or ""


def canonical_url(fid: str, name: str = "") -> str:
    return f"{ORIGIN}/file/{fid}" + (f"/{name}" if name else "")


def _text(page: str) -> str:
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", _html.unescape(t))


def _attrs(tag: str) -> dict[str, str]:
    return {k.lower(): _html.unescape(v if v is not None else w)
            for k, v, w in re.findall(r'([\w:\[\]-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', tag)}


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


def _size(text: str) -> int | None:
    m = re.search(r"(?:File size|Size)\s*:?\s*(\d+(?:[.,]\d+)?)\s*(B|KB|MB|GB|TB)\b", text, re.I)
    if not m:
        return None
    mult = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}[m.group(2).upper()]
    return int(float(m.group(1).replace(",", ".")) * mult)


def _meta(page: str, fallback: str = "") -> tuple[str, int | None]:
    name = ""
    for pat in (r"<title>\s*Download file\s+([^<]+?)\s*</title>",
                r'<div class="text-block file-descr">.*?<a [^>]*>\s*([^<]+?)\s*</a>',
                r'<meta\s+(?:property|name)="og:title"\s+content="([^"]+)"'):
        m = re.search(pat, page, re.I | re.S)
        if m:
            name = _html.unescape(m.group(1)).strip()
            break
    return name or fallback, _size(_text(page))


def _offline(status: int, final_url: str, page: str) -> bool:
    if status in (404, 410):
        return True
    t = _text(page)
    # the site sends missing files to its premium page with a "404 File not found" banner
    if re.search(r"\b404 File not found\b|file (?:was|has been) (?:deleted|removed)", t, re.I):
        return True
    return "/article/premium" in final_url and bool(re.search(r"File not found", t, re.I))


def _wait_message(text: str) -> str:
    """'' or the PluginError text for a free-mode limit."""
    m = re.search(r"Delay between downloads must be not less than\s*(\d+)\s*min", text, re.I)
    if m:
        return f"Rapidgator free download limit: wait {m.group(1)} minutes"
    m = re.search(r"(?:wait|try again in)\s*(\d+)\s*(minute|min|second|sec|hour)", text, re.I)
    if m and re.search(r"limit|free", text, re.I):
        n, unit = int(m.group(1)), m.group(2).lower()
        mins = n * 60 if unit.startswith("hour") else max(1, -(-n // 60)) if unit.startswith("sec") else n
        return f"Rapidgator free download limit: wait {mins} minutes"
    if re.search(r"reached your (?:daily|hourly) downloads? limit|download limit (?:is )?(?:reached|exceeded)", text, re.I):
        return "Rapidgator free download limit reached: wait 60 minutes (or use a premium account)"
    if re.search(r"(?:not more than|only) 1 file at a time|one file at a time", text, re.I):
        return "Rapidgator allows one free download at a time: wait 5 minutes"
    return ""


def _check_page(page: str) -> None:
    t = _text(page)
    if re.search(r"can be downloaded by premium only|only premium users can download|"
                 r"You can download files up to\s*\d+\s*[KMG]B in free mode", t, re.I):
        raise PaywallRequired(note="Rapidgator: this file needs a premium account")
    msg = _wait_message(t)
    if msg:
        raise PluginError(msg)


_WIDGETS = (("cf-turnstile", "turnstile", "cf-turnstile-response"),
            ("h-captcha", "hcaptcha", "h-captcha-response"),
            ("g-recaptcha", "recaptcha", "g-recaptcha-response"))


def _captcha(page: str, base: str):
    """('widget', kind, sitekey, field) / ('image', url, field) / None."""
    for m in re.finditer(r"(?is)<div\b[^>]*data-sitekey\s*=[^>]*>", page):
        a = _attrs(m.group(0))
        for marker, kind, field in _WIDGETS:
            if marker in a.get("class", ""):
                return ("widget", kind, a.get("data-sitekey", ""), field)
    m = re.search(r"grecaptcha\.(?:render|execute)\([^)]*?['\"]?sitekey['\"]?\s*:\s*['\"]([\w-]{20,})", page)
    if m:
        return ("widget", "recaptcha", m.group(1), "g-recaptcha-response")
    m = re.search(r'<img\b[^>]*src="([^"]*/download/captcha/image[^"]*)"', page, re.I)
    if m:
        f = re.search(r'name="(DownloadCaptchaForm\[(?:verifyCode|captcha)\])"[^>]*type="text"', page) or \
            re.search(r'type="text"[^>]*name="(DownloadCaptchaForm\[(?:verifyCode|captcha)\])"', page)
        return ("image", urljoin(base, _html.unescape(m.group(1))),
                f.group(1) if f else "DownloadCaptchaForm[verifyCode]")
    return None


_LINK_PATS = (
    r"var\s+download_link\s*=\s*['\"]([^'\"]+)['\"]",
    r"location\.href\s*=\s*['\"](https?://[^'\"]+/download/[^'\"]+)['\"]",
    r"""href=["'](https?://[a-z0-9.-]*rapidgator\.net/download/(?!captcha|Ajax)[^"'\s<>]+)["']""",
)


def _download_link(page: str) -> str:
    for pat in _LINK_PATS:
        m = re.search(pat, page, re.I)
        if m:
            return _html.unescape(m.group(1))
    return ""


class RapidgatorPlugin(SitePlugin):
    id = "rapidgator"
    name = "Rapidgator"
    site = SITE
    origin = "builtin"
    supports_captcha = True
    supports_login = True
    description = ("rapidgator.net / rg.to: premium through the official API v2 "
                   "(e-mail and password), free downloads with countdown and captcha.")

    def __init__(self):
        self._tokens: dict[str, tuple[str, float, bool]] = {}   # account -> (token, time, premium)

    def supports(self, url) -> bool:
        return parse_url(url) is not None

    # -- API ---------------------------------------------------------------------
    @staticmethod
    def _api_call(ctx, path: str, params: dict) -> tuple[int, dict | None, str]:
        """(status, response, details) of one API call (POST form, JSON answer)."""
        r = ctx.client.post(f"{API}/{path}", data=params,
                            headers={"accept": "application/json"}, detect_challenges=False)
        try:
            body = r.json()
        except ValueError:
            raise PluginError(f"Rapidgator API answered HTTP {r.status_code} without JSON")
        if not isinstance(body, dict):
            raise PluginError("Rapidgator API answer not understood")
        status = int(body.get("status") or r.status_code or 0)
        return status, body.get("response"), str(body.get("details") or "").strip()

    def _account(self, ctx):
        try:
            return ctx.account_for(site=SITE)
        except Exception:
            return None

    @staticmethod
    def _key(acc) -> str:
        return str(getattr(acc, "id", "") or getattr(acc, "username", ""))

    def _login(self, ctx, acc, force: bool = False) -> tuple[str, bool]:
        """(token, is_premium) for the account, from cache or user/login."""
        key = self._key(acc)
        hit = self._tokens.get(key)
        if hit and not force and hit[1] > time.time() - TOKEN_TTL:
            return hit[0], hit[2]
        data = getattr(acc, "data", None) or {}
        given = str(data.get("api_token") or data.get("token") or "").strip()
        if given and not force:
            status, resp, _ = self._api_call(ctx, "user/info", {"token": given})
            if status == 200 and isinstance(resp, dict):
                premium = bool((resp.get("user") or {}).get("is_premium"))
                self._tokens[key] = (given, time.time(), premium)
                return given, premium
        if not acc.username or not acc.password:
            raise LoginRequired(hint="Rapidgator account needs the e-mail and password")
        params = {"login": acc.username, "password": acc.password}
        if data.get("code"):
            params["code"] = str(data["code"])
        status, resp, details = self._api_call(ctx, "user/login", params)
        if status != 200 or not isinstance(resp, dict) or not resp.get("token"):
            if re.search(r"auth code|2fa|two.factor", details, re.I):
                raise LoginRequired(hint="Rapidgator account uses two-factor login: put the current "
                                         "code in the account's 'code' field and retry")
            if status in (401, 403):
                raise LoginRequired(hint=f"Rapidgator rejected the login: {details or status}")
            raise PluginError(f"Rapidgator login failed: {details or status}")
        premium = bool((resp.get("user") or {}).get("is_premium"))
        self._tokens[key] = (str(resp["token"]), time.time(), premium)
        return str(resp["token"]), premium

    def _authed(self, ctx, acc, path: str, params: dict):
        """API call with the account token; logs in again once when the session expired."""
        token, _ = self._login(ctx, acc)
        status, resp, details = self._api_call(ctx, path, {**params, "token": token})
        if status == 401:
            self._tokens.pop(self._key(acc), None)
            token, _ = self._login(ctx, acc, force=True)
            status, resp, details = self._api_call(ctx, path, {**params, "token": token})
        if status == 404 or re.search(r"file not found", details, re.I):
            raise PluginError("Rapidgator: file not found (offline)")
        if status == 401:
            raise LoginRequired(hint=f"Rapidgator API: {details or 'session not accepted'}")
        if status != 200 or not isinstance(resp, dict):
            if re.search(r"traffic|quota|bandwidth", details, re.I):
                raise PluginError(f"Rapidgator premium traffic exhausted: {details}")
            if re.search(r"premium", details, re.I):
                raise PaywallRequired(note=f"Rapidgator: {details}")
            raise PluginError(f"Rapidgator API error {status}: {details or 'unknown'}")
        return resp

    def _resolve_api(self, ctx, acc, fid: str, fallback_name: str) -> ResolveResult:
        name, size = fallback_name, None
        try:
            info = (self._authed(ctx, acc, "file/info", {"file_id": fid}) or {}).get("file") or {}
            name = str(info.get("name") or name)
            size = int(info["size"]) if str(info.get("size", "")).isdigit() else None
        except (LoginRequired, PaywallRequired):
            raise
        except PluginError as e:
            if "offline" in str(e):
                raise
        resp = self._authed(ctx, acc, "file/download", {"file_id": fid})
        direct = str(resp.get("download_url") or "")
        if not direct:
            raise PluginError("Rapidgator API returned no download URL")
        delay = int(resp.get("delay") or 0)
        if delay > MAX_COUNTDOWN:
            raise PluginError(f"Rapidgator asks to wait: wait {max(1, -(-delay // 60))} minutes")
        if delay > 0:
            _sleep(delay + 1)
        if direct.startswith("//"):
            direct = "https:" + direct
        return ResolveResult(url=direct, filename=name, size=size)

    # -- free web flow -----------------------------------------------------------
    def _xhr(self, ctx, url: str, referer: str) -> dict:
        r = ctx.client.get(url, headers={"x-requested-with": "XMLHttpRequest", "referer": referer,
                                         "accept": "application/json, text/javascript, */*"},
                           detect_challenges=False)
        try:
            data = r.json()
        except ValueError:
            raise PluginError(f"Rapidgator: unexpected answer from {urlsplit(url).path}")
        return data if isinstance(data, dict) else {}

    def _resolve_free(self, ctx, fid: str, url_name: str) -> ResolveResult:
        link = canonical_url(fid, url_name)
        r = ctx.client.get(link, detect_challenges=False)
        page, final = r.text, (r.url or link)
        if _offline(r.status_code, final, page):
            raise PluginError("Rapidgator: file not found (offline)")
        _check_page(page)
        if "/article/premium" in final:
            raise PaywallRequired(note="Rapidgator offers this file to premium users only")
        name, size = _meta(page, url_name)
        m = re.search(r"var\s+fid\s*=\s*['\"]?(\d+)", page)
        if not m:
            direct = _download_link(page)
            if direct:
                return ResolveResult(url=direct, filename=name, size=size, headers={"referer": link})
            raise PluginError("Rapidgator: free download data not found on the page (layout changed?)")
        num_id = m.group(1)
        m = re.search(r"var\s+secs\s*=\s*(\d+)", page)
        secs = int(m.group(1)) if m else 0
        if secs > MAX_COUNTDOWN:
            raise PluginError(f"Rapidgator free download: wait {max(1, -(-secs // 60))} minutes")

        start = self._xhr(ctx, f"{ORIGIN}/download/AjaxStartTimer?fid={num_id}", link)
        if start.get("state") != "started" or not start.get("sid"):
            msg = str(start.get("code") or start.get("message") or start.get("state") or "")
            raise PluginError(_wait_message(msg) or f"Rapidgator refused to start the free download: {msg}")
        sid = str(start["sid"])
        if secs:
            _sleep(secs + 1)
        done = self._xhr(ctx, f"{ORIGIN}/download/AjaxGetDownloadLink?sid={quote(sid)}", link)
        if done.get("state") != "done":
            msg = str(done.get("code") or done.get("message") or done.get("state") or "")
            raise PluginError(_wait_message(msg) or f"Rapidgator did not release the download: {msg}")

        cap_url = f"{ORIGIN}/download/captcha"
        r = ctx.client.get(cap_url, headers={"referer": link}, detect_challenges=False)
        page = r.text
        _check_page(page)
        direct = _download_link(page)
        if direct:
            return ResolveResult(url=direct, filename=name, size=size, headers={"referer": cap_url})
        form = re.search(r'(?is)<form\b([^>]*)>(.*?)</form>', page)
        cap = _captcha(page, cap_url)
        if cap is None:
            if re.search(r"solvemedia|adcopy", page, re.I):
                raise PluginError("Rapidgator: this captcha type (SolveMedia) is not supported")
            raise PluginError("Rapidgator: captcha not found on the download page (layout changed?)")
        fields = _inputs(form.group(2)) if form else {}
        if cap[0] == "widget":
            fields[cap[3]] = ctx.solve_captcha(kind=cap[1], sitekey=cap[2], site_url=cap_url)
        else:
            fields[cap[2]] = ctx.solve_captcha(image_url=cap[1], kind="text", site_url=cap_url)
        fields.setdefault("DownloadCaptchaForm[captcha]", "")
        action = cap_url
        if form:
            act = _attrs(form.group(1)).get("action")
            if act:
                action = urljoin(cap_url, act)
        r = ctx.client.post(action, data=fields, headers={"referer": cap_url}, detect_challenges=False)
        page = r.text
        direct = _download_link(page)
        if direct:
            return ResolveResult(url=direct, filename=name, size=size, headers={"referer": cap_url})
        _check_page(page)
        if re.search(r"verification code is incorrect|wrong captcha|captcha.{0,30}(?:incorrect|invalid)",
                     _text(page), re.I):
            raise PluginError("Rapidgator rejected the captcha answer")
        raise PluginError("Rapidgator did not return a download link")

    # -- plugin API ----------------------------------------------------------------
    def grab(self, url, ctx) -> GrabResult:
        parsed = parse_url(url)
        if parsed is None:
            raise PluginError("not a Rapidgator file link")
        fid, url_name = parsed
        acc = self._account(ctx)
        name, size = url_name, None
        if acc is not None and (acc.password or (getattr(acc, "data", None) or {}).get("api_token")):
            info = (self._authed(ctx, acc, "file/info", {"file_id": fid}) or {}).get("file") or {}
            name = str(info.get("name") or name)
            size = int(info["size"]) if str(info.get("size", "")).isdigit() else None
        else:
            r = ctx.client.get(canonical_url(fid, url_name), detect_challenges=False)
            if _offline(r.status_code, r.url or "", r.text):
                raise PluginError("Rapidgator: file not found (offline)")
            name, size = _meta(r.text, url_name)
        return GrabResult(title=name or fid, filename=name, size=size, site=SITE,
                          plugin_id=self.id, needs_captcha=acc is None,
                          extra={"file_id": fid})

    def resolve(self, url, ctx) -> ResolveResult:
        parsed = parse_url(url)
        if parsed is None:
            raise PluginError("not a Rapidgator file link")
        fid, url_name = parsed
        acc = self._account(ctx)
        if acc is not None and (acc.password or (getattr(acc, "data", None) or {}).get("api_token")):
            _, premium = self._login(ctx, acc)
            if premium:
                return self._resolve_api(ctx, acc, fid, url_name)
        return self._resolve_free(ctx, fid, url_name)
