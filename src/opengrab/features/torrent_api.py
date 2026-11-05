"""W11 routes: torrents, trackers, port mapping, router reconnect, network config."""
from __future__ import annotations

import time

from .. import netconfig
from ..engine.torrents import NOT_INSTALLED, libtorrent_available


def setup(srv):
    mgr = srv.manager
    ts = mgr.torrents
    r = srv.router

    def torrents(_req):
        out = []
        for l in mgr.all_links():
            if l.plugin == "torrent":
                t = dict(l.extra.get("torrent") or {})
                t.update(id=l.id, title=l.title, status=l.status.value, progress=l.progress)
                out.append(t)
        return {"installed": libtorrent_available(), "session": ts.session is not None,
                "listen_port": ts.session.listen_port if ts.session else None,
                "torrents": out, "hint": "" if libtorrent_available() else NOT_INSTALLED}

    def files(req):
        if mgr.get(req.params["lid"]) is None:
            return 404, {"error": "link not found"}
        return {"files": ts.files(req.params["lid"])}

    def select(req):
        sel = [int(i) for i in (req.body.get("selected") or [])]
        try:
            prios = ts.select_files(req.params["lid"], sel)
        except (ValueError, StopIteration) as exc:
            return 409, {"error": str(exc) or "the file list is not known yet"}
        return {"priorities": prios}

    def peers(req):
        from .. import geoip
        if mgr.get(req.params["lid"]) is None:
            return 404, {"error": "link not found"}
        geo = geoip.load_db()
        out = ts.peers(req.params["lid"], geo)
        if out is None:
            return 409, {"error": "this torrent is not running"}
        out["geo"] = {"installed": geo is not None, "attribution": geoip.ATTRIBUTION if geo else "",
                      "attribution_url": geoip.ATTRIBUTION_URL if geo else ""}
        return out

    def geo_status(_req):
        from .. import geoip
        return geoip.status()

    def geo_install(_req):
        from .. import geoip

        def done(ok, err):
            srv.broadcast("addon", {"name": "geoip", "ok": ok, "error": err})
        return 202, {"job": geoip.install_async(on_done=done)}

    def geo_remove(_req):
        from .. import geoip
        return {"removed": geoip.remove()}

    r.get(r"/api/torrents", torrents)
    r.get(r"/api/torrents/(?P<lid>[^/]+)/peers", peers)
    r.get(r"/api/geoip", geo_status)
    r.post(r"/api/geoip/install", geo_install, action="settings_write")
    r.post(r"/api/geoip/remove", geo_remove, action="settings_write")
    r.get(r"/api/torrents/(?P<lid>[^/]+)/files", files)
    r.post(r"/api/torrents/(?P<lid>[^/]+)/files", select)

    # -- trackers -----------------------------------------------------------
    def trackers(_req):
        return {"trackers": [dict(ts.trackers.health(u).to_dict(), healthy=ts.trackers.is_healthy(u))
                             for u in ts.trackers.urls()],
                "last_update": ts.trackers.last_update}

    def add_trackers(req):
        text = str(req.body.get("text") or req.body.get("url") or "")
        added = ts.trackers.merge(text, now=time.time()) if "\n" in text or " " in text \
            else int(ts.trackers.add(text))
        ts.trackers_save()
        mgr.settings.save()
        return {"added": added}

    def remove_tracker(req):
        ok = ts.trackers.remove(req.q("url"))
        ts.trackers_save()
        mgr.settings.save()
        return (200, {"ok": True}) if ok else (404, {"error": "tracker not in the list"})

    def update_trackers(_req):
        url = mgr.settings.tracker_list_url
        if not url:
            return 400, {"error": "set a public tracker list address first"}
        resp = mgr.client.get(url, timeout=20, detect_challenges=False)
        if resp.status_code != 200:
            return 502, {"error": f"tracker list answered HTTP {resp.status_code}"}
        added = ts.trackers.merge(resp.text, now=time.time())
        ts.trackers_save()
        mgr.settings.save()
        return {"added": added, "total": len(ts.trackers.urls())}

    r.get(r"/api/trackers", trackers)
    r.post(r"/api/trackers", add_trackers, action="settings_write")
    r.delete(r"/api/trackers", remove_tracker, action="settings_write")
    r.post(r"/api/trackers/update", update_trackers, action="settings_write")

    # -- port mapping -----------------------------------------------------------
    def portmap_status(_req):
        res = ts.portmap_result
        return {"session": ts.session is not None,
                "listen_port": ts.session.listen_port if ts.session else None,
                "result": None if res is None else {"ok": res.ok, "method": res.method,
                                                    "port": res.external_port, "errors": res.errors}}

    r.get(r"/api/portmap", portmap_status)
    r.post(r"/api/portmap/test", lambda _req: ts.map_port(), action="settings_write")

    # -- router reconnect ------------------------------------------------------------
    def reconnect_status(_req):
        rc = mgr.reconnector()
        hist = [] if rc is None else [h.__dict__ for h in rc.history[-20:]]
        return {"enabled": bool(rc and rc.config.enabled), "history": hist}

    def reconnect_now(_req):
        rc = mgr.reconnector()
        if rc is None:
            return 400, {"error": "the reconnect settings are invalid"}
        if not (mgr.settings.reconnect or {}).get("command"):
            return 400, {"error": "set the command that resets your router first"}
        res = rc.reconnect()
        return {"ok": res.ok, "old_ip": res.old_ip, "new_ip": res.new_ip,
                "attempts": res.attempts, "error": res.error}

    r.get(r"/api/reconnect", reconnect_status)
    r.post(r"/api/reconnect", reconnect_now, action="settings_write")

    # -- every network section, validated by the M11r glue ---------------------------
    def network(_req):
        s = mgr.settings
        cfg = netconfig.load_network_config({
            "reconnect": s.reconnect or None, "retry": s.retry_policy or None,
            "torrent": s.torrent or None, "portmap": s.portmap or None,
            "avscan": s.avscan or None, "categories": s.categories or None})
        return {"errors": cfg.errors, "sections": list(netconfig.SECTIONS)}

    r.get(r"/api/network", network)
    srv.settings_hooks.append(lambda applied: [f"network section {e!r} is invalid and uses defaults"
                                               for e in network(None)["errors"]])
    return ts
