"""Plugin registry: discovers builtins + user plugins, supports hot-reload."""
from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
import sys
import time
from pathlib import Path

from .base import SitePlugin

log = logging.getLogger("opengrab.plugins")


class PluginRegistry:
    def __init__(self):
        self.plugins: dict[str, SitePlugin] = {}
        self.errors: dict[str, str] = {}
        self._user_dir: Path | None = None
        self._scan_time = 0.0

    # -- discovery -----------------------------------------------------------
    def set_user_dir(self, d: Path) -> None:
        self._user_dir = d

    def scan(self, force: bool = False, min_interval: float = 2.0) -> int:
        """Rescan.  Returns number of plugins.  Cheap if nothing changed."""
        now = time.time()
        if not force and now - self._scan_time < min_interval:
            return len(self.plugins)
        self._scan_time = now
        self._scan_builtin_package()
        if self._user_dir:
            self._scan_user_dir()
        log.info("plugin scan complete: %d plugins", len(self.plugins))
        return len(self.plugins)

    def _scan_builtin_package(self) -> None:
        from . import builtin
        for m in pkgutil.iter_modules(builtin.__path__):
            if m.name.startswith("_"):
                continue
            try:
                mod = importlib.import_module(f".builtin.{m.name}", "opengrab.plugins")
                self._collect(mod, origin="builtin")
            except Exception as e:
                self.errors[f"builtin:{m.name}"] = str(e)
                log.exception("failed to load builtin plugin %s", m.name)

    def _scan_user_dir(self) -> None:
        if not self._user_dir or not self._user_dir.exists():
            return
        for f in sorted(self._user_dir.glob("*.py")):
            if f.name.startswith("_"):
                continue
            name = f.stem
            mod_name = f"opengrab_user_plugin_{name}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, f)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                spec.loader.exec_module(mod)
                self._collect(mod, origin="user")
            except Exception as e:
                self.errors[f"user:{name}"] = str(e)
                log.exception("failed to load user plugin %s", f.name)

    def _collect(self, mod, origin: str) -> None:
        for attr in vars(mod).values():
            if (isinstance(attr, type) and issubclass(attr, SitePlugin)
                    and attr is not SitePlugin and attr.id):
                inst = attr()
                inst.origin = origin
                self.plugins[attr.id] = inst

    # -- lookup ----------------------------------------------------------------
    def for_url(self, url: str) -> list[SitePlugin]:
        """All plugins claiming this URL, most specific first."""
        host = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(url).netloc.lower()
        out = []
        for p in self.plugins.values():
            try:
                if p.supports(url):
                    out.append(p)
            except Exception:
                continue
        def specificity(p: SitePlugin) -> tuple:
            host = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(url).netloc.lower()
            s = (p.site or "").lower()
            if s and (host == s or host.endswith("." + s)):
                tier = 0                       # dedicated site plugin
            elif getattr(p, "universal", False):
                tier = 3                       # catch-all fallback
            elif p.origin == "bridge" or type(p).__name__.endswith("BridgePlugin"):
                tier = 2                       # bridge (yt-dlp etc.)
            else:
                tier = 1                       # generic non-bridge (direct, user)
            return (tier, -len(s))
        out.sort(key=specificity)
        return out

    def get(self, plugin_id: str) -> SitePlugin | None:
        return self.plugins.get(plugin_id)

    def snapshot(self) -> list[dict]:
        out = []
        for p in self.plugins.values():
            out.append({
                "id": p.id, "name": p.name, "site": p.site,
                "origin": p.origin, "supports_captcha": p.supports_captcha,
                "supports_login": p.supports_login, "description": p.description,
            })
        return out

    def errors_snapshot(self) -> list[dict]:
        return [{"where": k, "error": v} for k, v in self.errors.items()]
