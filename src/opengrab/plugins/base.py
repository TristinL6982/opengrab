"""Plugin framework — the heart of the grabber.

JDownloader 2 has one Java plugin per site.  Here every plugin is a small
Python class:

    from opengrab.plugins.base import SitePlugin, ResolveResult, \
        CaptchaRequired, LoginRequired, PaywallRequired

    class MegaPlugin(SitePlugin):
        id = "megashare"
        name = "MegaShare"
        site = "megashare.example"

        def resolve(self, url, ctx):
            page = ctx.client.get(url)
            img = ctx.extract_captcha(page.text)          # finds the captcha img
            code = ctx.solve_captcha(img_b64, kind="text", site_url=url)
            form = {"captcha": code, "op": "download1"}
            r = ctx.client.post(url, data=form)
            direct = ctx.find_direct_link(r.text)
            return ResolveResult(url=direct, filename="movie.mp4", size=None)

Drop a file into the user plugins dir (%APPDATA%/OpenGrab/plugins/) and it
is hot-loaded on the next scan — no rebuild needed.

Plugins raise:
  CaptchaRequired(image_b64=..., kind=...)  -> engine parks link, asks solver/UI
  LoginRequired(hint=...)                   -> engine parks link, asks for creds
  PaywallRequired(note=...)                 -> engine marks paywall (configurable)

`ctx.solve_captcha` runs the full SolverChain (local OCR -> manual -> paid
providers), so a plugin author never touches provider code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse, urljoin


# ---------------------------------------------------------------------------
# Exceptions raised by plugins
# ---------------------------------------------------------------------------
class PluginError(Exception):
    pass


class CaptchaRequired(PluginError):
    def __init__(self, image_b64: str = "", kind: str = "text",
                 sitekey: str = "", site_url: str = "", extra: dict | None = None):
        super().__init__("captcha required")
        self.image_b64 = image_b64
        self.kind = kind
        self.sitekey = sitekey
        self.site_url = site_url
        self.extra = extra or {}


class LoginRequired(PluginError):
    def __init__(self, hint: str = ""):
        super().__init__("login required")
        self.hint = hint


class PaywallRequired(PluginError):
    def __init__(self, note: str = ""):
        super().__init__("paywall")
        self.note = note


# ---------------------------------------------------------------------------
# Result objects
# ---------------------------------------------------------------------------
@dataclass
class GrabResult:
    """Metadata about a link after the grab phase (no download yet)."""
    title: str = ""
    filename: str = ""
    size: Optional[int] = None
    site: str = ""
    plugin_id: str = ""
    direct_url: Optional[str] = None       # set => downloadable immediately
    direct_headers: dict = field(default_factory=dict)
    children: list = field(default_factory=list)  # nested links [url, title]
    needs_captcha: bool = False
    needs_login: bool = False
    paywalled: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class ResolveResult:
    """The real, direct download URL (after captcha/login/premium steps)."""
    url: str
    filename: str = ""
    size: Optional[int] = None
    headers: dict = field(default_factory=dict)   # extra headers for the download
    children: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Context handed to every plugin method
# ---------------------------------------------------------------------------
class PluginContext:
    """Everything a plugin can use.  Provided by the engine at runtime."""

    def __init__(self, client, settings, accounts=None, captcha_chain=None,
                 logger=None):
        self.client = client
        self.settings = settings
        self.accounts = accounts or None
        self.captcha_chain = captcha_chain or None
        self.log = logger

    # -- captcha helper (runs the whole solver chain) ------------------------
    def solve_captcha(self, image_b64: str = "", kind: str = "text",
                      site_url: str = "", sitekey: str = "",
                      image_url: str = "") -> str:
        """Solve via the configured chain; raises CaptchaRequired when the
        result must come from the human (manual) — the engine catches it and
        parks the link, then re-invokes the plugin with ctx.manual_code set.
        """
        from ..captcha.base import CaptchaRequest
        if self.manual_code:            # re-run after human solved it
            return self.manual_code
        if self.captcha_chain is None:
            raise CaptchaRequired(image_b64=image_b64, kind=kind,
                                  site_url=site_url, sitekey=sitekey)
        req = CaptchaRequest(image_b64=image_b64, image_url=image_url,
                             kind=kind, site_url=site_url,
                             recaptcha_sitekey=sitekey)
        res = self.captcha_chain.solve(req)
        if res.text == "__MANUAL__":
            raise CaptchaRequired(image_b64=image_b64, kind=kind,
                                  site_url=site_url, sitekey=sitekey,
                                  extra={"image_url": image_url} if image_url else None)
        if not res.ok:
            raise CaptchaRequired(image_b64=image_b64, kind=kind,
                                  site_url=site_url, sitekey=sitekey,
                                  extra={"solver_error": res.error, "image_url": image_url})
        return res.text

    # manual_code is set by the engine on the retry after the UI submitted
    # a manual solution; plugin authors don't need to know.
    manual_code: str = ""

    # -- account helpers ------------------------------------------------------
    def account_for(self, site: str | None = None, url: str | None = None):
        if self.accounts is None:
            return None
        if site and self.accounts.find is not None:
            return self.accounts.find(site)
        if url:
            host = urlparse(url).netloc
            if self.accounts.for_domain is not None:
                return self.accounts.for_domain(host)
        return None

    # -- page helpers ---------------------------------------------------------
    def extract_captcha(self, html: str, url: str = "",
                        img_sel: str | None = None) -> tuple[str, str]:
        """Find a captcha <img> in the page.  Returns (b64_or_url, src)."""
        m = re.search(
            r'<img[^>]+(?:id|name|class)="[^"]*(?:captcha|captcha-image|recaptcha)[^"]*"[^>]*src="([^"]+)"',
            html, re.I)
        if not m:
            m = re.search(r'src="([^"]*(?:captcha|recaptcha|hcaptcha)[^"]*\.(?:png|jpg|jpeg|gif)[^"]*)"',
                          html, re.I)
        if m:
            return ("", urljoin(url, m.group(1)))
        return ("", "")

    def find_direct_link(self, html: str, base_url: str = "",
                         prefer_exts: tuple = (".mp4", ".mkv", ".avi", ".zip",
                                                ".rar", ".7z", ".pdf", ".iso",
                                                ".nfo", ".txt", ".mp3", ".exe",
                                                ".apk", ".doc", ".xls", ".ppt")) -> str:
        """Pick the most likely direct-download <a> from a page."""
        cands = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>', html, re.I)
        best, best_score = "", -1
        for href in cands:
            href = href.strip()
            if href.startswith(("#", "javascript:", "mailto:")):
                continue
            low = href.lower()
            score = 0
            if any(low.endswith(e) for e in prefer_exts):
                score += 100
            if any(k in low for k in ("download", "direct", "file", "get")):
                score += 50
            if "track" in low or "continue" in low:
                score += 20
            if score > best_score:
                best_score = score
                best = href
        if best and best_score >= 50:
            return urljoin(base_url, best)
        return ""

    def find_magnets(self, html: str) -> list[str]:
        return re.findall(r'magnet:\?xt=[^"\'<>\s]+', html)


# ---------------------------------------------------------------------------
# Base plugin classes
# ---------------------------------------------------------------------------
class SitePlugin:
    """Base for site-specific plugins.  Subclass and set the class attrs."""
    id: str = ""
    name: str = ""
    site: str = ""                   # domain (or dotted suffix) to claim
    origin: str = "user"
    supports_captcha: bool = True
    supports_login: bool = False
    universal: bool = False          # True = catch-all fallback (lowest priority)
    description: str = ""

    def supports(self, url: str) -> bool:
        if not self.site:
            return False
        host = urlparse(url).netloc.lower()
        s = self.site.lower()
        return host == s or host.endswith("." + s)

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        """Cheap metadata fetch.  Default: try to resolve directly."""
        raise NotImplementedError

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        """Produce the final direct download URL (may solve captchas etc.)"""
        raise NotImplementedError

    # Optional: finalize(self, path, link, ctx) -> path
    # Called after the transfer finishes, before checksums and extraction.
    # Hosts that serve files encrypted (Mega, transfer.it) decrypt here,
    # using values the plugin stored in link.extra during resolve. Raise
    # PluginError to fail the download with a message.


class BridgePlugin(SitePlugin):
    """Marker for plugins that delegate to an external extractor
    (e.g. the yt-dlp bridge).  They claim many hosts."""
    pass
