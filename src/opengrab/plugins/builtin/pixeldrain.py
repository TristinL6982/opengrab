"""
pixeldrain.com file host: maps share links to the public file API.
"""

import re
from ..base import SitePlugin, GrabResult, ResolveResult, PluginError, PluginContext

FILE_RE = re.compile(r"^https?://(?:www\.)?pixeldrain\.com/(?:u|api/file)/([A-Za-z0-9]+)")

def file_id(url: str) -> str | None:
    m = FILE_RE.match(url)
    return m.group(1) if m else None

def api_url(fid: str) -> str:
    return f"https://pixeldrain.com/api/file/{fid}?download"

class PixeldrainPlugin(SitePlugin):
    id = "pixeldrain"
    name = "pixeldrain"
    site = "pixeldrain.com"
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "pixeldrain.com single-file links (/u/<id>)."

    def supports(self, url) -> bool:
        return file_id(url) is not None

    def grab(self, url, ctx) -> GrabResult:
        fid = file_id(url)
        if fid is None:
            raise PluginError("not a pixeldrain file link")
        return GrabResult(
            title=fid,
            site="pixeldrain.com",
            plugin_id=self.id,
            direct_url=api_url(fid),
            direct_headers={"referer": f"https://pixeldrain.com/u/{fid}"},
        )

    def resolve(self, url, ctx) -> ResolveResult:
        g = self.grab(url, ctx)
        return ResolveResult(url=g.direct_url, headers=g.direct_headers)