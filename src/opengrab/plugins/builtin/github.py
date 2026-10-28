"""
GitHub file links: rewrites blob and raw URLs to raw.githubusercontent.com, passes release assets through.
"""

import re
from urllib.parse import urlsplit

from ..base import SitePlugin, GrabResult, ResolveResult, PluginError, PluginContext


BLOB_RE = re.compile(r"^/([^/]+)/([^/]+)/(?:blob|raw)/(.+)$")
RELEASE_RE = re.compile(r"^/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/]+)$")


def direct_url(url: str) -> str | None:
    p = urlsplit(url)
    if p.scheme not in ("http", "https"):
        return None
    host = p.netloc.lower()
    if host == "raw.githubusercontent.com" and p.path.count("/") >= 4:
        return url
    if host != "github.com":
        return None
    m = RELEASE_RE.match(p.path)
    if m:
        return f"https://github.com{p.path}"
    m = BLOB_RE.match(p.path)
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return None


def file_name(url: str) -> str:
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]


class GitHubPlugin(SitePlugin):
    id = "github"
    name = "GitHub"
    site = "github.com"
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "GitHub blob/raw file links and release assets."

    def supports(self, url) -> bool:
        return direct_url(url) is not None

    def grab(self, url, ctx: PluginContext) -> GrabResult:
        d = direct_url(url)
        if d is None:
            raise PluginError("not a GitHub file link")
        return GrabResult(
            title=file_name(d),
            site="github.com",
            plugin_id=self.id,
            direct_url=d,
            direct_headers={},
        )

    def resolve(self, url, ctx: PluginContext) -> ResolveResult:
        g = self.grab(url, ctx)
        return ResolveResult(url=g.direct_url, headers=g.direct_headers)