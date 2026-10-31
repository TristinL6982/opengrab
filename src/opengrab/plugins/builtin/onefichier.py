"""
1fichier.com (and its alias domains): free and premium downloads.

Premium with an API key (Account.data["api_token"] or ["api_key"]) uses the
official API at https://api.1fichier.com/ (download/get_token.cgi and
file/info.cgi, Bearer authentication). Premium with e-mail and password
signs in on the web site (login.pl) and reads the direct link the site
offers to signed-in customers.

Free downloads follow the public page: the "Access to download" form, any
countdown the page shows (waited up to 90 s), any captcha widget the page
embeds (answered through ctx.solve_captcha), then the "Click here to
download" link. Server-side waits between free downloads are raised as
PluginError("... wait N minutes ...") for the engine's retry/reconnect.
"""
from __future__ import annotations

import html as _html
import re
import time
from urllib.parse import urljoin, urlsplit

from ..base import (GrabResult, LoginRequired, PluginError, ResolveResult,
                    SitePlugin)

_sleep = time.sleep          # tests replace this

SITE = "1fichier.com"
BASE = "https://1fichier.com/"
API = "https://api.1fichier.com/v1"
MAX_COUNTDOWN = 90

# 1fichier serves the same files on these domains
DOMAINS = ("1fichier.com", "alterupload.com", "cjoint.net", "desfichiers.com",
           "dfichiers.com", "megadl.fr", "mesfichiers.org", "piecejointe.net",
           "pjointe.com", "tenvoi.com", "dl4free.com")

_ID = r"[A-Za-z0-9]{5,30}"
_QUERY_ID = re.compile(rf"^({_ID})(?:[&;].*)?$")


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def file_id(url: str) -> str | None:
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None
    host = _host(url)
    for d in DOMAINS:
        if host == d:
            if p.path not in ("", "/"):
                return None            # /dir/, /console/, /login.pl ...
            m = _QUERY_ID.match(p.query or "")
            return m.group(1).lower() if m else None
        if host.endswith("." + d):
            sub = host[: -len(d) - 1]
            if re.fullmatch(_ID, sub) and p.path in ("", "/") and sub not in ("api", "img", "static"):
                return sub.lower()      # old style https://<id>.1fichier.com/
            return None
    return None


def canonical_url(fid: str) -> str:
    return f"{BASE}?{fid}"


def _api_key(acc) -> str:
    data = getattr(acc, "data", None) or {}
    return str(data.get("api_token") or data.get("api_key") or "").strip()


def _text(html: str) -> str:
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", _html.unescape(t))


def _check_waits(html: str) -> None:
    """Raise for every 'come back later' state the page can show."""
    t = _text(html)
    m = re.search(r"(?:you must wait|vous devez attendre)\D{0,20}(\d+)\s*(minute|min|second|sec|hour|heure)", t, re.I)
    if m:
        unit = m.group(2).lower()
        unit = "hours" if unit.startswith(("hour", "heure")) else \
            "seconds" if unit.startswith("sec") else "minutes"
        raise PluginError(f"1fichier free download limit: wait {m.group(1)} {unit}")
    if re.search(r"one file at a time|un seul fichier à la fois|un fichier à la fois", t, re.I):
        raise PluginError("1fichier allows one free download at a time: wait 5 minutes "
                          "(or use a premium account)")
    if re.search(r"not compatible with massively shared|massivement partag", t, re.I):
        raise PluginError("1fichier refuses free downloads from this shared IP/VPN: wait 60 minutes "
                          "or change the connection")
    if re.search(r"Please wait until the file has been scanned|en cours d'analyse", t, re.I):
        raise PluginError("1fichier is scanning the file for viruses: wait 5 minutes")


def _offline(status: int, html: str) -> bool:
    if status in (404, 410):
        return True
    t = _text(html)
    return bool(re.search(
        r"requested file (?:does not exist|could not be found|has been deleted)|"
        r"file (?:has been|was) deleted|n'existe pas|a été supprimé", t, re.I))


def _attrs(tag: str) -> dict[str, str]:
    return {k.lower(): _html.unescape(v if v is not None else w)
            for k, v, w in re.findall(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', tag)}


def _forms(html: str) -> list[tuple[dict, str]]:
    return [(_attrs(m.group(1)), m.group(2))
            for m in re.finditer(r"(?is)<form\b([^>]*)>(.*?)</form>", html)]


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
        if typ == "submit" and name in out:
            continue
        out[name] = a.get("value", "on" if typ == "checkbox" else "")
    return out


_WIDGETS = (
    ("cf-turnstile", "turnstile", "cf-turnstile-response"),
    ("h-captcha", "hcaptcha", "h-captcha-response"),
    ("g-recaptcha", "recaptcha", "g-recaptcha-response"),
)


def _captcha_widget(html: str):
    """(kind, sitekey, response_field) for a widget on the page, else None."""
    for m in re.finditer(r"(?is)<div\b[^>]*data-sitekey\s*=[^>]*>", html):
        tag = m.group(0)
        a = _attrs(tag)
        cls = a.get("class", "")
        for marker, kind, field in _WIDGETS:
            if marker in cls:
                return kind, a.get("data-sitekey", ""), field
    return None


def _countdown(html: str) -> int:
    for pat in (r"\bvar\s+ct\s*=\s*(\d+)", r'id="(?:cnt|countdown|dlw_cnt)"[^>]*>\s*(\d+)',
                r'data-countdown="(\d+)"', r"(?:please wait|veuillez patienter)\D{0,15}(\d+)\s*s"):
        m = re.search(pat, html, re.I)
        if m:
            return int(m.group(1))
    return 0


def _direct_link(html: str, base: str) -> str:
    for pat in (r'<a\b[^>]*href="([^"]+)"[^>]*class="[^"]*\bok\b[^"]*btn-orange',
                r'<a\b[^>]*class="[^"]*\bok\b[^"]*btn-orange[^"]*"[^>]*href="([^"]+)"',
                r'href="(https?://[a-z0-9-]+\.1fichier\.com/[cp][^"]+)"'):
        m = re.search(pat, html, re.I)
        if m:
            return urljoin(base, _html.unescape(m.group(1)))
    return ""


def _download_form(html: str):
    """The 'Access to download' form (the one that is not login/search)."""
    for attrs, body in _forms(html):
        if (attrs.get("method", "get").lower() == "post"
                and "name=\"pass\"" not in body and "lg-mail" not in body
                and ("adz" in body or "submit" in body.lower())):
            return attrs, body
    return None


class OneFichierPlugin(SitePlugin):
    id = "1fichier"
    name = "1fichier"
    site = SITE
    origin = "builtin"
    supports_captcha = True
    supports_login = True
    description = ("1fichier.com and alias domains: free downloads (wait/captcha) "
                   "and premium via the official API key or e-mail/password.")

    def __init__(self):
        self._sessions: dict[str, float] = {}   # account id -> login time

    def supports(self, url) -> bool:
        return file_id(url) is not None

    # -- helpers -------------------------------------------------------------
    def _account(self, ctx):
        try:
            return ctx.account_for(site=SITE)
        except Exception:
            return None

    def _api(self, ctx, path: str, key: str, body: dict) -> dict:
        r = ctx.client.request("POST", f"{API}/{path}", json_body=body,
                               headers={"authorization": f"Bearer {key}",
                                        "accept": "application/json"},
                               detect_challenges=False)
        try:
            data = r.json()
        except ValueError:
            data = {}
        msg = str((data or {}).get("message") or "").strip()
        if r.status_code in (401, 403):
            raise LoginRequired(hint=f"1fichier rejected the API key{': ' + msg if msg else ''}")
        if r.status_code == 429:
            raise PluginError("1fichier API rate limit (429): wait 1 minute")
        if r.status_code == 404 or re.search(r"not found|#6\d\d|doesn't exist|does not exist", msg, re.I):
            raise PluginError("1fichier: file not found (offline)")
        if r.status_code >= 400 or str(data.get("status", "OK")).upper() == "KO":
            if re.search(r"password|mot de passe", msg, re.I):
                raise PluginError("1fichier: the file is password protected")
            raise PluginError(f"1fichier API error {r.status_code}: {msg or 'unknown'}")
        return data

    def _login(self, ctx, acc) -> None:
        """Web sign-in with e-mail/password; the client keeps the SID cookie."""
        if self._sessions.get(acc.id, 0) > time.time() - 6 * 3600:
            return
        if not acc.username or not acc.password:
            raise LoginRequired(hint="1fichier account needs an e-mail and password, or an API key")
        r = ctx.client.post(urljoin(BASE, "login.pl"),
                            data={"mail": acc.username, "pass": acc.password, "lt": "on",
                                  "purge": "on", "valider": "Sign in"},
                            headers={"referer": urljoin(BASE, "login.pl")})
        page = r.text
        if 'id="lg-pass"' in page or re.search(r"invalid (?:email|password)|incorrect", _text(page), re.I):
            raise LoginRequired(hint="1fichier rejected the e-mail or password")
        self._sessions[acc.id] = time.time()

    @staticmethod
    def _apply_account_cookies(ctx, acc) -> bool:
        try:
            from ...accountauth import cookie_header_for
            header = cookie_header_for(acc, BASE, time.time())
        except Exception:
            header = ""
        if not header or not hasattr(ctx.client, "set_cookie"):
            return False
        for part in header.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                ctx.client.set_cookie(BASE, k, v)
        return True

    def _check_link(self, ctx, url: str) -> tuple[str, int | None]:
        """Public link checker: 'url;filename;size' or 'url;;;NOT FOUND'."""
        try:
            r = ctx.client.post(urljoin(BASE, "check_links.pl"), data={"links[]": url},
                                detect_challenges=False)
        except Exception:
            return "", None
        line = (r.text or "").strip().splitlines()[0] if (r.text or "").strip() else ""
        parts = line.split(";")
        if "NOT FOUND" in line.upper() or "DELETED" in line.upper():
            raise PluginError("1fichier: file not found (offline)")
        if len(parts) >= 3:
            size = int(parts[2]) if parts[2].strip().isdigit() else None
            return _html.unescape(parts[1].strip()), size
        return "", None

    # -- plugin API -----------------------------------------------------------
    def grab(self, url, ctx) -> GrabResult:
        fid = file_id(url)
        if fid is None:
            raise PluginError("not a 1fichier file link")
        link = canonical_url(fid)
        name, size = "", None
        acc = self._account(ctx)
        key = _api_key(acc) if acc else ""
        if key:
            info = self._api(ctx, "file/info.cgi", key, {"url": link})
            name, size = str(info.get("filename") or ""), info.get("size")
        else:
            name, size = self._check_link(ctx, link)
        return GrabResult(title=name or fid, filename=name, size=size, site=SITE,
                          plugin_id=self.id, needs_captcha=not acc,
                          extra={"file_id": fid})

    def resolve(self, url, ctx) -> ResolveResult:
        fid = file_id(url)
        if fid is None:
            raise PluginError("not a 1fichier file link")
        link = canonical_url(fid)
        acc = self._account(ctx)
        if acc is not None:
            key = _api_key(acc)
            if key:
                return self._resolve_api(ctx, link, key)
            has_cookies = self._apply_account_cookies(ctx, acc)
            if has_cookies or acc.password:
                if not has_cookies:
                    self._login(ctx, acc)
                if acc.premium:
                    res = self._resolve_page(ctx, link, premium=True)
                    if res is not None:
                        return res
        return self._resolve_page(ctx, link, premium=False)

    def _resolve_api(self, ctx, link: str, key: str) -> ResolveResult:
        name, size = "", None
        try:
            info = self._api(ctx, "file/info.cgi", key, {"url": link})
            name, size = str(info.get("filename") or ""), info.get("size")
        except LoginRequired:
            raise
        except PluginError as e:
            if "offline" in str(e):
                raise
        tok = self._api(ctx, "download/get_token.cgi", key, {"url": link})
        direct = str(tok.get("url") or "")
        if not direct:
            raise PluginError(f"1fichier API returned no download URL: {tok.get('message', '')}")
        return ResolveResult(url=direct, filename=name, size=size if isinstance(size, int) else None)

    def _resolve_page(self, ctx, link: str, premium: bool) -> ResolveResult | None:
        if hasattr(ctx.client, "set_cookie"):
            ctx.client.set_cookie(BASE, "LG", "en")
        r = ctx.client.get(link, follow=False, detect_challenges=False)
        loc = r.headers.get("location", "")
        if r.status_code in (301, 302, 303, 307) and loc:
            target = urljoin(link, loc)
            if file_id(target) is None and "1fichier.com/?c=" not in target:
                return ResolveResult(url=target, headers={"referer": link})   # premium direct download
            r = ctx.client.get(target, detect_challenges=False)
        page = r.text
        if _offline(r.status_code, page):
            raise PluginError("1fichier: file not found (offline)")
        _check_waits(page)
        if 'name="pass"' in page and 'id="lg-pass"' not in page:
            raise PluginError("1fichier: the file is password protected")
        direct = _direct_link(page, link)
        if direct:
            return ResolveResult(url=direct, headers={"referer": link})
        form = _download_form(page)
        if form is None:
            if premium:
                return None
            raise PluginError("1fichier: download form not found (page layout changed?)")
        attrs, body = form
        data = _inputs(body)
        widget = _captcha_widget(page)
        if widget:
            kind, sitekey, field = widget
            data[field] = ctx.solve_captcha(kind=kind, sitekey=sitekey, site_url=link)
            if kind == "recaptcha":
                data.setdefault("g-recaptcha-response", data[field])
        wait = _countdown(page) if not premium else 0
        if wait > MAX_COUNTDOWN:
            raise PluginError(f"1fichier free download limit: wait {max(1, round(wait / 60))} minutes")
        if wait:
            _sleep(wait + 1)
        action = urljoin(link, attrs.get("action") or link)
        r2 = ctx.client.post(action, data=data, headers={"referer": link},
                             follow=False, detect_challenges=False)
        loc = r2.headers.get("location", "")
        if r2.status_code in (301, 302, 303, 307) and loc and file_id(urljoin(action, loc)) is None:
            return ResolveResult(url=urljoin(action, loc), headers={"referer": link})
        page2 = r2.text
        _check_waits(page2)
        direct = _direct_link(page2, link)
        if direct:
            return ResolveResult(url=direct, headers={"referer": link})
        if re.search(r"captcha", _text(page2), re.I) and widget:
            raise PluginError("1fichier rejected the captcha answer")
        if premium:
            return None
        raise PluginError("1fichier did not return a download link")
