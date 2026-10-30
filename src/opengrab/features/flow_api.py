"""W3-W5 routes: speed profiles, data cap, queues, schedule status, checksums."""
from __future__ import annotations

from ..queues import DEFAULT_QUEUE


def setup(srv):
    mgr = srv.manager
    flow = mgr.flow
    r = srv.router

    def save():
        try:
            mgr.settings.save()
        except Exception:
            pass

    def status(_req):
        st = flow.status()
        st["categories_enabled"] = bool(mgr.settings.categories_enabled)
        st["categories"] = flow.categories.to_list()
        st["max_per_host"] = mgr.settings.max_per_host
        st["per_host_limits"] = dict(mgr.settings.per_host_limits)
        return st

    r.get(r"/api/flow", status)

    def switch_profile(req):
        name = str(req.body.get("name") or "")
        try:
            prof = flow.switch_profile(name)
        except KeyError:
            return 404, {"error": f"no speed profile named {name!r}"}
        save()
        mgr._global_limiter.set_rate(flow.effective_limit_bps())
        srv.broadcast("flow", status(None))
        return {"active_profile": prof.name, "effective_limit_bps": flow.effective_limit_bps()}

    r.post(r"/api/speed/profile", switch_profile)

    # -- queues ---------------------------------------------------------------
    def store_queues():
        mgr.settings.queues = {"queues": [dict(q.to_dict(), items=[])
                                          for q in flow.queues.queues.values()]}
        save()
        srv.broadcast("flow", status(None))

    def create_queue(req):
        try:
            q = flow.queues.create(str(req.body.get("name") or ""),
                                   int(req.body.get("max_active", 1) or 1))
        except ValueError as exc:
            return 400, {"error": str(exc)}
        store_queues()
        return 201, q.to_dict()

    def update_queue(req):
        name = req.params["name"]
        try:
            q = flow.queues.get(name)
            if "max_active" in req.body:
                flow.queues.set_limit(q.name, int(req.body["max_active"]))
            if "paused" in req.body:
                flow.queues.pause(q.name, bool(req.body["paused"]))
            new = str(req.body.get("rename") or "").strip()
            if new and new != q.name:
                old = q.name
                q = flow.queues.rename(old, new)
                for l in mgr.all_links():
                    if l.extra.get("queue") == old:
                        l.extra["queue"] = q.name
        except KeyError:
            return 404, {"error": f"no queue named {name!r}"}
        except ValueError as exc:
            return 400, {"error": str(exc)}
        store_queues()
        return q.to_dict()

    def delete_queue(req):
        name = req.params["name"]
        try:
            q = flow.queues.get(name)
            flow.queues.remove(q.name)
        except KeyError:
            return 404, {"error": f"no queue named {name!r}"}
        except ValueError as exc:
            return 400, {"error": str(exc)}
        moved = 0
        for l in mgr.all_links():
            if l.extra.get("queue") == q.name:
                l.extra.pop("queue", None)
                moved += 1
        mgr._persist()
        store_queues()
        return {"ok": True, "moved_to_main": moved}

    r.post(r"/api/queues", create_queue)
    r.post(r"/api/queues/(?P<name>[^/]+)", update_queue)
    r.delete(r"/api/queues/(?P<name>[^/]+)", delete_queue)

    def move(req):
        link = mgr.get(req.params["lid"])
        if link is None:
            return 404, {"error": "link not found"}
        name = str(req.body.get("queue") or DEFAULT_QUEUE)
        try:
            q = flow.queues.get(name)
        except KeyError:
            return 404, {"error": f"no queue named {name!r}"}
        if q.name == DEFAULT_QUEUE:
            link.extra.pop("queue", None)
        else:
            link.extra["queue"] = q.name
        link.extra.pop("wait_reason", None)
        mgr._persist()
        mgr._broadcast("grabbed", link)
        return {"ok": True, "queue": q.name}

    r.post(r"/api/links/(?P<lid>[^/]+)/move", move)

    # -- checksums ------------------------------------------------------------
    def verify(req):
        try:
            return mgr.verify_now(req.params["lid"], str(req.body.get("expected") or ""))
        except FileNotFoundError:
            return 404, {"error": "the downloaded file is gone"}

    r.post(r"/api/links/(?P<lid>[^/]+)/verify", verify)

    srv.settings_hooks.append(lambda applied: mgr.settings_changed())
    return flow
