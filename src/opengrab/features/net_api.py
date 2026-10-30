"""W7 routes: server/proxy logins and a proxy test."""
from __future__ import annotations

import time

from ..proxies import parse_proxy
from ..siteauth import SCHEMES


def setup(srv):
    mgr = srv.manager
    net = mgr.net
    r = srv.router

    r.get(r"/api/site-logins", lambda _req: {"logins": net.logins(), "schemes": list(SCHEMES)})

    def add_login(req):
        b = req.body
        try:
            net.creds.add(str(b.get("pattern") or ""), str(b.get("username") or ""),
                          str(b.get("password") or ""), scheme=str(b.get("scheme") or "basic"),
                          for_proxy=bool(b.get("for_proxy")))
        except ValueError as exc:
            return 400, {"error": str(exc)}
        net.save_credentials()
        return 201, {"logins": net.logins()}

    def remove_login(req):
        removed = net.creds.remove(req.q("pattern"), for_proxy=req.q("proxy") in ("1", "true"))
        if not removed:
            return 404, {"error": "no such login"}
        net.save_credentials()
        return {"logins": net.logins()}

    r.post(r"/api/site-logins", add_login, action="settings_write")
    r.delete(r"/api/site-logins", remove_login, action="settings_write")

    def test_proxy(req):
        text = str(req.body.get("proxy") or "")
        target = str(req.body.get("url") or "https://www.example.com/")
        if not target.lower().startswith(("http://", "https://")):
            return 400, {"error": "url must be http(s)"}
        try:
            proxy = parse_proxy(text)
        except ValueError as exc:
            return 400, {"error": str(exc)}
        from ..engine.downloader import _open_session
        kw = net.session_kwargs(target, proxy)
        sess = _open_session(mgr.settings, kw)
        t0 = time.monotonic()
        try:
            resp = sess.get(target, timeout=15, allow_redirects=True)
            return {"ok": resp.status_code < 500, "status": resp.status_code,
                    "ms": int((time.monotonic() - t0) * 1000)}
        except Exception as exc:  # unreachable, refused, auth required …
            return {"ok": False, "error": str(exc)[:300]}
        finally:
            try:
                sess.close()
            except Exception:
                pass

    r.post(r"/api/proxies/test", test_proxy, action="settings_write")
    r.get(r"/api/proxies", lambda _req: {
        "proxies": [p.url.split("@")[-1] for p in net.proxy_list.proxies],
        "failed": [f"{p.host}:{p.port}" for p in net.proxy_list._failed],
        "errors": list(net.errors)})
    return net
