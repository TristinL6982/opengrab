"""
chomikuj.pl: file downloads through the site's own download dialog.

File links look like https://chomikuj.pl/<user>/<folders>/<name>,<id>.<ext>
with an optional type suffix such as "(archive)" or "(video)".

The plugin does what the "Pobierz" (download) button does: it posts the
file id with the page's anti-forgery token to /action/License/Download and
walks the dialogs the site answers with:
  - visitors (no account) may download files up to 1 MB after a reCAPTCHA,
    answered through ctx.solve_captcha; bigger files raise LoginRequired;
  - signed-in users confirm the transfer/points dialogs the site shows,
    like a click on "Pobierz" would; not enough transfer raises
    PaywallRequired;
  - a "Redirect" answer carries the file server URL.
Sign-in uses the account's chomik name and password (/action/Login/TopBarLogin).
"""
from __future__ import annotations

import html as _html
import re
import time
from urllib.parse import unquote, urljoin, urlsplit

from ..base import (GrabResult, LoginRequired, PaywallRequired, PluginError,
                    ResolveResult, SitePlugin)

SITE = "chomikuj.pl"
ORIGIN = "https://chomikuj.pl"
_ID = re.compile(r",(\d{1,15})\.[^/,]*$")
XHR = {"x-requested-with": "XMLHttpRequest",
       "accept": "application/json, text/javascript, */*; q=0.01"}
# dialogs that only confirm the download (warning, own points/transfer, large file)
_CONFIRM_FORMS = ("downloadWarningForm", "FormAcceptOwnPoints", "FormAcceptLargePoints",
                  "confirmLargeTransferForm", "AcceptOwnTransferForm",
                  "FormAcceptLimitedSalesLicense", "FormAcceptLimitedAndStreamingOnlyLicense")
_SESSION_TTL = 6 * 3600


def file_id(url: str) -> str | None:
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or host not in (SITE, "www." + SITE):
        return None
    path = unquote(p.path)
    if path.count("/") < 2:          # /<user>/<name>,<id>.<ext> at the least
        return None
    m = _ID.search(path)
    return m.group(1) if m else None


def _text(page: str) -> str:
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def _attrs(tag: str) -> dict[str, str]:
    return {k.lower(): _html.unescape(v if v is not None else w)
            for k, v, w in re.findall(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', tag)}


def _inputs(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in re.findall(r"(?is)<input\b[^>]*>", body):
        a = _attrs(tag)
        name = a.get("name")
        typ = a.get("type", "text").lower()
        if not name or typ in ("submit", "reset", "button", "image"):
            continue
        if typ in ("checkbox", "radio") and "checked" not in tag.lower():
            continue
        out[name] = a.get("value", "")
    return out


def _forms(page: str) -> list[tuple[dict, str]]:
    return [(_attrs(m.group(1)), m.group(2))
            for m in re.finditer(r"(?is)<form\b([^>]*)>(.*?)</form>", page)]


def _token(page: str) -> str:
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page)
    return _html.unescape(m.group(1)) if m else ""


def _size(text: str) -> int | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(B|KB|MB|GB|TB)\b", text, re.I)
    if not m:
        return None
    mult = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}[m.group(2).upper()]
    return int(float(m.group(1).replace(",", ".")) * mult)


def _meta(page: str) -> tuple[str, int | None]:
    name = ""
    m = re.search(r'(?is)<h1[^>]*class="[^"]*filename[^"]*"[^>]*>(.*?)</h1>', page)
    if m:
        name = _text(m.group(1))
    if not name:
        m = re.search(r"<title>\s*([^<]+?)\s+-\s+[^<]*Chomikuj\.pl", page, re.I)
        if m:
            name = _html.unescape(m.group(1)).split(" - ")[0].strip()
    m = re.search(r'class="fileSize"[^>]*>\s*([^<]+)<', page)
    return name, _size(m.group(1)) if m else None


def _sitekey(content: str) -> str:
    m = re.search(r"['\"]sitekey['\"]\s*:\s*['\"]([\w-]{20,})['\"]", content) or \
        re.search(r'data-sitekey="([\w-]{20,})"', content)
    return m.group(1) if m else ""


class ChomikujPlugin(SitePlugin):
    id = "chomikuj"
    name = "Chomikuj.pl"
    site = SITE
    origin = "builtin"
    supports_captcha = True
    supports_login = True
    description = ("chomikuj.pl: small files for visitors (reCAPTCHA), bigger files "
                   "with a chomik account (transfer/points).")

    def __init__(self):
        self._sessions: dict[str, float] = {}

    def supports(self, url) -> bool:
        return file_id(url) is not None

    def _account(self, ctx):
        try:
            return ctx.account_for(site=SITE)
        except Exception:
            return None

    def _login(self, ctx, acc) -> bool:
        """Sign in; True when the client now holds a signed-in session."""
        key = str(getattr(acc, "id", "") or acc.username)
        if self._sessions.get(key, 0) > time.time() - _SESSION_TTL:
            return True
        if not acc.username or not acc.password:
            return False
        home = ctx.client.get(ORIGIN + "/", detect_challenges=False).text
        r = ctx.client.post(ORIGIN + "/action/Login/TopBarLogin",
                            data={"Login": acc.username, "Password": acc.password,
                                  "__RequestVerificationToken": _token(home), "ReturnUrl": ""},
                            headers={**XHR, "referer": ORIGIN + "/"}, detect_challenges=False)
        try:
            j = r.json()
        except ValueError:
            j = {}
        if not (j.get("IsSuccess") and (j.get("Data") or {}).get("LoggedIn", True)):
            msg = _text(str(j.get("Message") or "")) or f"HTTP {r.status_code}"
            raise LoginRequired(hint=f"chomikuj.pl rejected the login: {msg}")
        self._sessions[key] = time.time()
        return True

    def _post(self, ctx, path: str, data: dict, referer: str) -> dict:
        r = ctx.client.post(urljoin(ORIGIN, path), data=data,
                            headers={**XHR, "referer": referer}, detect_challenges=False)
        try:
            j = r.json()
        except ValueError:
            raise PluginError(f"chomikuj.pl: unexpected answer from {path} (HTTP {r.status_code})")
        return j if isinstance(j, dict) else {}

    # -- plugin API ---------------------------------------------------------------
    def grab(self, url, ctx) -> GrabResult:
        fid = file_id(url)
        if fid is None:
            raise PluginError("not a chomikuj.pl file link")
        r = ctx.client.get(url, detect_challenges=False)
        if r.status_code in (404, 410):
            raise PluginError("chomikuj.pl: file not found (offline)")
        name, size = _meta(r.text)
        acc = self._account(ctx)
        return GrabResult(title=name or fid, filename=name, size=size, site=SITE,
                          plugin_id=self.id,
                          needs_login=acc is None and (size or 0) > 1024 * 1024,
                          needs_captcha=acc is None, extra={"file_id": fid})

    def resolve(self, url, ctx) -> ResolveResult:
        fid = file_id(url)
        if fid is None:
            raise PluginError("not a chomikuj.pl file link")
        acc = self._account(ctx)
        logged_in = bool(acc is not None and self._login(ctx, acc))
        r = ctx.client.get(url, detect_challenges=False)
        if r.status_code in (404, 410):
            raise PluginError("chomikuj.pl: file not found (offline)")
        page = r.text
        token = _token(page)
        if not token:
            raise PluginError("chomikuj.pl: download token not found on the page (layout changed?)")
        name, size = _meta(page)
        answer = self._post(ctx, "/action/License/Download",
                            {"fileId": fid, "__RequestVerificationToken": token}, url)
        captcha_sent = False
        for _step in range(6):
            data = answer.get("Data") if isinstance(answer.get("Data"), dict) else {}
            redirect = answer.get("redirectUrl") or data.get("redirectUrl") or ""
            if redirect:
                return ResolveResult(url=urljoin(ORIGIN, str(redirect)), filename=name, size=size,
                                     headers={"referer": url})
            content = str(answer.get("Content") or "")
            if not answer.get("IsSuccess", True) and not content:
                msg = _text(str(answer.get("Message") or "")) or "download refused"
                raise PluginError(f"chomikuj.pl: {msg}")
            forms = {a.get("id", ""): (a, body) for a, body in _forms(content)}
            text = _text(content)
            if "notLoggedDownloadCaptchaForm" in forms:
                if captcha_sent:
                    raise PluginError("chomikuj.pl rejected the captcha answer")
                attrs, body = forms["notLoggedDownloadCaptchaForm"]
                fields = _inputs(body)
                sitekey = _sitekey(content)
                if not sitekey:
                    raise PluginError("chomikuj.pl: captcha key not found (layout changed?)")
                fields["g-recaptcha-response"] = ctx.solve_captcha(kind="recaptcha", sitekey=sitekey,
                                                                   site_url=url)
                fields["__RequestVerificationToken"] = token
                captcha_sent = True
                answer = self._post(ctx, attrs.get("action") or "/action/License/DownloadNotLoggedCaptchaEntered",
                                    fields, url)
                continue
            confirm = next((forms[f] for f in _CONFIRM_FORMS if f in forms), None)
            if confirm is not None:
                attrs, body = confirm
                fields = _inputs(body)
                fields["__RequestVerificationToken"] = token
                answer = self._post(ctx, attrs.get("action") or "/action/License/DownloadWarningAccept",
                                    fields, url)
                continue
            if re.search(r"wymaga opłacenia kosztów tra[n]?sferu|BuyAdditionalTransfer|"
                         r"brak wystarczającej ilości transferu|nie masz wystarczającej", content + " " + text,
                         re.I):
                if logged_in:
                    raise PaywallRequired(note="chomikuj.pl: not enough transfer on the account for this file")
                raise LoginRequired(hint="chomikuj.pl: files over 1 MB need a signed-in chomik account "
                                         "with transfer")
            if not logged_in and ("downloadLoginForm" in forms or re.search(r"Zaloguj się", text)):
                raise LoginRequired(hint="chomikuj.pl asks to sign in for this file")
            if re.search(r"FormAcceptStreamingOnlyFile|tylko do odtwarzania online", content, re.I):
                raise PaywallRequired(note="chomikuj.pl: this file may only be streamed, not downloaded")
            raise PluginError(f"chomikuj.pl: {text[:200] or 'download dialog not understood'}")
        raise PluginError("chomikuj.pl did not return a download link")
