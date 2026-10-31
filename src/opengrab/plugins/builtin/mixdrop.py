"""
MixDrop (mixdrop.co/.ag/.top/.to/.ps/.si/.sx/.bz and mirrors such as
mxdrop.*, mixdrp.*, m1xdrop.*): video file pages /f/<id> and embeds /e/<id>.

The embed page builds the media URL in JavaScript packed with Dean Edwards'
p.a.c.k.e.r ("eval(function(p,a,c,k,e,d){...}"). The plugin unpacks that
script (a plain dictionary substitution, the same thing the browser does)
and reads the URL the page would hand to the player (MDCore.wurl). The
file server checks the Referer, so the embed URL is sent along.
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import urljoin, urlsplit

from ..base import GrabResult, PluginError, ResolveResult, SitePlugin

SITE = "mixdrop.co"
_HOST = re.compile(r"^(?:www\.)?(?:mixdrop|mixdrp|mixdroop|m1xdrop|mxdrop|mixdropjmk)\d*\.[a-z]{2,8}$", re.I)
_PATH = re.compile(r"^/(f|e|emb)/([a-z0-9]{6,20})(?:/[^?#]*)?/?$", re.I)


def parse_url(url: str) -> tuple[str, str, str] | None:
    """(scheme://host, kind, id) for a MixDrop file or embed link."""
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not _HOST.match(host):
        return None
    m = _PATH.match(p.path)
    if not m:
        return None
    return f"https://{host}", m.group(1).lower(), m.group(2)


# ---------------------------------------------------------------------------
# p.a.c.k.e.r unpacking
# ---------------------------------------------------------------------------
_PACKED = re.compile(
    r"\}\s*\(\s*'((?:[^'\\]|\\.)*)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'((?:[^'\\]|\\.)*)'\s*\.split\(\s*'\|'\s*\)",
    re.S)
_ALPHA62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _unbase(word: str, base: int) -> int:
    if base <= 36:
        return int(word, base)
    if base <= 62:
        n = 0
        for ch in word:
            n = n * base + _ALPHA62.index(ch)
        return n
    # bases above 62 use characters from chr(29 + digit) beyond the alphabet
    n = 0
    for ch in word:
        d = _ALPHA62.index(ch) if ch in _ALPHA62 else ord(ch) - 29
        n = n * base + d
    return n


def unpack(source: str) -> str:
    """Unpack every p.a.c.k.e.r block in `source`; '' when there is none."""
    out = []
    for m in _PACKED.finditer(source):
        payload = m.group(1).replace("\\'", "'").replace("\\\\", "\\")
        base, count = int(m.group(2)), int(m.group(3))
        words = m.group(4).replace("\\'", "'").split("|")
        if len(words) < count:
            words += [""] * (count - len(words))

        def sub(w: re.Match) -> str:
            tok = w.group(0)
            try:
                i = _unbase(tok, base)
            except (ValueError, IndexError):
                return tok
            return words[i] if i < len(words) and words[i] else tok

        out.append(re.sub(r"\b\w+\b", sub, payload))
    return "\n".join(out)


def _media_url(script: str) -> str:
    for key in ("wurl", "vsrc", "furl", "srcurl"):
        m = re.search(rf"MDCore\.{key}\s*=\s*['\"]([^'\"]+)['\"]", script)
        if m:
            u = m.group(1)
            return "https:" + u if u.startswith("//") else u
    return ""


def _offline(status: int, page: str) -> bool:
    if status in (404, 410):
        return True
    return bool(re.search(r"can(?:'|&#039;|&#39;)?t find the (?:video|file)|"
                          r"File (?:not found|has been deleted|was deleted)",
                          page, re.I))


def _title(page: str) -> str:
    for pat in (r'<div class="title">\s*<b>([^<]+)</b>', r'<meta\s+property="og:title"\s+content="([^"]+)"',
                r"<title>\s*(?:MixDrop\s*-\s*Watch\s+)?([^<]+?)\s*</title>"):
        m = re.search(pat, page, re.I)
        if m:
            t = _html.unescape(m.group(1)).strip()
            if t and not re.fullmatch(r"(?i)mixdrop(?:\.\w+)?|m[i1]?xdrop.*", t):
                return t
    return ""


def _size(page: str) -> int | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(KB|MB|GB)\b", re.sub(r"<[^>]+>", " ", page))
    if not m:
        return None
    return int(float(m.group(1)) * {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}[m.group(2).upper()])


class MixDropPlugin(SitePlugin):
    id = "mixdrop"
    name = "MixDrop"
    site = SITE
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "MixDrop and mirror domains: file pages and embeds (unpacks the player script)."

    def supports(self, url) -> bool:
        return parse_url(url) is not None

    def _embed(self, ctx, origin: str, kind: str, vid: str):
        """(embed_url, embed_html, file_page_html)."""
        file_html = ""
        if kind == "f":
            r = ctx.client.get(f"{origin}/f/{vid}", detect_challenges=False)
            if _offline(r.status_code, r.text):
                raise PluginError("MixDrop: file not found (offline)")
            file_html = r.text
            origin = "https://" + (urlsplit(r.url or origin).hostname or urlsplit(origin).hostname)
            m = re.search(r"<iframe\b[^>]*src=['\"]([^'\"]*/e/[^'\"]+)['\"]", file_html, re.I)
            embed = urljoin(origin + "/", _html.unescape(m.group(1))) if m else f"{origin}/e/{vid}"
            if embed.startswith("//"):
                embed = "https:" + embed
        else:
            embed = f"{origin}/e/{vid}"
        r = ctx.client.get(embed, headers={"referer": f"{origin}/f/{vid}"}, detect_challenges=False)
        if _offline(r.status_code, r.text):
            raise PluginError("MixDrop: file not found (offline)")
        return (r.url or embed), r.text, file_html

    def grab(self, url, ctx) -> GrabResult:
        parsed = parse_url(url)
        if parsed is None:
            raise PluginError("not a MixDrop link")
        origin, kind, vid = parsed
        page_kind = "f" if kind == "f" else "e"
        r = ctx.client.get(f"{origin}/{page_kind}/{vid}", detect_challenges=False)
        if _offline(r.status_code, r.text):
            raise PluginError("MixDrop: file not found (offline)")
        name = _title(r.text)
        return GrabResult(title=name or vid, filename=name, size=_size(r.text) if page_kind == "f" else None,
                          site=SITE, plugin_id=self.id, extra={"file_id": vid})

    def resolve(self, url, ctx) -> ResolveResult:
        parsed = parse_url(url)
        if parsed is None:
            raise PluginError("not a MixDrop link")
        origin, kind, vid = parsed
        embed_url, embed_html, file_html = self._embed(ctx, origin, "f" if kind == "f" else "e", vid)
        script = unpack(embed_html)
        media = _media_url(script) or _media_url(embed_html)
        if not media:
            if "eval(function(p,a,c,k,e,d)" not in embed_html:
                raise PluginError("MixDrop: player script not found (file still processing or layout changed)")
            raise PluginError("MixDrop: media URL not found in the player script")
        name = _title(file_html) or _title(embed_html)
        if name and "." not in name[-6:]:
            name += ".mp4"
        e_origin = "https://" + (urlsplit(embed_url).hostname or "")
        return ResolveResult(url=media, filename=name,
                             size=_size(file_html) if file_html else None,
                             headers={"referer": embed_url, "origin": e_origin})
