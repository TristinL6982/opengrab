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
import re
from urllib.parse import urlparse

from ..base import (BridgePlugin, GrabResult, ResolveResult, PluginError,
                    PluginContext)
from ...media import ffmpeg as media_ffmpeg
from ...media import presets as media_presets

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
        # Each extractor has a `_VALID_URL` regex (or list of regexes);
        # pull any literal domains out.
        for ie in ies:
            v = getattr(ie, "_VALID_URL", "") or ""
            vals = v if isinstance(v, (list, tuple)) else [v]
            for raw in vals:
                s = str(raw).replace("\\.", ".").replace("\\", "")  # unescape
                for m in re.finditer(r"[a-z0-9\-]+(?:\.[a-z0-9\-]+)+", s, re.I):
                    d = m.group(0).lower()
                    # keep only host-like tokens (drop scheme fragments)
                    if re.fullmatch(r"[a-z0-9\-]+(\.[a-z]{2,})+", d):
                        doms.add(d)
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
        max_entries = max(1, min(
            int(getattr(ctx.settings, "media_playlist_max_entries", 10000)),
            50000))
        opts = {"quiet": True, "no_warnings": True, "noplaylist": False,
                "extract_flat": "in_playlist", "playlistend": max_entries + 1,
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
            truncated = False
            for en in entries:
                if not en:
                    continue
                child_url = self._playlist_entry_url(en, url)
                if child_url:
                    if len(keep) >= max_entries:
                        truncated = True
                        break
                    keep.append((child_url, str(en.get("title") or en.get("id") or "video")))
            info["_children"] = keep
            info["_children_truncated"] = truncated
        return info

    @staticmethod
    def _playlist_entry_url(entry: dict, playlist_url: str) -> str:
        for key in ("webpage_url", "original_url", "url"):
            value = str(entry.get(key) or "")
            if value.startswith(("http://", "https://")):
                return value
        video_id = str(entry.get("id") or "")
        extractor = str(entry.get("ie_key") or entry.get("extractor_key") or "").lower()
        host = urlparse(playlist_url).netloc.lower()
        if video_id and ("youtube" in extractor
                         or host == "youtube.com" or host.endswith(".youtube.com")
                         or host == "youtu.be"):
            return f"https://www.youtube.com/watch?v={video_id}"
        return ""

    @staticmethod
    def _pick_url(info: dict, max_height: int = 0) -> dict:
        formats = info.get("formats") or []
        url = info.get("url")
        if url:
            return {"url": url, "ext": info.get("ext", ""),
                    "size": info.get("filesize") or info.get("filesize_approx")}
        # prefer progressive (single file), then best video+audio fallback
        prog = [f for f in formats if f.get("vcodec") != "none"
                and f.get("acodec") != "none" and f.get("url")]
        if prog:
            eligible = ([f for f in prog if (f.get("height") or 0) <= max_height]
                        if max_height else prog)
            if max_height and not eligible:
                raise PluginError(f"no progressive format at or below {max_height}p")
            best = max(eligible, key=lambda f: (f.get("height") or 0,
                                                f.get("tbr") or 0))
            return {"url": best["url"], "ext": best.get("ext", "mp4"),
                    "size": best.get("filesize") or best.get("filesize_approx")}
        v = [f for f in formats if f.get("vcodec") != "none" and f.get("url")
             and (not max_height or (f.get("height") or 0) <= max_height)]
        a = [f for f in formats if f.get("acodec") != "none" and f.get("url")]
        if v and a:
            raise PluginError(
                "this stream uses separate video and audio tracks; choose a progressive format")
        if v:
            best = max(v, key=lambda f: (f.get("height") or 0))
            return {"url": best["url"], "ext": best.get("ext", "mp4"),
                    "size": best.get("filesize")}
        if a:
            best = max(a, key=lambda f: f.get("tbr") or 0)
            return {"url": best["url"], "ext": best.get("ext", "m4a"),
                    "size": best.get("filesize")}
        raise PluginError("no downloadable format")

    @staticmethod
    def _plan(info: dict, settings) -> dict | None:
        """Pick formats with the configured preset; None means use _pick_url."""
        preset = str(getattr(settings, "media_preset", "") or "")
        if not preset or info.get("url"):
            return None
        can_merge = media_ffmpeg.available(str(getattr(settings, "media_ffmpeg_path", "") or ""))
        try:
            return media_presets.select_formats(info, preset, can_merge)
        except (media_presets.NoFormat, ValueError) as e:
            raise PluginError(str(e))

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        host = urlparse(url).netloc
        info = self._extract(url, ctx)
        title = info.get("title") or url
        if info.get("_children"):
            return GrabResult(title=title, site=host, plugin_id=self.id,
                              children=info["_children"],
                              extra={"playlist_truncated": info.get("_children_truncated", False),
                                     "playlist_count": len(info["_children"])})
        pick = self._pick_url(
            info, max_height=max(0, min(
                4320, int(getattr(ctx.settings, "media_max_height", 0)))))
        plan = self._plan(info, ctx.settings)
        if plan is not None:
            main = plan["video"] or plan["audio"]
            extra = {"duration": info.get("duration"), "uploader": info.get("uploader"),
                     "media_mode": plan["mode"], "media_ext": plan["ext"]}
            if plan["mode"] == "merge":
                extra["media_parts"] = [
                    {"kind": "video", "url": plan["video"]["url"], "ext": plan["video"].get("ext", "")},
                    {"kind": "audio", "url": plan["audio"]["url"], "ext": plan["audio"].get("ext", "")}]
            return GrabResult(
                title=title,
                filename=f"{title[:80]}.{plan['ext']}",
                size=main.get("filesize") or main.get("filesize_approx"),
                site=host,
                plugin_id=self.id,
                direct_url=main["url"],
                direct_headers={"referer": url, "user-agent": ctx.client._ua()},
                extra=extra,
            )
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
