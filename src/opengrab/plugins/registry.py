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
from .builtin import direct

log = logging.getLogger("opengrab.plugins")


class PluginRegistry:
    def __init__(self):
        self.plugins: dict[str, SitePlugin] = {}
        self.errors: dict[str, str] = {}
        self._user_dir: Path | None = None
        self._scan_time = 0.0
        self.health = {}

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
            # Remove stale template entries before scanning
            for plugin_id in list(self.plugins.keys()):
                if self.plugins[plugin_id].origin == "template":
                    del self.plugins[plugin_id]
            
            for key in list(self.errors.keys()):
                if key.startswith("template:"):
                    del self.errors[key]
                    
            self._scan_user_dir()
            self._scan_templates()
        log.info("plugin scan complete: %d plugins", len(self.plugins))
        return len(self.plugins)

    def _scan_builtin_package(self) -> None:
        from . import builtin
        # Scan builtin package, with specific fallback for direct plugin
        try:
            mod = importlib.import_module(f".builtin.direct", "opengrab.plugins")
            self._collect(mod, origin="builtin")
        except Exception:
            pass  # Skip if no direct plugin

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

    def _scan_templates(self) -> None:
        if not self._user_dir or not self._user_dir.exists():
            return
        from .declarative import load_templates_dir
        plugins, errors = load_templates_dir(str(self._user_dir))
        for filename, message in errors.items():
            self.errors[f"template:{filename}"] = message

        # Check for conflicts with existing plugins
        for p in plugins:
            if p.id in self.plugins:
                existing = self.plugins[p.id]
                if existing.origin != "template":
                    self.errors[f"template:{p.id}"] = f"id conflicts with {existing.origin} plugin"
                    continue
            self.plugins[p.id] = p

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
                "health": self.health_for(p.id)
            })
        return out

    def record_result(self, plugin_id: str, ok: bool, error: str | None = None) -> None:
        """Record a plugin execution result.
        
        Args:
            plugin_id: The ID of the plugin to record
            ok: True if the execution succeeded, False otherwise
            error: Optional error message if execution failed
        """
        if plugin_id not in self.health:
            self.health[plugin_id] = {"ok": 0, "fail": 0, "last_error": None, "last_used": None}
        
        plugin_health = self.health[plugin_id]
        if ok:
            plugin_health["ok"] += 1
        else:
            plugin_health["fail"] += 1
            plugin_health["last_error"] = (error or "unknown error")[:500]
        plugin_health["last_used"] = time.time()

    def health_for(self, plugin_id: str) -> dict:
        """Get the health counters for a plugin.
        
        Returns a copy of the health data so modifications don't affect internal state.
        """
        if plugin_id in self.health:
            # Return a copy to prevent external mutation
            return self.health[plugin_id].copy()
        else:
            # Return default empty stats
            return {"ok": 0, "fail": 0, "last_error": None, "last_used": None}
