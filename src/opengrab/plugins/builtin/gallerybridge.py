"""gallery-dl bridge (M9h): list the files of an image gallery page.

gallery-dl is an optional external tool. The bridge runs ``gallery-dl -g <url>``,
which only prints file URLs and never downloads, and turns them into links.
"""
from __future__ import annotations

import shutil
import subprocess
from urllib.parse import unquote, urlparse

from ..base import BridgePlugin, GrabResult, PluginError, ResolveResult

GALLERY_HOSTS = ("pixiv.net", "deviantart.com", "imgur.com", "flickr.com",
                 "artstation.com", "danbooru.donmai.us", "gelbooru.com",
                 "pinterest.com", "tumblr.com", "imgbox.com", "kemono.su")


def host_matches(url: str, hosts=GALLERY_HOSTS) -> bool:
    """True when the URL's host is one of ``hosts`` or a subdomain of one."""
    host = urlparse(url).netloc.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in hosts)


def parse_urls(output: str) -> list[str]:
    """File URLs from ``gallery-dl -g`` output, in order, without duplicates.

    Lines starting with "| " are gallery-dl fallback URLs and are skipped.
    """
    urls: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith(("http://", "https://")):
            continue
        if line not in urls:
            urls.append(line)
    return urls


def filename_for(url: str, index: int) -> str:
    """Last path segment of the URL (unquoted), or file_<index> when empty."""
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    return name or "file_%d" % index


def run_gallery_dl(url: str, timeout: float = 60, proxy: str = "") -> str:
    """Run ``gallery-dl -g`` and return its stdout."""
    exe = shutil.which("gallery-dl")
    if not exe:
        raise PluginError("gallery-dl not installed")
    cmd = [exe, "-g"]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PluginError("gallery-dl failed: %s" % exc) from exc
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        err = (proc.stderr or "").strip()[:300] or "exit %d" % proc.returncode
        raise PluginError("gallery-dl failed: %s" % err)
    return proc.stdout or ""


class GalleryDlBridgePlugin(BridgePlugin):
    id = "gallerydl"
    name = "gallery-dl bridge (image galleries)"
    site = ""
    origin = "bridge"
    supports_captcha = False
    supports_login = False
    description = "Lists gallery images with gallery-dl (optional tool)."

    def supports(self, url: str) -> bool:
        return host_matches(url)

    def grab(self, url: str, ctx) -> GrabResult:
        settings = ctx.settings
        out = run_gallery_dl(url, timeout=float(getattr(settings, "timeout_s", 60) or 60),
                             proxy=str(getattr(settings, "proxy", "") or ""))
        urls = parse_urls(out)
        if not urls:
            raise PluginError("gallery-dl found no files")
        host = urlparse(url).netloc
        if len(urls) == 1:
            name = filename_for(urls[0], 1)
            return GrabResult(title=name, filename=name, site=host, plugin_id=self.id,
                              direct_url=urls[0], direct_headers={"referer": url})
        children = [(u, filename_for(u, i)) for i, u in enumerate(urls, 1)]
        return GrabResult(title=url, site=host, plugin_id=self.id, children=children,
                          extra={"gallery_count": len(children)})

    def resolve(self, url: str, ctx) -> ResolveResult:
        g = self.grab(url, ctx)
        if not g.direct_url:
            raise PluginError("gallery-dl bridge found more than one file")
        return ResolveResult(url=g.direct_url, filename=g.filename, headers=g.direct_headers)