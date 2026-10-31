"""W12 routes: plugin permissions, template validation/updates, marketplace."""
from __future__ import annotations

from ..engine.plugin_policy import validate_template_text
from ..plugins import marketplace as mkt
from ..plugins.permissions import parse_permissions


def setup(srv):
    mgr = srv.manager
    pol = mgr.plugin_policy
    r = srv.router

    r.get(r"/api/plugins/permissions", lambda _req: {"plugins": pol.overview(),
                                                      "sandbox": mgr.settings.plugin_sandbox_enabled})

    def grant(req):
        pid = req.params["pid"]
        p = mgr.registry.get(pid)
        if p is None:
            return 404, {"error": "no such plugin"}
        raw = req.body.get("permissions")
        try:
            perms = parse_permissions(raw) if isinstance(raw, dict) else pol.requested(p)
        except ValueError as exc:
            return 400, {"error": str(exc)}
        pol.grant(pid, perms)
        return {"granted": perms.to_dict()}

    def revoke(req):
        return (200, {"ok": True}) if pol.revoke(req.params["pid"]) else (404, {"error": "nothing granted"})

    r.post(r"/api/plugins/(?P<pid>[A-Za-z0-9_.-]+)/permissions", grant, action="settings_write")
    r.delete(r"/api/plugins/(?P<pid>[A-Za-z0-9_.-]+)/permissions", revoke, action="settings_write")

    def validate(req):
        text = req.body.get("template")
        if isinstance(text, dict):
            import json
            text = json.dumps(text)
        errors = validate_template_text(str(text or ""))
        return {"ok": not errors, "errors": errors}

    r.post(r"/api/plugins/validate", validate)

    def update(_req):
        try:
            return pol.update_templates()
        except (OSError, ValueError) as exc:
            return 400, {"error": str(exc)}

    r.post(r"/api/plugins/update", update, action="settings_write")

    def catalog(req):
        if not getattr(mgr.settings, "marketplace_url", ""):
            # not an error: nothing is set up yet, so say how instead of failing
            return {"listings": [], "configured": False,
                    "note": "No plugin catalog is set up. Add its address in Settings, Plugins, Marketplace catalog."}
        try:
            listings = pol.catalog()
        except (OSError, ValueError, mkt.MarketplaceError) as exc:
            return 400, {"error": str(exc)}
        installed = pol.installed_versions()
        found = mkt.search(listings, req.q("q"), req.q("sort", "rating") or "rating")
        updates = {l.id for l in mkt.updates_available(listings, installed)}
        return {"listings": [{"id": l.id, "name": l.name, "version": l.version, "author": l.author,
                              "description": l.description, "rating": l.rating,
                              "ratings_count": l.ratings_count, "signed": bool(l.signature),
                              "permissions": l.permissions.to_dict(),
                              "installed": installed.get(l.id), "update": l.id in updates}
                             for l in found]}

    def install(req):
        try:
            return 201, pol.install(str(req.body.get("id") or ""))
        except KeyError:
            return 404, {"error": "not in the catalog"}
        except (OSError, ValueError, mkt.MarketplaceError) as exc:
            return 400, {"error": str(exc)}

    r.get(r"/api/marketplace", catalog)
    r.post(r"/api/marketplace/install", install, action="settings_write")
    return pol
