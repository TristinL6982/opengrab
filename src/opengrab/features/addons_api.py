"""Routes for optional add-ons (B22): list, install, remove, hardware profile."""
from __future__ import annotations

from .. import addons, hardware


def setup(srv):
    r = srv.router

    def list_addons(req):
        return {"addons": [addons.status(n) for n in addons.ADDONS],
                "hardware": hardware.profile()}

    def install(req):
        name = req.params["name"]
        if name not in addons.ADDONS:
            return 404, {"error": "unknown add-on"}

        def done(n, ok, err):
            srv.broadcast("addon", {"name": n, "ok": ok, "error": err})
            notify = srv.features.get("notify")
            if notify is not None:
                title = "Add-on installed" if ok else "Add-on install failed"
                notify.add("info" if ok else "failed", title,
                           addons.ADDONS[n]["title"] + ("" if ok else ": " + err))

        return 202, {"job": addons.install_async(name, on_done=done)}

    def remove(req):
        name = req.params["name"]
        if name not in addons.ADDONS:
            return 404, {"error": "unknown add-on"}
        return {"removed": addons.remove(name)}

    r.get(r"/api/addons", list_addons)
    r.post(r"/api/addons/(?P<name>[a-z0-9_-]+)/install", install, action="settings_write")
    r.post(r"/api/addons/(?P<name>[a-z0-9_-]+)/remove", remove, action="settings_write")
    return None
