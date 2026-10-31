"""Direct-download servers: proxy and storage hosts that serve the file
straight from the URL (tunnel5.dlproxy.uk, stor11.mocha.my, ...).

Their links often have no file extension, so the "direct" plugin (which
claims URLs by extension) skips them and the generic page grabber would GET
the whole file into memory to sniff it. This plugin treats ANY URL on a
listed host as a file: one HEAD request for the name and size, never a body
download, and the download carries a Referer (some of these servers refuse a
request without one).

To support another host, add a shell-style pattern to DIRECT_HOSTS
("*.example.net" = every sub-domain; "stor*.example.net" = numbered nodes).
"""
from __future__ import annotations

import fnmatch
import mimetypes
from urllib.parse import unquote, urlparse

from ..base import (CaptchaRequired, GrabResult, PluginContext, PluginError,
                    ResolveResult, SitePlugin)
from ._hostflow import leaves_public_net
from .direct import _filename_from_cd

DIRECT_HOSTS: tuple[str, ...] = (
    "*.dlproxy.uk",          # tunnel1.dlproxy.uk, tunnel5.dlproxy.uk, ...
    "stor*.mocha.my",        # stor1.mocha.my, stor11.mocha.my, ...
)

GONE_STATUSES = (404, 410)


def is_direct_host(url: str, patterns: tuple[str, ...] = DIRECT_HOSTS) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and any(fnmatch.fnmatchcase(host, p.lower()) for p in patterns)


def _origin(url: str) -> str:
    p = urlparse(url)
    host = p.hostname or ""
    port = f":{p.port}" if p.port else ""
    return f"{p.scheme}://{host}{port}/"   # never the user:password part


class DirectHostPlugin(SitePlugin):
    id = "directhost"
    name = "Direct-download servers"
    site = ""
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = ("Proxy/storage servers that serve files straight from the URL "
                   "(dlproxy.uk, mocha.my storage nodes), with or without extension.")
    hosts = DIRECT_HOSTS

    def supports(self, url: str) -> bool:
        return urlparse(url).scheme in ("http", "https") and is_direct_host(url, self.hosts)

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        host = urlparse(url).hostname or ""
        referer = _origin(url)
        headers = {"referer": referer}
        try:
            r = ctx.client.request("HEAD", url, headers=headers,
                                   timeout=getattr(ctx.settings, "timeout_s", None))
        except CaptchaRequired:
            raise  # a challenge page: the engine parks the link (no bypass)
        except Exception as e:
            raise PluginError(f"{host} did not answer ({type(e).__name__}); try again later")
        status = r.status_code
        if status in GONE_STATUSES:
            raise PluginError(f"file not found on {host} (HTTP {status})")
        ct = (r.headers.get("content-type") or "").lower()
        cd = r.headers.get("content-disposition") or ""
        if status < 400 and ct.startswith("text/html") and "attachment" not in cd.lower():
            # an error/landing page instead of the file (expired link, ...)
            raise PluginError(f"{host} answered with a web page, not a file")
        # 403/405/5xx on HEAD: many file servers only allow GET; the
        # downloader probes with a ranged GET, so still hand the URL over.
        final = str(r.url or url) if status < 400 else url
        if leaves_public_net(url, final):
            raise PluginError(f"{host} redirected the download to a local network "
                              "address; OpenGrab did not follow it")
        fname = _filename_from_cd(cd) or unquote(urlparse(final).path.rsplit("/", 1)[-1])
        if fname and "." not in fname and ct and not ct.startswith("application/octet-stream"):
            ext = mimetypes.guess_extension(ct.split(";", 1)[0].strip()) or ""
            fname += ext
        cl = r.headers.get("content-length") or ""
        size = int(cl) if status < 400 and cl.isdigit() and int(cl) > 0 else None
        title = fname.rsplit(".", 1)[0] if "." in fname else fname
        return GrabResult(title=title or url, filename=fname, size=size, site=host,
                          plugin_id=self.id, direct_url=final,
                          direct_headers=headers,
                          extra={"http_probe_status": status})

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        g = self.grab(url, ctx)
        return ResolveResult(url=g.direct_url or url, filename=g.filename,
                             size=g.size, headers=g.direct_headers)
