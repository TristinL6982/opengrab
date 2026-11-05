"""Shared download flow for file hosts built on XFileSharing (XFS).

Hundreds of file hosts run the same off-the-shelf XFS site software, so
their download pages share one shape:

1. The file page (``/<12-char code>`` or ``/<code>/<name>``) carries a
   "Free Download" form with ``op=download1``, ``id`` and ``method_free``.
2. Posting it answers the second step: a form with ``op=download2``,
   ``id``, ``rand`` and often a countdown and a captcha (Turnstile,
   hCaptcha, reCAPTCHA, an image, or digits drawn with CSS positions).
3. Posting that answers the file: a redirect, JSON ``{"url": ...}``, or a
   page with the link (``https://<server>/d/<hash>/<name>``, ``/files/``,
   ``/dl/``, ``/cgi-bin/dl.cgi/``), sometimes inside packed JavaScript.

``resolve`` walks those steps for any XFS site. A site with quirks gets a
small ``Profile`` (a fixed referer, extra form fields, its captcha kind)
instead of a plugin of its own; ``looks_like`` lets the generic page plugin
hand unknown XFS sites to the same flow.

Not a plugin module (the leading underscore keeps the registry from loading it).
"""
from __future__ import annotations

import html as _html
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit

from ..base import PluginContext, PluginError, ResolveResult
from ._hostflow import forms, leaves_public_net, text as page_text, wait_message

MAX_WAIT = 90                 # longest countdown the flow sleeps through
MAX_STEPS = 4                 # page -> download1 -> download2 -> link, plus one spare
sleep = time.sleep            # tests replace this
clock = time.monotonic
REDIRECTS = (301, 302, 303, 307, 308)

LINK_RE = re.compile(r'https?://[^\s"\'<>]+?/(?:d|dl|files|cgi-bin/dl\.cgi)/[^\s"\'<>]+', re.I)
OFFLINE_RE = re.compile(r"no such file|file not found|file was (?:removed|deleted)|"
                        r"has been removed|file (?:has )?expired|file is no longer available", re.I)
PREMIUM_RE = re.compile(r"available for premium users only|only premium users can download|"
                        r"can download files up to|upgrade your account to download", re.I)


@dataclass
class Profile:
    """What makes one XFS site differ from the rest."""
    name: str                                   # shown in messages
    referer: str = ""                           # sent with every page request (some sites check it)
    captcha_kind: str = ""                      # kind for a bare data-sitekey with no widget class
    download2: dict = field(default_factory=dict)   # extra fields for the op=download2 post
    cookies: dict = field(default_factory=lambda: {"lang": "english"})
    max_wait: int = MAX_WAIT


# ---------------------------------------------------------------------------
# page reading
# ---------------------------------------------------------------------------
def _attr(page: str, name: str) -> str:
    """Value of input ``name`` or of an attribute ``name="..."`` / Vue ``:name="'x'"``."""
    for pat in (rf'<input[^>]*name="{name}"[^>]*value="([^"]*)"',
                rf'<input[^>]*value="([^"]*)"[^>]*name="{name}"',
                rf':{name}="\'([^\']*)\'"',
                rf'(?<![\w:-]){name}="([^"]*)"'):
        m = re.search(pat, page, re.I)
        if m:
            return _html.unescape(m.group(1))
    return ""


def _input(page: str, name: str) -> str:
    """Value of the form input ``name`` only (``id`` is also every element's HTML id)."""
    for pat in (rf'<input[^>]*name="{name}"[^>]*value="([^"]*)"',
                rf'<input[^>]*value="([^"]*)"[^>]*name="{name}"'):
        m = re.search(pat, page, re.I)
        if m:
            return _html.unescape(m.group(1))
    return ""


def with_hidden_html(page: str) -> str:
    """The page plus widget HTML some sites keep escaped in ``captcha-html="..."``."""
    extra = [_html.unescape(x) for x in re.findall(r'captcha-html="([^"]*)"', page, re.I)]
    return "\n".join([page, *extra])


def download_forms(page: str, page_url: str) -> list[tuple[str, dict]]:
    """[(action URL, fields)] of the forms with op=download1/download2/... ."""
    out = []
    for a, fields, _inner in forms(page):
        if re.fullmatch(r"download\d?", fields.get("op", ""), re.I) or a.get("name") == "F1":
            out.append((urljoin(page_url, a.get("action") or page_url), fields))
    return out


def looks_like(page: str) -> bool:
    """True for a page from an XFS site: a download form that names the file by id."""
    return any("id" in f for _a, f in download_forms(page, "https://x/"))


def countdown(page: str) -> int:
    for pat in (r'id="countdown_str"[^>]*>.*?<span[^>]*>\s*(\d+)\s*<',
                r'class="seconds"[^>]*>\s*(\d+)',
                r'(?<![\w-])countdown="(\d+)"',
                r':(?:seconds|countdown|timer)="(\d+)"',
                r'id="countdown"[^>]*>\s*(?:<[^>]+>\s*)*(\d+)'):
        m = re.search(pat, page or "", re.I | re.S)
        if m:
            return int(m.group(1))
    return 0


def file_name(page: str) -> str:
    for pat in (r'<input[^>]*name="fname"[^>]*value="([^"]+)"',
                r'(?<![\w:-])file-name="([^"]+)"',
                r'<h\d[^>]*class="[^"]*(?:file-?name|filename)[^"]*"[^>]*>\s*([^<]+?)\s*<',
                r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"'):
        m = re.search(pat, page or "", re.I)
        if m:
            name = _html.unescape(m.group(1)).strip()
            if name and name.lower() not in ("download", "download file"):
                return name
    return ""


def css_digits(page: str) -> str:
    """The code of an XFS "type these digits" captcha drawn with CSS positions."""
    spans = re.findall(r'<span[^>]*padding-left:\s*(\d+)px[^>]*>\s*(&#\d+;|\w)\s*</span>', page or "", re.I)
    return "".join(_html.unescape(ch) for _x, ch in sorted(spans, key=lambda s: int(s[0])))


def unpack_js(page: str) -> str:
    """Text of every ``eval(function(p,a,c,k,e,d)...)`` packed script, unpacked."""
    out = []
    for m in re.finditer(r"\}\('(.*?)',\s*(\d+),\s*(\d+),\s*'(.*?)'\.split\('\|'\)", page or "", re.S):
        payload, radix, count, words = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).split("|")
        if not 2 <= radix <= 62:
            continue
        digits = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"[:radix]

        def word(w: re.Match) -> str:
            n = 0
            for ch in w.group(0):
                if ch not in digits:
                    return w.group(0)
                n = n * radix + digits.index(ch)
            return words[n] if n < min(count, len(words)) and words[n] else w.group(0)
        out.append(re.sub(r"\b\w+\b", word, payload.replace("\\'", "'")))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# answers
# ---------------------------------------------------------------------------
def _is_file_link(url: str, site_host: str) -> bool:
    """A redirect target is the file unless it is another page of the site itself."""
    if not url.startswith(("http://", "https://")):
        return False
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.") != site_host.removeprefix("www.") or bool(LINK_RE.fullmatch(url))


def find_link(r, page: str, site_host: str) -> str:
    """The file's address in an answer, or ""."""
    loc = (r.headers.get("location") or "").strip()
    if r.status_code in REDIRECTS and loc:
        nxt = urljoin(getattr(r, "url", "") or "", loc)
        if _is_file_link(nxt, site_host):
            return nxt
    try:
        body = json.loads(page)
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("url"), str) and body["url"]:
        return unquote(body["url"])
    for text in (page, unpack_js(page)):
        for m in LINK_RE.finditer(_html.unescape(text or "")):
            if not re.search(r"\.(?:png|jpe?g|gif|svg|ico|webp|css|js)$", m.group(0), re.I):
                return m.group(0)
    return ""


def check_errors(page: str, name: str) -> None:
    low = (page or "").lower()
    if "not allowed from domain you" in low:
        raise PluginError(f"{name} refused the request (it did not accept where it came from)")
    if "wrong captcha" in low or "verification failed" in low:
        raise PluginError(f"{name} refused the captcha answer")
    if "skipped countdown" in low:
        raise PluginError(f"{name} says the countdown was skipped; try again")
    secs = wait_message(page_text(page))
    if secs or re.search(r"download limit|reached the limit|all download slots", low):
        secs = secs or 3600
        raise PluginError(f"{name} download limit reached: wait {max(1, -(-secs // 60))} minutes")
    if PREMIUM_RE.search(low):
        raise PluginError(f"{name} lets only premium accounts download this file")
    if "maintenance" in low and "<form" not in low:
        raise PluginError(f"{name} is down for maintenance; try again later")


def captcha_fields(ctx: PluginContext, page: str, page_url: str, profile: Profile) -> dict:
    """Form fields carrying a captcha answer (empty when the step has none)."""
    full = with_hidden_html(page)
    key = _attr(full, "data-sitekey")
    if key:
        kind = ("turnstile" if re.search(r"cf-turnstile|challenges\.cloudflare\.com/turnstile", full, re.I)
                else "hcaptcha" if re.search(r"h-captcha|hcaptcha\.com", full, re.I)
                else "recaptcha" if re.search(r"g-recaptcha|google\.com/recaptcha", full, re.I)
                else profile.captcha_kind or "turnstile")
        field_ = {"turnstile": "cf-turnstile-response", "hcaptcha": "h-captcha-response",
                  "recaptcha": "g-recaptcha-response"}[kind]
        token = ctx.solve_captcha(kind=kind, sitekey=key, site_url=page_url)
        out = {field_: token}
        if kind == "hcaptcha" and "g-recaptcha" in full:
            out["g-recaptcha-response"] = token
        return out
    digits = css_digits(page)
    if digits:
        return {"code": digits}
    m = re.search(r'<img\b[^>]*src="([^"]*/captchas?/[^"]*)"', page, re.I)
    if m:
        img = urljoin(page_url, _html.unescape(m.group(1)))
        return {"code": ctx.solve_captcha(image_url=img, kind="text", site_url=page_url)}
    return {}


def _fill(fields: dict, page: str, code: str, profile: Profile) -> dict:
    fields = dict(fields)
    op = fields.get("op", "")
    fields.setdefault("id", code)
    if op == "download1":
        fields.pop("method_premium", None)
        fields["method_free"] = fields.get("method_free") or "Free Download"
    else:
        fields["method_free"] = fields.get("method_free") or "Free Download >>"
        fields.setdefault("method_premium", "")
        for name, attr in (("rand", "rand"), ("dl_token", "dl-token")):
            if not fields.get(name):
                v = _attr(page, attr) or _attr(page, name)
                if v:
                    fields[name] = v
        fields.update(profile.download2)
    if "adblock_detected" in fields:
        fields["adblock_detected"] = "0"
    return fields


def _built_form(page: str, page_url: str, code: str) -> list[tuple[str, dict]]:
    """Sites whose second form is drawn by JavaScript leave its values as attributes."""
    rand = _attr(page, "rand")
    if not rand:
        return []
    base = f"{urlsplit(page_url).scheme}://{urlsplit(page_url).netloc}"
    return [(page_url if page_url.rstrip("/") != base else base + "/download",
             {"op": "download2", "id": _input(page, "id") or code, "rand": rand,
              "referer": _attr(page, "referer")})]


# ---------------------------------------------------------------------------
# the flow
# ---------------------------------------------------------------------------
def resolve(url: str, ctx: PluginContext, profile: Profile, code: str = "",
            name: str = "", wait_fn=None) -> ResolveResult:
    """Walk an XFS site's free download steps to the file's address.

    ``wait_fn`` sleeps a countdown (default: ``sleep``; plugins pass their own
    so their tests can replace it)."""
    wait_fn = wait_fn or (lambda s: sleep(s))
    p = urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    site_host = (p.hostname or "").lower()
    code = code or next((x for x in p.path.split("/") if x), "")
    if hasattr(ctx.client, "set_cookie"):
        for k, v in profile.cookies.items():
            ctx.client.set_cookie(base, k, v)
    ref = {"referer": profile.referer} if profile.referer else {}
    r = ctx.client.get(url, headers=ref)
    page, page_url = r.text, r.url or url
    if r.status_code == 404 or (not download_forms(page, page_url) and OFFLINE_RE.search(page)):
        raise PluginError(f"{profile.name} file is offline (not found or deleted)")
    if r.status_code >= 400:
        raise PluginError(f"{profile.name} page answered HTTP {r.status_code}")
    name = name or file_name(page)
    for _step in range(MAX_STEPS):
        link = find_link(r, page, site_host)
        if link:
            if leaves_public_net(page_url, link):
                raise PluginError(f"{profile.name} sent the download to a local network address; "
                                  "OpenGrab did not follow it")
            return ResolveResult(url=link, filename=name, headers={"referer": base + "/"})
        check_errors(page, profile.name)
        found = download_forms(page, page_url) or _built_form(page, page_url, code)
        if not found:
            raise PluginError(f"{profile.name} needs the browser download flow "
                              f"(no download form; last answer HTTP {r.status_code})")
        # the step furthest along wins (a page can carry both forms)
        action, fields = max(found, key=lambda f: f[1].get("op", ""))
        fields = _fill(fields, page, code, profile)
        name = name or fields.get("fname", "")
        wait = countdown(page)
        if wait > profile.max_wait:
            raise PluginError(f"{profile.name} asks to wait {max(1, -(-wait // 60))} minutes")
        t0 = clock()
        fields.update(captcha_fields(ctx, page, page_url, profile))
        left = wait - int(clock() - t0)   # time spent on the captcha counts
        if left > 0:
            wait_fn(left)
        r = ctx.client.post(action, data=fields, follow=False,
                            headers={"referer": page_url, "origin": base})
        page = r.text
        loc = (r.headers.get("location") or "").strip()
        if r.status_code in REDIRECTS and loc and not _is_file_link(urljoin(action, loc), site_host):
            r = ctx.client.get(urljoin(action, loc), headers={"referer": page_url})
            page = r.text
        page_url = r.url or action
    raise PluginError(f"{profile.name} needs the browser download flow (too many steps)")
