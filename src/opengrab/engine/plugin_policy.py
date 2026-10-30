"""W12: plugin permissions, sandbox, template updates and the marketplace.

Wires the M7 plugin tooling that had tests but no caller:

* plugins.permissions + plugins.sandbox - user plugins (.py) and templates
  (.json) from the plugins folder run with a GuardedClient: every request
  and cookie access is checked against what the user granted. A template may
  reach its own site without asking; a script plugin declares
  ``PERMISSIONS = {"hosts": [...], "cookies": bool, "files": bool}`` and
  stays blocked until the user approves it. Builtin plugins are trusted.
  This guards network and cookie access only; a Python plugin can still
  import anything (recorded as an open decision in PROJECT_SPEC.md).
* plugins.updates - template updates from an index URL (sha256 + schema
  check), installed versions tracked in templates_installed.json.
* plugins.sdk - template validation for the UI and the CLI.
* plugins.marketplace - a catalog URL, search, install with sha256 and an
  Ed25519 signature from a key the user trusts.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from ..plugins import marketplace as mkt
from ..plugins import updates as upd
from ..plugins.permissions import (Permissions, PermissionError_, load_grants, parse_permissions,
                                   requested_beyond, save_grants)
from ..plugins.sandbox import sandbox_context
from ..plugins.sdk import validate_template

log = logging.getLogger("opengrab.plugin_policy")


class PluginPolicy:
    def __init__(self, manager):
        self.mgr = manager
        self.settings = manager.settings
        self.data_dir = Path(manager.data_dir)
        self.grants_path = self.data_dir / "plugin_grants.json"
        self.installed_path = self.data_dir / "templates_installed.json"
        self._lock = threading.RLock()
        try:
            self.grants: dict[str, Permissions] = load_grants(self.grants_path)
        except (OSError, ValueError):
            self.grants = {}
        self.last_update: dict = {}

    # -- permissions ------------------------------------------------------------
    def requested(self, plugin) -> Permissions:
        if plugin.origin == "template":
            site = (plugin.site or "").lower().lstrip(".")
            return Permissions(hosts=[site, "*." + site] if site else [])
        raw = getattr(plugin, "PERMISSIONS", None)
        if isinstance(raw, dict):
            try:
                return parse_permissions(raw)
            except ValueError:
                pass
        return Permissions()

    def effective(self, plugin) -> Permissions:
        """What the plugin may do now (templates: their own site, implicitly)."""
        if plugin.origin == "template":
            granted = self.grants.get(plugin.id) or Permissions()
            own = self.requested(plugin)
            return Permissions(hosts=sorted(set(own.hosts) | set(granted.hosts)),
                               files=granted.files, cookies=granted.cookies)
        return self.grants.get(plugin.id) or Permissions()

    def sandboxed(self, plugin) -> bool:
        return (getattr(self.settings, "plugin_sandbox_enabled", True)
                and getattr(plugin, "origin", "builtin") in ("user", "template"))

    def context_for(self, plugin, ctx):
        if not self.sandboxed(plugin):
            return ctx
        if plugin.origin == "user":
            missing = requested_beyond(self.requested(plugin), self.effective(plugin))
            if missing:
                raise PermissionError_(
                    f"plugin {plugin.id} needs your approval for: {', '.join(missing)} "
                    "(Settings, Plugin health)")
        return sandbox_context(ctx, self.effective(plugin))

    def grant(self, plugin_id: str, perms: Permissions) -> None:
        with self._lock:
            self.grants[plugin_id] = perms
            save_grants(self.grants_path, self.grants)

    def revoke(self, plugin_id: str) -> bool:
        with self._lock:
            if self.grants.pop(plugin_id, None) is None:
                return False
            save_grants(self.grants_path, self.grants)
            return True

    def overview(self) -> list[dict]:
        out = []
        for info in self.mgr.registry.snapshot():
            p = self.mgr.registry.get(info["id"])
            if p is None or p.origin not in ("user", "template"):
                continue
            req, eff = self.requested(p), self.effective(p)
            out.append({"id": p.id, "origin": p.origin, "requested": req.to_dict(),
                        "granted": eff.to_dict(), "missing": requested_beyond(req, eff)})
        return out

    # -- template updates -----------------------------------------------------------
    def installed_versions(self) -> dict:
        try:
            return dict(json.loads(self.installed_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}

    def _remember(self, plugin_id: str, version: str) -> None:
        data = self.installed_versions()
        data[plugin_id] = version
        self.installed_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _fetch(self, url: str) -> bytes:
        r = self.mgr.client.get(url, timeout=30, detect_challenges=False)
        if r.status_code != 200:
            raise OSError(f"HTTP {r.status_code} for {url}")
        return r.content

    def update_templates(self) -> dict:
        url = getattr(self.settings, "plugin_index_url", "")
        if not url:
            raise ValueError("set a template index address first")
        from ..settings import user_plugins_dir
        installed = self.installed_versions()
        index = json.loads(self._fetch(url).decode("utf-8"))
        plan = upd.plan_updates(installed, index)
        result = upd.check_and_update(installed, url, self._fetch, user_plugins_dir())
        for entry in plan:
            if entry["id"] in result["updated"]:
                self._remember(entry["id"], entry["version"])
        if result["updated"]:
            self.mgr.registry.scan(force=True)
        self.last_update = dict(result, checked_at=time.time())
        return self.last_update

    def maybe_auto_update(self) -> None:
        if not getattr(self.settings, "plugin_auto_update", False) or not self.settings.plugin_index_url:
            return
        try:
            self.update_templates()
        except Exception as exc:
            log.info("automatic template update failed: %s", exc)

    # -- marketplace -----------------------------------------------------------------
    def catalog(self) -> list:
        url = getattr(self.settings, "marketplace_url", "")
        if not url:
            raise ValueError("set a marketplace catalog address first")
        return mkt.parse_catalog(json.loads(self._fetch(url).decode("utf-8")))

    def verifier(self):
        if not getattr(self.settings, "marketplace_require_signature", True):
            return None
        keys = [k for k in (getattr(self.settings, "marketplace_keys", None) or []) if k.strip()]
        if not keys:
            return lambda listing, data: False  # nothing trusted yet
        return lambda listing, data: verify_signature(listing.signature, data, keys)

    def install(self, listing_id: str) -> dict:
        from ..settings import user_plugins_dir
        listing = next((l for l in self.catalog() if l.id == listing_id), None)
        if listing is None:
            raise KeyError(listing_id)
        data = self._fetch(listing.url)
        try:
            mkt.verify_download(listing, data, self.verifier())
        except mkt.MarketplaceError as exc:
            if "signature" in str(exc) and not (self.settings.marketplace_keys or []):
                raise mkt.MarketplaceError(str(exc) + ": add the publisher's key in Settings first") from exc
            raise
        dest = user_plugins_dir()
        if listing.url.lower().split("?")[0].endswith(".json"):
            entry = {"id": listing.id, "sha256": listing.sha256}
            path = upd.apply_update(entry, data, dest)
        else:
            if not upd.ID_RE.match(listing.id):
                raise ValueError("bad plugin id")
            path = dest / f"{listing.id}.py"
            path.write_bytes(data)
        self._remember(listing.id, listing.version)
        # installing from the catalog approves exactly what the listing declared
        self.grant(listing.id, listing.permissions)
        self.mgr.registry.scan(force=True)
        return {"installed": listing.id, "version": listing.version, "path": str(path),
                "permissions": listing.permissions.to_dict()}


def verify_signature(signature_b64: str, data: bytes, public_keys: list[str]) -> bool:
    """Ed25519 signature check against any trusted key (PEM or 64-char hex)."""
    from Cryptodome.PublicKey import ECC
    from Cryptodome.Signature import eddsa
    try:
        sig = base64.b64decode(signature_b64 or "", validate=True)
    except (ValueError, TypeError):
        return False
    for key in public_keys:
        try:
            k = key.strip()
            pub = ECC.import_key(k) if "BEGIN" in k else eddsa.import_public_key(bytes.fromhex(k))
            eddsa.new(pub, "rfc8032").verify(data, sig)
            return True
        except (ValueError, TypeError):
            continue
    return False


def validate_template_text(text: str) -> list[str]:
    try:
        tpl = json.loads(text)
    except ValueError as exc:
        return [f"not valid JSON: {exc}"]
    return validate_template(tpl)
