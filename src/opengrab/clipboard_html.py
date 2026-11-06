"""Links from the HTML flavour of the clipboard (CF_HTML on Windows).

Copying a list of hyperlinks from a web page puts only the link *names* in the
plain-text flavour. The HTML flavour still carries every ``<a href>``, which is
what JDownloader reads. This module is pure (no clipboard access) so it can be
tested with made-up samples.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

_HEADER_FIELD = re.compile(rb"^([A-Za-z]+):(.*?)\r?$", re.MULTILINE)


def _split_cf_html(data: bytes | str) -> tuple[str, str]:
    """Return (fragment_html, source_url) from a CF_HTML payload or plain HTML."""
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    raw = raw.split(b"\0", 1)[0]
    head = raw[:2048]
    fields = {m.group(1).lower(): m.group(2).strip() for m in _HEADER_FIELD.finditer(head)}
    source = fields.get(b"sourceurl", b"").decode("utf-8", "replace")
    if source.lower() == "about:blank":
        source = ""

    def offset(name: bytes):
        try:
            return int(fields[name])
        except (KeyError, ValueError):
            return None

    start, end = offset(b"startfragment"), offset(b"endfragment")
    if start is not None and end is not None and 0 <= start <= end <= len(raw):
        body = raw[start:end]
    else:
        # No usable offsets: drop the header lines and take the markup.
        marker = raw.lower().find(b"<html")
        body = raw[marker:] if marker >= 0 else raw
    return body.decode("utf-8", "replace"), source


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.base = ""

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "base" and d.get("href") and not self.base:
            self.base = d["href"]
        elif tag == "a" and d.get("href"):
            self.hrefs.append(d["href"])


def links_from_html(html: str, base_url: str = "") -> list[str]:
    parser = _Anchors()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    base = urljoin(base_url, parser.base) if parser.base else base_url
    out: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        href = href.strip()
        if not href or href.startswith("#"):
            continue
        low = href.lower()
        if not low.startswith("magnet:"):
            if base and not urlsplit(href).scheme:
                href = urljoin(base, href)
            if urlsplit(href).scheme.lower() not in ("http", "https"):
                continue
        if href not in seen:
            seen.add(href)
            out.append(href)
    return out


def links_from_cf_html(data: bytes | str) -> list[str]:
    """All http(s)/magnet ``<a href>`` links of a CF_HTML clipboard payload, in
    order, de-duplicated; relative links resolve against SourceURL."""
    if not data:
        return []
    html, source = _split_cf_html(data)
    return links_from_html(html, source)


_URL_START = r"(?:https?://|ftps?://|magnet:\?)"
_TEXT_URL = re.compile(_URL_START + r"(?:(?!" + _URL_START + r")[^\s<>\"'])+", re.IGNORECASE)


def urls_from_text(text: str) -> list[str]:
    """Every URL in copied text, in order, de-duplicated. URLs glued together
    without a separator (https://a/1https://b/2) are split at the next scheme."""
    out: list[str] = []
    for raw in _TEXT_URL.findall(text or ""):
        url = raw.rstrip(".,;:!?)]}")
        if url and url not in out:
            out.append(url)
    return out
