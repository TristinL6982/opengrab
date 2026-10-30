"""M14 routes: Windows link handlers, file types and autostart; opening
files and opengrab:// links handed over by Windows or a second launch.

GET  /api/system/integration   what is registered for this user
POST /api/system/integration   {protocols:{magnet:true}, files:{".torrent":true}, autostart:true}
POST /api/open                 {targets:[url | opengrab://... | local file path]}

Both POST routes change this computer or read its files, so they need the
master token from this computer (never a paired device or the LAN).
"""
from __future__ import annotations

from pathlib import Path

from .. import winintegration as wi
from ..remote import is_loopback

OPEN_EXTS = (".torrent", ".crawljob", ".dlc", ".ccf", ".rsdf", ".txt")


def _local(req) -> bool:
    h = req.handler
    if h is None:
        return False
    return is_loopback(req.srv.access.client_ip(h.client_address[0], h.headers))


def open_targets(srv, targets) -> dict:
    mgr = srv.manager
    out = {"links": 0, "files": 0, "errors": []}
    auto = srv.features.get("automation")
    for raw in targets or []:
        t = wi.normalize_target(str(raw))
        if not t:
            continue
        p = Path(t)
        if not wi.looks_like_url(t) and p.suffix.lower() in OPEN_EXTS:
            if not p.is_file():
                out["errors"].append(f"{p.name}: file not found")
                continue
            try:
                if p.suffix.lower() == ".torrent":
                    mgr.add_torrent_file(str(p), title=p.stem)
                    out["links"] += 1
                elif auto is not None:
                    out["links"] += auto._import(p, "linkgrabber")
                else:
                    raise ValueError("automation is not running")
                out["files"] += 1
            except Exception as exc:  # DLC/CCF raise ContainerError
                out["errors"].append(f"{p.name}: {exc}")
            continue
        try:
            mgr.add_url(t)
            out["links"] += 1
        except ValueError as exc:
            out["errors"].append(f"{t[:80]}: {exc}")
    return out


def setup(srv):
    r = srv.router

    def status(_req):
        if not wi.available():
            return {"available": False}
        return {"available": True, **wi.Registry().status()}

    def change(req):
        if not wi.available():
            return 400, {"error": "Windows integration is only available on Windows"}
        if not _local(req):
            return 403, {"error": "change link handlers from this computer"}
        reg = wi.Registry()
        body = req.body or {}
        for scheme, on in (body.get("protocols") or {}).items():
            if scheme in wi.PROTOCOLS:
                reg.set_protocol(scheme, bool(on))
        for ext, on in (body.get("files") or {}).items():
            if ext in wi.FILE_TYPES:
                reg.set_file_type(ext, bool(on))
        if "autostart" in body:
            reg.set_autostart(bool(body["autostart"]))
        return {"available": True, **reg.status()}

    def open_(req):
        if not _local(req):
            return 403, {"error": "files can only be opened from this computer"}
        targets = (req.body or {}).get("targets") or []
        if not isinstance(targets, list):
            return 400, {"error": "targets must be a list"}
        return open_targets(srv, targets[:500])

    r.get(r"/api/system/integration", status)
    r.post(r"/api/system/integration", change, action="settings_write")
    r.post(r"/api/open", open_, action="settings_write")
    return None
