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
        return {"enabled": cfg.enabled, "scanner": cfg.scanner,
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

    r.get(r"/api/avscan", status)
    r.post(r"/api/avscan/test", test, action="settings_write")
    return vs
