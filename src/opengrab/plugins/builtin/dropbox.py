"""
dropbox.com shared links: rewrites share URLs to direct downloads (dl=1).
"""

from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, unquote
from ..base import SitePlugin, GrabResult, ResolveResult, PluginError, PluginContext


HOSTS = ("dropbox.com", "www.dropbox.com")


def is_share_link(url: str) -> bool:
    p = urlsplit(url)
    return p.scheme in ("http", "https") and p.netloc.lower() in HOSTS and (p.path.startswith("/s/") or p.path.startswith("/scl/fi/")) and len([s for s in p.path.split("/") if s]) >= 3


def direct_url(url: str) -> str:
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k not in ("dl", "raw")]
    q.append(("dl", "1"))
    return urlunsplit(("https", "www.dropbox.com", p.path, urlencode(q), ""))


def file_name(url: str) -> str:
    return unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])


class DropboxPlugin(SitePlugin):
    id = "dropbox"
    name = "Dropbox"
    site = "dropbox.com"
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "Dropbox shared file links (/s/ and /scl/fi/)."

    def supports(self, url) -> bool:
        return is_share_link(url)

    def grab(self, url, ctx) -> GrabResult:
        if not is_share_link(url):
            raise PluginError("not a Dropbox shared file link")
        return GrabResult(title=file_name(url), site="dropbox.com", plugin_id=self.id, direct_url=direct_url(url), direct_headers={})

    def resolve(self, url, ctx) -> ResolveResult:
        g = self.grab(url, ctx)
        return ResolveResult(url=g.direct_url, headers=g.direct_headers)