"""Shared helpers for small file-host plugins (1cloudfile, mdiaload,
Buzzheavier, zetrrent, GoFile storage links, signed server links).

Not a plugin module (the leading underscore keeps the registry from loading
it). Everything here is page parsing and one careful probe of a direct link:

* ``probe_direct`` sends ONE ``HEAD`` request (never a body download) to a
  storage-server link and turns the answer into a ``ResolveResult`` or a
  clear ``PluginError``. Signed links that expired answer 404/410, a web
  page, or a redirect back to the host's website; all of these give
  "this download link has expired; add the file's page link instead".
"""
from __future__ import annotations

import html as _html
import ipaddress
import mimetypes
import re
import time
from urllib.parse import unquote, urljoin, urlsplit

from ..base import CaptchaRequired, PluginError, ResolveResult
from .direct import _filename_from_cd

EXPIRED = "this download link has expired; add the file's page link instead"
MAX_WAIT = 90                 # longest countdown the plugins sleep through
sleep = time.sleep            # tests replace this
REDIRECTS = (301, 302, 303, 307, 308)

WIDGETS = (("cf-turnstile", "turnstile", "cf-turnstile-response"),
           ("h-captcha", "hcaptcha", "h-captcha-response"),
           ("g-recaptcha", "recaptcha", "g-recaptcha-response"))


# ---------------------------------------------------------------------------
# page parsing
# ---------------------------------------------------------------------------
def attrs(tag: str) -> dict[str, str]:
    return {k.lower(): _html.unescape(v if v is not None else w)
            for k, v, w in re.findall(r'([\w:.-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', tag)}


def text(page: str) -> str:
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def inputs(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in re.findall(r"(?is)<input\b[^>]*>", body):
        a = attrs(tag)
        name = a.get("name")
        if not name:
            continue
        typ = a.get("type", "text").lower()
        if typ in ("checkbox", "radio") and "checked" not in tag.lower():
            continue
        if typ in ("submit", "button", "image") and name in out:
            continue
        out[name] = a.get("value", "")
    return out


def forms(page: str) -> list[tuple[dict, dict, str]]:
    """[(form attrs, input fields, inner html)] for every <form>."""
    return [(attrs(m.group(1)), inputs(m.group(2)), m.group(2))
            for m in re.finditer(r"(?is)<form\b([^>]*)>(.*?)</form>", page or "")]


def captcha(page: str, base: str):
    """('widget', kind, sitekey, field) / ('image', url, field) / None."""
    for m in re.finditer(r"(?is)<div\b[^>]*data-sitekey\s*=[^>]*>", page or ""):
        a = attrs(m.group(0))
        for marker, kind, field in WIDGETS:
            if marker in a.get("class", ""):
                return ("widget", kind, a.get("data-sitekey", ""), field)
    m = re.search(r'<img\b[^>]*src="([^"]*captcha[^"]*)"', page or "", re.I)
    if m and re.search(r'name="(?:code|captcha_code|captcha)"', page, re.I):
        field = re.search(r'name="(code|captcha_code|captcha)"', page, re.I).group(1)
        return ("image", urljoin(base, _html.unescape(m.group(1))), field)
    return None


def answer_captcha(ctx, cap, page_url: str) -> dict[str, str]:
    """Form fields carrying the solver chain's answer (no own solving)."""
    if cap is None:
        return {}
    if cap[0] == "widget":
        _, kind, sitekey, field = cap
        return {field: ctx.solve_captcha(kind=kind, sitekey=sitekey, site_url=page_url)}
    _, img, field = cap
    return {field: ctx.solve_captcha(image_url=img, kind="text", site_url=page_url)}


def countdown(page: str) -> int:
    for pat in (r'data-(?:countdown|seconds|timer)="(\d+)"',
                r'id="(?:countdown_str|countdown|cxc|download-timer|timer)"[^>]*>\D{0,60}?(\d+)',
                r'class="(?:seconds|countdown)"[^>]*>\s*(\d+)',
                r'\bvar\s+(?:seconds|countdown|timer|wait|counter)\s*=\s*(\d+)\s*;'):
        m = re.search(pat, page or "", re.I)
        if m:
            return int(m.group(1))
    return 0


def wait_countdown(seconds: int, label: str) -> None:
    """Sleep a server-enforced countdown; longer ones are reported."""
    if seconds > MAX_WAIT:
        raise PluginError(f"{label} asks to wait {max(1, -(-seconds // 60))} minutes")
    if seconds > 0:
        sleep(seconds)


_UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def parse_size(s: str) -> int | None:
    m = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\b", s or "", re.I)
    if not m:
        return None
    unit = m.group(2).upper().replace("IB", "B")
    return int(float(m.group(1).replace(",", ".")) * _UNITS[unit])


def wait_message(page_text: str) -> int | None:
    """Seconds asked by "You have to wait 1 hour, 5 minutes, 3 seconds"."""
    m = re.search(r"(?:You have to|You must) wait\s+(?:(\d+)\s*hours?,?\s*)?"
                  r"(?:(\d+)\s*minutes?,?\s*)?(?:(\d+)\s*seconds?)?", page_text or "", re.I)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_local_host(host: str) -> bool:
    """True for localhost and loopback, private, link-local or unspecified IPs."""
    host = (host or "").strip("[]").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified


def leaves_public_net(src_url: str, dst_url: str) -> bool:
    """True when a link found on (or redirected from) a public site points at
    this computer or the local network. Links the user added themselves to a
    LAN address are not affected: src_url is then local too."""
    return is_local_host(host_of(dst_url)) and not is_local_host(host_of(src_url))


SECRET_HEADERS = ("cookie", "authorization")


def name_from_url(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


# ---------------------------------------------------------------------------
# storage-server links
# ---------------------------------------------------------------------------
def probe_direct(url: str, ctx, headers: dict | None = None, *, expired: str = EXPIRED,
                 is_site_page=None, hops: int = 3) -> ResolveResult:
    """HEAD the link (no body) and decide: a file, or an expired link.

    ``is_site_page(url)`` says whether a redirect target is the host's
    website (= the server refused the link and sent the visitor back)."""
    headers = dict(headers or {})
    cur = url
    for _ in range(hops + 1):
        host = host_of(cur)
        try:
            r = ctx.client.request("HEAD", cur, headers=headers, follow=False,
                                   timeout=getattr(getattr(ctx, "settings", None), "timeout_s", None))
        except CaptchaRequired:
            raise                      # a challenge page: the engine parks the link
        except Exception as e:
            raise PluginError(f"{expired} ({host} did not answer: {type(e).__name__})")
        status = r.status_code
        loc = (r.headers.get("location") or "").strip()
        if status in REDIRECTS and loc:
            nxt = urljoin(cur, loc)
            if is_site_page is not None and is_site_page(nxt):
                raise PluginError(expired)
            if leaves_public_net(cur, nxt):
                raise PluginError(f"{host} redirected the download to a local network "
                                  "address; OpenGrab did not follow it")
            if host_of(nxt) != host:
                # account cookies and tokens stay with the host they were meant for
                headers = {k: v for k, v in headers.items() if k.lower() not in SECRET_HEADERS}
            cur = nxt
            continue
        if status in (404, 410):
            raise PluginError(expired)
        if status in (401, 403):
            raise PluginError(f"{expired} (the server refused it: HTTP {status})")
        ct = (r.headers.get("content-type") or "").lower()
        cd = r.headers.get("content-disposition") or ""
        if status < 400 and ct.startswith("text/html") and "attachment" not in cd.lower():
            raise PluginError(expired)
        # 405/501/5xx on HEAD: many file servers only answer GET; the
        # downloader probes with its own ranged GET.
        fname = _filename_from_cd(cd) or name_from_url(cur)
        if fname and "." not in fname and ct and not ct.startswith(("application/octet-stream", "text/")):
            fname += mimetypes.guess_extension(ct.split(";", 1)[0].strip()) or ""
        cl = r.headers.get("content-length") or ""
        size = int(cl) if status < 400 and cl.isdigit() and int(cl) > 0 else None
        return ResolveResult(url=cur, filename=fname, size=size, headers=headers,
                             extra={"http_probe_status": status})
    raise PluginError(f"{expired} (too many redirects)")
