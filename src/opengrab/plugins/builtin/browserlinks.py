"""Signed one-time links on file hosts' storage servers.

Users often copy the final link out of the browser's download list instead
of the file page, e.g.::

    https://s105.turbobit.net/download.php?name=...&id=...&key=...
    https://a-12.1fichier.com/c123456789
    https://download2276.mediafire.com/<token>/<quickkey>/<name>
    https://fs12.bowfile.com/token/download/<token>/<name>
    https://<server>.clicknupload.net:8080/d/<token>/<name>

Those URLs are the file, signed for a short time (and often for one IP).
This plugin claims them so neither the site plugin (which expects a page
link) nor the generic page grabber (which would GET the whole file to sniff
it) gets them. It sends ONE ``HEAD`` request for the name and size, never a
body download, and hands the URL to the downloader. A link that expired
answers 404/410, a web page, or a redirect back to the site; the message is
then "this download link has expired; add the file's page link instead"
(with the page link when it can be derived, e.g. MediaFire's quickkey).

Add a host: one ``Rule`` (host pattern, path regex, site home page).
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from ..base import GrabResult, PluginContext, PluginError, ResolveResult, SitePlugin
from ._hostflow import EXPIRED, host_of, name_from_url, probe_direct


@dataclass(frozen=True)
class Rule:
    label: str
    host: str              # fnmatch pattern on the host name (no port)
    path: str              # regex the path must match
    site: str              # the site's own domain: a redirect there = expired


RULES: tuple[Rule, ...] = (
    Rule("Turbobit", "s*.turbobit.net", r"^/(?:download\.php|download/|d/)", "turbobit.net"),
    Rule("Hitfile", "s*.hitfile.net", r"^/(?:download\.php|download/|d/)", "hitfile.net"),
    Rule("1fichier", "a-*.1fichier.com", r"^/[cp][A-Za-z0-9]+", "1fichier.com"),
    Rule("MediaFire", "download*.mediafire.com", r"^/[^/]+/[A-Za-z0-9]+/", "mediafire.com"),
    Rule("Bowfile", "fs*.bowfile.com", r"^/token/download/", "bowfile.com"),
    Rule("Clicknupload", "*.clicknupload.*", r"^/d/[^/]+/", "clicknupload"),
)
_WEB_LABELS = ("", "www", "app")


def rule_for(url: str) -> Rule | None:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None
    host = (p.hostname or "").lower()
    for rule in RULES:
        if fnmatch.fnmatchcase(host, rule.host) and re.match(rule.path, p.path or "/"):
            first = host.split(".", 1)[0]
            if first in _WEB_LABELS:
                continue
            return rule
    return None


def _is_site_page(rule: Rule):
    """A redirect to the site's own website (not another storage node)."""
    def check(url: str) -> bool:
        host = host_of(url)
        if rule.site == "clicknupload":
            return re.fullmatch(r"(?:www\.)?clicknupload\.[a-z]+", host) is not None
        return host in (rule.site, "www." + rule.site)
    return check


def page_hint(rule: Rule, url: str, redirect: str = "") -> str:
    """The file page a user should add instead, when the link tells it."""
    p = urlsplit(url)
    if rule.label == "MediaFire":
        parts = [x for x in p.path.split("/") if x]
        qkey = parts[1] if len(parts) > 2 else ""
        if redirect:
            qkey = (parse_qs(urlsplit(redirect).query).get("qkey") or [qkey])[0]
        if qkey:
            return f"https://www.mediafire.com/file/{qkey}"
    return ""


def _query_name(url: str) -> str:
    """Turbobit-style ``download.php?name=<file>`` links carry the name."""
    q = parse_qs(urlsplit(url).query)
    return (q.get("name") or q.get("filename") or [""])[0]


class SignedServerLinkPlugin(SitePlugin):
    id = "signedlinks"
    name = "Signed server links"
    site = ""
    origin = "builtin"
    dedicated = True
    supports_captcha = False
    supports_login = False
    description = ("Download links copied from the browser that point straight at a host's "
                   "storage server (Turbobit, 1fichier, MediaFire, Bowfile, Clicknupload).")

    def supports(self, url: str) -> bool:
        return rule_for(url) is not None

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        rule = rule_for(url)
        if rule is None:
            raise PluginError("not a signed server link")
        name = _query_name(url) or name_from_url(url)
        if "." not in name or name.lower().endswith((".php", ".html")):
            name = ""
        return GrabResult(title=name or url, filename=name, site=host_of(url), plugin_id=self.id)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        rule = rule_for(url)
        if rule is None:
            raise PluginError("not a signed server link")
        seen: list[str] = []

        def site_page(u: str) -> bool:
            seen.append(u)
            return _is_site_page(rule)(u)

        referer = f"https://{rule.site}/" if "." in rule.site else ""
        try:
            res = probe_direct(url, ctx, {"referer": referer} if referer else {},
                               is_site_page=site_page)
        except PluginError as e:
            if str(e).startswith(EXPIRED):
                hint = page_hint(rule, url, seen[-1] if seen else "")
                raise PluginError(f"{rule.label}: {EXPIRED}" + (f" ({hint})" if hint else ""))
            raise
        if not res.filename or "." not in res.filename or res.filename.lower().endswith((".php", ".html")):
            res.filename = _query_name(url) or ""
        return res
