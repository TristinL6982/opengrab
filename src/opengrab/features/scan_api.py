"""W10 routes: virus scan history and a scanner test."""
from __future__ import annotations

import os
import tempfile


def setup(srv):
    mgr = srv.manager
    vs = mgr.virus_scan
    r = srv.router

    def status(_req):
        from ..avscan import find_defender
        cfg = vs.config()
        return {"enabled": cfg.enabled, "scanner": cfg.scanner, "virustotal": vs.vt_enabled(),
                "defender": find_defender(os.path.exists) or "",
                "history": list(reversed(vs.history[-50:]))}

    def test(_req):
        """Scan a harmless temporary file with the current settings."""
        fd, path = tempfile.mkstemp(prefix="opengrab-scan-test-", suffix=".txt")
        with os.fdopen(fd, "w") as fh:
            fh.write("OpenGrab virus scanner test file. Harmless.\n")
        try:
            cfg = vs.config()
            if not cfg.enabled:
                return 400, {"error": "turn on virus scanning first"}
            res = vs.scan(path)
            return {"status": res.status, "code": res.code, "detail": res.detail[-300:]}
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def vt_link(req):
        """Check one finished download on VirusTotal now (also when the
        automatic check is off). Runs in the background; the row updates."""
        link = mgr.get(req.params["lid"])
        if link is None or not link.path or not os.path.isfile(link.path):
            return 404, {"error": "the download has no file to check"}
        if not vs.vt_config().api_key:
            return 400, {"error": "add your VirusTotal API key in Settings, Virus scan"}
        link.extra["virustotal"] = {"status": "checking", "detail": "checking on VirusTotal"}
        mgr._broadcast("grabbed", link)

        def run():
            res = vs.vt_check(link.path)
            link.extra["virustotal"] = res.to_dict()
            if res.flagged:
                mgr.on_change("virus", link.to_public())
            mgr._persist()
            mgr._broadcast("grabbed", link)
        mgr._spawn(run)
        return 202, {"ok": True}

    r.get(r"/api/avscan", status)
    r.post(r"/api/links/(?P<lid>[^/]+)/virustotal", vt_link, action="settings_write")
    r.post(r"/api/avscan/test", test, action="settings_write")
    return vs
