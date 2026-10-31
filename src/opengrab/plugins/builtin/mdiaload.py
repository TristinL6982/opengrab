"""Mdia Load (down.mdiaload.com) file links, an XFileSharing site.

Seen on the live site (2026-10-31):

* ``https://down.mdiaload.com/<file_code>`` answers 302 to
  ``/download<N>`` and sets a ``file_code`` cookie (like FileKeeper).
* That page (template "download0") has two buttons: "Premium Download"
  (``?op=payments``) and "Free Download" (``/download61.html``), a further
  page of the free flow.
* The later steps are the usual XFileSharing ones: a form with
  ``op=download1``/``download2`` (``id``, ``rand``, ``method_free``), an
  optional countdown and captcha, and finally a redirect to (or a link on)
  the storage server.

The plugin walks those pages like a visitor: follows the "Free Download"
link, posts each ``op=download*`` form once, waits out countdowns of up to
90 s, sends captchas to ``ctx.solve_captcha``, and stops at the first link
that leaves the site's pages for a file server. Pages that are empty for a
missing file ("download0" with no file name and no form) give "file not
found". Premium: the account's cookies or login (``op=login``) are used when
saved for mdiaload.com; premium pages redirect straight to the file.
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import unquote, urljoin, urlsplit

from ..base import (GrabResult, LoginRequired, PaywallRequired, PluginContext, PluginError,
                    ResolveResult, SitePlugin)
from . import _hostflow as hf

SITE = "mdiaload.com"
HOST = "down.mdiaload.com"
BASE = "https://down.mdiaload.com"
CODE_RE = re.compile(r"^/([a-z0-9]{12})(?:/[^/]*)?(?:\.html?)?/?$", re.I)
PAGE_HOSTS = (HOST, SITE, "www." + SITE)
MAX_STEPS = 10
OFFLINE_RE = re.compile(r"File Not Found|No such file|file (?:was|has been) (?:removed|deleted)|"
                        r"file you were looking for could not be found", re.I)


def file_code(url: str) -> str | None:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or (p.hostname or "").lower() != HOST:
        return None
    m = CODE_RE.match(p.path)
    if not m or re.fullmatch(r"download\d*", m.group(1), re.I):
        return None
    return m.group(1).lower()


def _on_site(url: str) -> bool:
    return hf.host_of(url) in PAGE_HOSTS


def _looks_file(url: str) -> bool:
    path = urlsplit(url).path
    last = unquote(path.rsplit("/", 1)[-1])
    return bool(re.search(r"\.[A-Za-z0-9]{1,5}$", last)) and not last.lower().endswith((".html", ".htm", ".php"))


def _check(page: str) -> None:
    t = hf.text(page)
    wait = hf.wait_message(t)
    if wait:
        raise PluginError(f"Mdia Load free download limit: wait {max(1, -(-wait // 60))} minutes")
    if re.search(r"available for Premium Users only|only premium users can download|"
                 r"You can download files up to \d+\s*[KMG]b only", t, re.I):
        raise PaywallRequired(note="Mdia Load: this file needs a premium account")
    if re.search(r"reached the download.limit|download limit (?:reached|exceeded)", t, re.I):
        raise PluginError("Mdia Load download limit reached: wait 60 minutes")
    if re.search(r"Wrong captcha", t, re.I):
        raise PluginError("Mdia Load refused the captcha answer")


def _meta(page: str) -> tuple[str, int | None]:
    name = ""
    for pat in (r'<input[^>]*name="fname"[^>]*value="([^"]+)"',
                r'class="[^"]*(?:desc-filename|title-down-file|filename)[^"]*"[^>]*>\s*([^<]+?)\s*<',
                r"<h2>\s*Download File\s+([^<]+?)\s*</h2>"):
        m = re.search(pat, page or "", re.I)
        if m and m.group(1).strip():
            name = _html.unescape(m.group(1)).strip()
            break
    size = None
    m = re.search(r"\(\s*([\d.,]+\s*[KMGT]?B)\s*\)", hf.text(page)) or \
        re.search(r"Size\s*:?\s*([\d.,]+\s*[KMGT]?B)", hf.text(page), re.I)
    if m:
        size = hf.parse_size(m.group(1))
    return name, size


def _download_form(page: str, code: str):
    for a, fields, _body in hf.forms(page):
        if str(fields.get("op", "")).startswith("download"):
            if "password" in fields:
                raise LoginRequired(hint="this Mdia Load file is password protected")
            fields.setdefault("id", code)
            fields.pop("method_premium", None)
            if fields["op"] == "download1":
                fields.setdefault("method_free", "Free Download")
            return a.get("action", ""), fields
    return None


def _free_link(page: str, cur: str) -> str:
    for m in re.finditer(r"(?is)<a\b[^>]*>.*?</a>", page or ""):
        tag = m.group(0)
        a = hf.attrs(tag[: tag.find(">") + 1])
        href = a.get("href", "")
        label = hf.text(tag) + " " + a.get("title", "")
        if href and re.search(r"free\s*download|click\s*(?:here\s*)?to\s*download|continue", label, re.I):
            nxt = urljoin(cur, href)
            if _on_site(nxt) and "op=payments" not in nxt:
                return nxt
    return ""


def _file_link(page: str) -> str:
    for pat in (r'id="direct_link"[^>]*>\s*<a\b[^>]*href="([^"]+)"',
                r'<a\b[^>]*href="(https?://[^"]+)"[^>]*>\s*(?:<[^>]+>\s*)*(?:Direct Download|Download Now|Click here to download)'):
        m = re.search(pat, page or "", re.I)
        if m:
            link = _html.unescape(m.group(1))
            if not _on_site(link) or _looks_file(link):
                return link
    for m in re.finditer(r'href="(https?://[^"]+)"', page or ""):
        link = _html.unescape(m.group(1))
        if not _on_site(link) and _looks_file(link) and re.search(r"/d/|/files?/|/dl/|:\d+/", link):
            return link
    return ""


class MdiaLoadPlugin(SitePlugin):
    id = "mdiaload"
    name = "Mdia Load"
    site = SITE
    origin = "builtin"
    supports_captcha = True
    supports_login = True
    description = "down.mdiaload.com file links (XFileSharing: free pages, countdown, captcha)."

    def supports(self, url: str) -> bool:
        return file_code(url) is not None

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        code = file_code(url)
        if code is None:
            raise PluginError("not a Mdia Load file link")
        return GrabResult(title=code, site=SITE, plugin_id=self.id)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        code = file_code(url)
        if code is None:
            raise PluginError("not a Mdia Load file link")
        self._login(ctx)
        cur, method, data, referer = f"{BASE}/{code}", "GET", None, BASE + "/"
        name, size, posted, visited = "", None, set(), set()
        for _ in range(MAX_STEPS):
            visited.add(cur)
            r = ctx.client.request(method, cur, data=data, headers={"referer": referer}, follow=False)
            loc = (r.headers.get("location") or "").strip()
            if r.status_code in hf.REDIRECTS and loc:
                nxt = urljoin(cur, loc)
                if not _on_site(nxt) or _looks_file(nxt):
                    return ResolveResult(url=nxt, filename=name, size=size, headers={"referer": cur})
                cur, method, data, referer = nxt, "GET", None, cur
                continue
            if r.status_code in (404, 410):
                raise PluginError("Mdia Load file is offline (not found or deleted)")
            if r.status_code >= 400:
                raise PluginError(f"Mdia Load answered HTTP {r.status_code}")
            ct = (r.headers.get("content-type") or "").lower()
            if ct and not ct.startswith("text/html"):
                return ResolveResult(url=cur, filename=name, size=size, headers={"referer": referer})
            page = r.text
            if OFFLINE_RE.search(hf.text(page)[:400]) and not _download_form(page, code):
                raise PluginError("Mdia Load file is offline (not found or deleted)")
            _check(page)
            n2, s2 = _meta(page)
            name, size = name or n2, size or s2
            link = _file_link(page)
            if link:
                return ResolveResult(url=link, filename=name, size=size, headers={"referer": cur})
            form = _download_form(page, code)
            if form and form[1]["op"] not in posted:
                action, fields = form
                posted.add(fields["op"])
                fields.update(hf.answer_captcha(ctx, hf.captcha(page, cur), cur))
                hf.wait_countdown(hf.countdown(page), "Mdia Load")
                cur, method, data, referer = urljoin(cur, action or cur), "POST", fields, cur
                continue
            nxt = _free_link(page, cur)
            if nxt and nxt not in visited:
                cur, method, data, referer = nxt, "GET", None, cur
                continue
            if not name and not form:
                raise PluginError("Mdia Load file is offline (the page shows no file)")
            raise PluginError("Mdia Load page has no download step the plugin knows "
                              "(the site may have changed)")
        raise PluginError("Mdia Load download did not finish (too many steps)")

    def _login(self, ctx: PluginContext) -> None:
        acc = ctx.account_for(site=SITE) if ctx is not None else None
        user = getattr(acc, "username", "") if acc is not None else ""
        pw = getattr(acc, "password", "") if acc is not None else ""
        if not (user and pw):
            return
        r = ctx.client.post(f"{BASE}/", data={"op": "login", "login": user, "password": pw,
                                              "redirect": ""},
                            headers={"referer": f"{BASE}/login.html"}, follow=False)
        if r.status_code not in hf.REDIRECTS and re.search(r"Incorrect Login|Wrong login", hf.text(r.text), re.I):
            raise LoginRequired(hint="Mdia Load rejected the saved login")
