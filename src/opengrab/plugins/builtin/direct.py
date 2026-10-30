"""Direct-link plugin: handles URLs that ARE the file.

Strategy:
  1. HEAD (fallback GET range 0) the URL with the anti-bot client.
  2. If Content-Range/Content-Length arrive and the content-type is a file
     (or Content-Disposition: attachment) -> direct download.
  3. Derive the filename from Content-Disposition, else the URL path.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse, unquote

from ..base import (SitePlugin, GrabResult, ResolveResult, PluginError, CaptchaRequired,
                    PluginContext)

FILE_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".mp3", ".flac", ".wav", ".ogg",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".img",
    ".pdf", ".epub", ".txt", ".nfo", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".exe", ".msi", ".apk", ".dmg", ".deb", ".rpm",
    ".torrent", ".m4a", ".aac", ".flac", ".jpg", ".png", ".webp", ".gif",
    ".srt", ".ass", ".zipx", ".cbz", ".cbr", ".comic", ".bin", ".rom",
}

FILE_CTS = (
    "video/", "audio/", "application/zip", "application/x-rar",
    "application/x-7z", "application/octet-stream", "application/pdf",
    "application/x-tar", "application/gzip", "application/x-bzip2",
    "application/x-xz", "application/iso9660-image",
    "application/vnd.apple.diskimage", "image/",
)


def _filename_from_cd(cd: str) -> str:
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", cd, re.I)
    if m:
        return unquote(m.group(1).strip())
    return ""


class DirectLinkPlugin(SitePlugin):
    id = "direct"
    name = "Direct link"
    site = ""                      # claims any URL that sniffs as a file
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "Downloads URLs that serve the file directly (content sniffed)."

    def supports(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        return ext in FILE_EXTS

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        return self._sniff(url, ctx)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        g = self._sniff(url, ctx)
        if not g.direct_url:
            raise PluginError("URL does not look like a direct file anymore")
        return ResolveResult(url=g.direct_url, filename=g.filename,
                             size=g.size, headers=g.direct_headers)

    def _sniff(self, url: str, ctx: PluginContext) -> GrabResult:
        host = urlparse(url).netloc
        headers = {}
        try:
            r = ctx.client.request("HEAD", url, timeout=ctx.settings.timeout_s)
            if r.status_code in (405, 403, 501, 429, 503) or not r.headers:
                # a GET body is needed to recognise a challenge page (W2)
                r = ctx.client.get(url, max_bytes=200000)
        except CaptchaRequired:
            raise  # W2: a challenge page; the engine parks the link
        except Exception as e:
            raise PluginError(f"HEAD/GET failed: {e}")
        ct = r.headers.get("content-type", "").lower()
        cd = r.headers.get("content-disposition", "")
        cl = r.headers.get("content-length", "")
        size = None
        if cl.isdigit():
            size = int(cl)
        looks_file = (any(ct.startswith(p) for p in FILE_CTS)
                      or "attachment" in cd.lower()
                      or bool(self.supports(url)))
        if not looks_file or r.status_code >= 400:
            return GrabResult(title=url, site=host,
                              direct_url=None, needs_captcha=False)
        fname = _filename_from_cd(cd) or unquote(urlparse(url).path.rsplit("/", 1)[-1])
        title = fname.rsplit(".", 1)[0] if "." in fname else fname
        return GrabResult(
            title=title or url,
            filename=fname,
            size=size,
            site=host,
            plugin_id=self.id,
            direct_url=str(r.url),
            direct_headers={"referer": url} if host != urlparse(str(r.url)).netloc else {},
        )
