"""
Google Drive shared file links: rewrites share URLs to direct downloads.
"""

import re
from urllib.parse import urlsplit, parse_qs

from ..base import SitePlugin, GrabResult, ResolveResult, PluginError, PluginContext


PATH_RE = re.compile(r"^/file/d/([A-Za-z0-9_-]{10,})")
HOSTS = ("drive.google.com", "docs.google.com")


def file_id(url: str) -> str | None:
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or p.netloc.lower() not in HOSTS:
        return None
    m = PATH_RE.match(p.path)
    if m:
        return m.group(1)
    if p.path in ("/open", "/uc"):
        ids = parse_qs(p.query).get("id")
        if ids and re.fullmatch(r"[A-Za-z0-9_-]{10,}", ids[0]):
            return ids[0]
    return None


def direct_url(fid: str) -> str:
    return f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"


class GoogleDrivePlugin(SitePlugin):
    id = "gdrive"
    name = "Google Drive"
    site = "drive.google.com"
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "Google Drive shared file links (/file/d/<id>, open?id=, uc?id=)."

    def supports(self, url) -> bool:
        return file_id(url) is not None

    def grab(self, url, ctx) -> GrabResult:
        fid = file_id(url)
        if fid is None:
            raise PluginError("not a Google Drive file link")
        return GrabResult(
            title=fid,
            site="drive.google.com",
            plugin_id=self.id,
            direct_url=direct_url(fid),
            direct_headers={},
        )

    def resolve(self, url, ctx) -> ResolveResult:
        g = self.grab(url, ctx)
        return ResolveResult(url=g.direct_url, headers=g.direct_headers)