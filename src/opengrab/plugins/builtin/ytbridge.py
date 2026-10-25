"""yt-dlp bridge plugin.

yt-dlp ships extractors for 1000+ sites (YouTube, Vimeo, Twitch,
MegaDl, various file hosts, ...).  This plugin exposes that breadth as a
single OpenGrab plugin, so we get 'all the grabbers' without porting each
Java plugin.  If a dedicated OpenGrab plugin claims the URL, it wins
(registry sorts non-bridge plugins first).

Requires:  pip install -U yt-dlp   (bundled in requirements.txt)
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from ..base import (BridgePlugin, GrabResult, ResolveResult, PluginError,
                    PluginContext)

log = logging.getLogger("opengrab.ytbridge")

_ydl = None
_supported_cache: list[str] | None = None


def _get_ydl(opts: dict | None = None):
    global _ydl
    try:
        import yt_dlp
    except Exception as e:
        raise PluginError(f"yt-dlp not installed: {e}")
    base = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 30,
    }
    if opts:
        base.update(opts)
    return yt_dlp.YoutubeDL(base)


def _supported_domains() -> list[str]:
    """Domain list yt-dlp can handle (cached after first build)."""
    global _supported_cache
    if _supported_cache is not None:
        return _supported_cache
    try:
        import yt_dlp
        ies = yt_dlp.gen_extractors()
        doms: set[str] = set()
        for ie in ies:
            try:
                for _ie_class, _ie in (ie._match_valid_url.__code__ and []):
                    pass
            except Exception:
                pass
            # each extractor has a `_VALID_URL`; parse domain(s) from it
            v = getattr(ie, "_VALID_URL", "")
            for m in __import__("re").findall(r"\*\.(?:www\.)?([a-z0-9\-]+(?:\.[a-z]{2,})+)", v, re.I):
                doms.add(m.lower())
        _supported_cache = sorted(doms)
    except Exception:
        _supported_cache = []
    return _supported_cache


class YtDlpBridgePlugin(BridgePlugin):
    id = "ytbridge"
    name = "yt-dlp bridge (1000+ sites)"
    site = ""
    origin = "bridge"
    supports_captcha = False
    supports_login = False
    description = "Delegates to yt-dlp extractors for video & file sites."

    def supports(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        # fast common domains first, then the full (cached) list
        common = ("youtube.com", "youtu.be", "vimeo.com", "twitch.tv",
                  "dailymotion.com", "bilibili.com", "tiktok.com",
                  "instagram.com", "facebook.com", "twitter.com", "x.com",
                  "soundcloud.com", "bandcamp.com", "niconico",
                  "rumble.com", "ok.ru", "vk.com")
        if any(host == d or host.endswith("." + d) for d in common):
            return True
        doms = _supported_domains()
        return any(host == d or host.endswith("." + d) for d in doms)

    def _extract(self, url: str, ctx: PluginContext, pick: str = "best") -> dict:
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
                "skip_download": True, "socket_timeout": ctx.settings.timeout_s}
        if ctx.settings.proxy:
            opts["proxy"] = ctx.settings.proxy
        try:
            ydl = _get_ydl(opts)
            info = ydl.extract_info(url, download=False)
        except Exception as e:
            raise PluginError(f"yt-dlp failed: {e}")
        if not info:
            raise PluginError("yt-dlp returned nothing")
        if info.get("_type") == "playlist":
            entries = info.get("entries") or []
            keep = []
            for en in entries[:100]:
                if not en:
                    continue
                try:
                    keep.append(self._pick_url(en))
                except Exception:
                    pass
            info["_children"] = keep
        return info

    @staticmethod
    def _pick_url(info: dict) -> dict:
        formats = info.get("formats") or []
        url = info.get("url")
        if url:
            return {"url": url, "ext": info.get("ext", ""),
                    "size": info.get("filesize") or info.get("filesize_approx")}
        # prefer progressive (single file), then best video+audio fallback
        prog = [f for f in formats if f.get("vcodec") != "none"
                and f.get("acodec") != "none" and f.get("url")]
        if prog:
            best = max(prog, key=lambda f: (f.get("height") or 0))
            return {"url": best["url"], "ext": best.get("ext", "mp4"),
                    "size": best.get("filesize") or best.get("filesize_approx")}
        v = [f for f in formats if f.get("vcodec") != "none" and f.get("url")]
        a = [f for f in formats if f.get("acodec") != "none" and f.get("url")]
        if v and a:
            return {"url": max(v, key=lambda f: (f.get("height") or 0))["url"],
                    "ext": "mp4", "size": None, "_note": "video+audio merged format not picked"}
        if v:
            best = max(v, key=lambda f: (f.get("height") or 0))
            return {"url": best["url"], "ext": best.get("ext", "mp4"),
                    "size": best.get("filesize")}
        if a:
            best = max(a, key=lambda f: f.get("tbr") or 0)
            return {"url": best["url"], "ext": best.get("ext", "m4a"),
                    "size": best.get("filesize")}
        raise PluginError("no downloadable format")

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        host = urlparse(url).netloc
        info = self._extract(url, ctx)
        title = info.get("title") or url
        if info.get("_children"):
            return GrabResult(title=title, site=host, plugin_id=self.id,
                              children=[(u, "video") for u in info["_children"]])
        pick = self._pick_url(info)
        ext = pick.get("ext") or "mp4"
        fname = f"{title[:80]}.{ext}"
        return GrabResult(
            title=title,
            filename=fname,
            size=pick.get("size"),
            site=host,
            plugin_id=self.id,
            direct_url=pick.get("url"),
            direct_headers={"referer": url, "user-agent": ctx.client._ua()},
            extra={"duration": info.get("duration"), "uploader": info.get("uploader")},
        )

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        g = self.grab(url, ctx)
        if not g.direct_url:
            raise PluginError("yt-dlp bridge found no direct URL")
        return ResolveResult(url=g.direct_url, filename=g.filename,
                             size=g.size, headers=g.direct_headers)
