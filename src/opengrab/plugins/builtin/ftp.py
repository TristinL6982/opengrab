"""FTP / FTPS links (ftp://, ftps://).

* a file link becomes a direct download; engine/ftp.py does the transfer
  (SIZE for the size, REST to resume)
* a folder link (ending in /) lists its files as children; sub-folders are
  kept in extra["subdirs"] so the user can open them one level at a time
* login: user:password@ in the link, anonymous otherwise
"""
from __future__ import annotations

from ...engine import ftp as ftp_engine
from ..base import GrabResult, PluginContext, PluginError, ResolveResult, SitePlugin

MAX_CHILDREN = 1000


class FtpPlugin(SitePlugin):
    id = "ftp"
    name = "FTP / FTPS"
    site = ""
    origin = "builtin"
    supports_captcha = False
    supports_login = True
    description = ("Downloads ftp:// and ftps:// links with resume; folder links "
                   "list their files. Login from user:password@ in the link.")

    def supports(self, url: str) -> bool:
        return ftp_engine.is_ftp_url(url)

    def _timeout(self, ctx: PluginContext) -> float:
        return float(getattr(ctx.settings, "grab_timeout_s", 0)
                     or getattr(ctx.settings, "timeout_s", 0) or 30.0)

    def _probe(self, url: str, ctx: PluginContext) -> dict:
        try:
            return ftp_engine.probe(url, self._timeout(ctx))
        except ValueError as e:
            raise PluginError(str(e))
        except Exception as e:
            raise PluginError(ftp_engine.describe_error(e))

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        try:
            target = ftp_engine.parse_ftp_url(url)
        except ValueError as e:
            raise PluginError(str(e))
        info = self._probe(url, ctx)
        if "entries" in info:
            files = [(u, n) for u, n, is_dir, _ in info["entries"] if not is_dir]
            subdirs = [u for u, _, is_dir, _ in info["entries"] if is_dir]
            # a folder holding only folders lists those, so it can be browsed
            kids = files or [(u, n + "/") for u, n, is_dir, _ in info["entries"] if is_dir]
            return GrabResult(title=target.name, site=target.host, plugin_id=self.id,
                              children=kids[:MAX_CHILDREN],
                              extra={"dir_index": True, "subdirs": subdirs[:MAX_CHILDREN]})
        name = info.get("name") or target.name
        return GrabResult(title=name.rsplit(".", 1)[0] if "." in name else name,
                          filename=name, size=info.get("size"), site=target.host,
                          plugin_id=self.id, direct_url=url)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        g = self.grab(url, ctx)
        if g.direct_url:
            return ResolveResult(url=g.direct_url, filename=g.filename, size=g.size)
        if g.children:
            return ResolveResult(url="", children=list(g.children), extra=g.extra)
        raise PluginError("the ftp folder is empty")
