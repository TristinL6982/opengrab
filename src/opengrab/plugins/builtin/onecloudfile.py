"""1Cloud File (1cloudfile.com) single-file links.

The site runs the YetiShare file-hosting script ("spirit" theme). Its API
(1cloudfile.com/api, ``/api/v2/``: authorize with key1/key2, file/upload,
file/info, folder/*) only manages files in the caller's own account and has
no download call for other people's links, so downloads use the page:

* ``https://1cloudfile.com/<shortcode>`` (optionally ``/<name>``) shows the
  file page. Free visitors get a countdown; afterwards the page's button
  links to ``/<shortcode>?pt=<token>`` (newer versions:
  ``?download_token=<token>``), a token the server put in the page.
* That URL either redirects to the storage server (the file), or shows a
  captcha form (reCAPTCHA/hCaptcha/Turnstile or an image) that is answered
  through ``ctx.solve_captcha`` and posted back (``submitted=1``, ``d=1``).
* Missing files answer 404 or redirect to ``/error?e=...``; "You must wait"
  limits are reported in minutes.

Countdowns up to 90 s are waited out; longer ones are reported. Premium
accounts (login + password saved for 1cloudfile.com) sign in through the
site's login form first; premium file pages redirect straight to the file.
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from ..base import (GrabResult, LoginRequired, PaywallRequired, PluginContext, PluginError,
                    ResolveResult, SitePlugin)
from . import _hostflow as hf

SITE = "1cloudfile.com"
BASE = "https://1cloudfile.com"
CODE_RE = re.compile(r"^/([A-Za-z0-9]{2,16})(?:/[^/?#]*)?/?$")
RESERVED = {"faq", "api", "register", "login", "logout", "account", "upgrade", "terms",
            "privacy", "contact", "report_file", "link_checker", "explore", "folder",
            "themes", "error", "index", "upload", "search", "premium", "ajax", "core",
            "plugins", "files", "share", "page", "blog", "dmca", "abuse"}
OFFLINE_RE = re.compile(r"file (?:has been|was) (?:removed|deleted)|file not found|"
                        r"could not be found|no longer available|page not found", re.I)
MAX_STEPS = 6


def parse(url: str) -> tuple[str, str] | None:
    """(shortcode, filename) for a 1cloudfile file link."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or (p.hostname or "").lower() not in (SITE, "www." + SITE):
        return None
    m = CODE_RE.match(p.path)
    if not m or m.group(1).lower() in RESERVED:
        return None
    parts = [x for x in p.path.split("/") if x]
    return m.group(1), unquote(parts[1]) if len(parts) > 1 else ""


def _is_site(url: str) -> bool:
    return hf.host_of(url) in (SITE, "www." + SITE)


def next_link(page: str, code: str) -> str:
    """The token link the page's download button points at."""
    m = re.search(r"""(https?://(?:www\.)?1cloudfile\.com/%s\?(?:pt|download_token)=[^"'\s<>]+)""" % re.escape(code),
                  page, re.I) or \
        re.search(r"""["'](/%s\?(?:pt|download_token)=[^"'\s<>]+)""" % re.escape(code), page, re.I)
    return urljoin(BASE, _html.unescape(m.group(1))) if m else ""


def page_meta(page: str) -> tuple[str, int | None]:
    name = ""
    for pat in (r'<meta\s+property="og:title"\s+content="([^"]+)"',
                r"<title>\s*(?:Download\s+)?([^<]+?)\s*-\s*1Cloud File\s*</title>",
                r'class="[^"]*file-?name[^"]*"[^>]*>\s*([^<]+?)\s*<'):
        m = re.search(pat, page or "", re.I)
        if m:
            v = _html.unescape(m.group(1)).strip()
            if v and not OFFLINE_RE.search(v) and v.lower() not in ("404", "error", "upload files"):
                name = v
                break
    size = None
    m = re.search(r"\(\s*([\d.,]+\s*[KMGT]?B)\s*\)", hf.text(page)) or \
        re.search(r"(?:File )?size\s*:?\s*([\d.,]+\s*[KMGT]?B)", hf.text(page), re.I)
    if m:
        size = hf.parse_size(m.group(1))
    return name, size


def _check(page: str) -> None:
    t = hf.text(page)
    wait = hf.wait_message(t)
    if wait:
        raise PluginError(f"1Cloud File free download limit: wait {max(1, -(-wait // 60))} minutes")
    if re.search(r"only available to premium|premium (?:account|users?) only|upgrade your account to download", t, re.I):
        raise PaywallRequired(note="1Cloud File: this file needs a premium account")
    if re.search(r"file is password protected|enter (?:the )?(?:file )?password", t, re.I) and \
            re.search(r'name="filePassword"|name="password"', page, re.I):
        raise LoginRequired(hint="this 1Cloud File file is password protected")


class OneCloudFilePlugin(SitePlugin):
    id = "1cloudfile"
    name = "1Cloud File"
    site = SITE
    origin = "builtin"
    supports_captcha = True
    supports_login = True
    description = "1cloudfile.com file links (free countdown/captcha flow, premium login)."

    def supports(self, url: str) -> bool:
        return parse(url) is not None

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        parsed = parse(url)
        if parsed is None:
            raise PluginError("not a 1Cloud File link")
        code, name = parsed
        return GrabResult(title=name or code, filename=name, site=SITE, plugin_id=self.id)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        parsed = parse(url)
        if parsed is None:
            raise PluginError("not a 1Cloud File link")
        code, name = parsed
        self._login(ctx)
        cur, method, data, referer = f"{BASE}/{code}", "GET", None, BASE + "/"
        size = None
        for _ in range(MAX_STEPS):
            if method == "GET" and re.search(r"[?&](?:pt|download_token)=", cur):
                # the token URL may BE the file: look first, never pull the body
                h = ctx.client.request("HEAD", cur, headers={"referer": referer}, follow=False)
                hct = (h.headers.get("content-type") or "").lower()
                if h.status_code < 300 and hct and not hct.startswith("text/html"):
                    cl = h.headers.get("content-length") or ""
                    fname = hf._filename_from_cd(h.headers.get("content-disposition") or "") or name
                    return ResolveResult(url=cur, filename=fname,
                                         size=int(cl) if cl.isdigit() else size,
                                         headers={"referer": referer})
            r = ctx.client.request(method, cur, data=data, headers={"referer": referer}, follow=False)
            loc = (r.headers.get("location") or "").strip()
            if r.status_code in hf.REDIRECTS and loc:
                nxt = urljoin(cur, loc)
                if not _is_site(nxt):
                    return ResolveResult(url=nxt, filename=name, size=size,
                                         headers={"referer": BASE + "/"})
                if urlsplit(nxt).path.startswith("/error"):
                    msg = (parse_qs(urlsplit(nxt).query).get("e") or [""])[0]
                    _check(msg)
                    raise PluginError(f"1Cloud File: {msg or 'file not found'}")
                if urlsplit(nxt).path.rstrip("/") in ("", "/index"):
                    raise PluginError("1Cloud File file is offline (sent back to the home page)")
                cur, method, data, referer = nxt, "GET", None, cur
                continue
            if r.status_code in (404, 410):
                raise PluginError("1Cloud File file is offline (not found or deleted)")
            if r.status_code >= 400:
                raise PluginError(f"1Cloud File answered HTTP {r.status_code}")
            ct = (r.headers.get("content-type") or "").lower()
            if not ct.startswith("text/html") and ct:
                return ResolveResult(url=cur, filename=name, size=size, headers={"referer": referer})
            page = r.text
            _check(page)
            n2, s2 = page_meta(page)
            name, size = name or n2, size or s2
            cap = hf.captcha(page, cur)
            form = self._captcha_form(page, cur) if cap else None
            if form:
                action, fields = form
                fields.update(hf.answer_captcha(ctx, cap, cur))
                cur, method, data, referer = action, "POST", fields, cur
                continue
            link = next_link(page, code)
            if link and link != cur:
                hf.wait_countdown(hf.countdown(page), "1Cloud File")
                cur, method, data, referer = link, "GET", None, cur
                continue
            if OFFLINE_RE.search(hf.text(page)) and not link:
                raise PluginError("1Cloud File file is offline (not found or deleted)")
            raise PluginError("1Cloud File page has no download button the plugin knows "
                              "(the site may have changed)")
        raise PluginError("1Cloud File download did not finish (too many steps)")

    @staticmethod
    def _captcha_form(page: str, cur: str):
        for a, fields, body in hf.forms(page):
            if re.search(r"captcha|data-sitekey", body, re.I) or "submitted" in fields:
                fields.setdefault("submitted", "1")
                fields.setdefault("d", "1")
                return urljoin(cur, a.get("action") or cur), fields
        return None

    def _login(self, ctx: PluginContext) -> None:
        acc = ctx.account_for(site=SITE) if ctx is not None else None
        user = getattr(acc, "username", "") if acc is not None else ""
        pw = getattr(acc, "password", "") if acc is not None else ""
        if not (user and pw):
            return
        r = ctx.client.post(f"{BASE}/account/login",
                            data={"username": user, "password": pw, "submitme": "1"},
                            headers={"referer": f"{BASE}/account/login"}, follow=False)
        loc = r.headers.get("location") or ""
        if r.status_code in hf.REDIRECTS and "login" not in loc:
            return
        if re.search(r"invalid|incorrect|failed", hf.text(r.text), re.I):
            raise LoginRequired(hint="1Cloud File rejected the saved login")
